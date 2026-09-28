"""
Daily KPI insight writer.

Reads Agent_Targets and Ticket_KPI_Daily from SharePoint (Microsoft Graph,
app-only auth via the Freshdesk-KPI-Insight Entra app, Sites.Selected write
access to this one site), computes each agent's standing for the latest
tracked day, asks Claude to turn those facts into a 2-4 sentence insight, and
writes it to the Daily_Summary list (one row per date, overwritten on re-run).

Python does all the counting. Claude only writes sentences from the computed
facts, so it never has to count or infer a number.

Run it after Flow B's 6pm ET check so BelowTarget is final for the day. Not
scheduled (Windows Task Scheduler's automatic triggers are unreliable in this
environment, confirmed 2026-07-26):

    python scripts/daily_insight.py
    python scripts/daily_insight.py --date 2026-09-27

Setup (one time):
    1. pip install anthropic
    2. scripts/.env needs MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET,
       ANTHROPIC_API_KEY and SHAREPOINT_HOSTNAME (see .env.example).
    3. .env is gitignored -- never commit it, never paste a value into chat.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import anthropic

MODEL = "claude-opus-5"

AGENT_TARGETS_LIST = "Agent_Targets"
TICKET_LIST = "Ticket_KPI_Daily"
SUMMARY_LIST = "Daily_Summary"

REQUIRED_SETTINGS = [
    "MS_TENANT_ID",
    "MS_CLIENT_ID",
    "MS_CLIENT_SECRET",
    "ANTHROPIC_API_KEY",
    "SHAREPOINT_HOSTNAME",
]

SYSTEM_PROMPT = """You write the Daily Insight line for a support-team KPI dashboard. A supervisor
reads it before looking at any chart.

Rules:
- Use only the facts in the JSON you are given. Never invent a number, a name,
  or a cause. If the data doesn't explain why something happened, don't guess.
- 2 to 4 sentences, plain text, no markdown, no bullet points.
- Lead with what needs the supervisor's attention today: who is below target and
  for how many days in a row. If everyone is at or above target, say so first.
- Mention a target change only if one happened today.
- No em dashes. No filler words like "essentially" or "notably". Don't restate
  a point once it's made."""


def load_settings():
    settings = {}
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                settings[key.strip()] = value.strip()
    for name in REQUIRED_SETTINGS:
        if not settings.get(name):
            settings[name] = os.environ.get(name, "")
    missing = [name for name in REQUIRED_SETTINGS if not settings[name]]
    if missing:
        sys.exit(
            f"Missing in scripts/.env: {', '.join(missing)}. "
            "See scripts/.env.example for the full list."
        )
    return settings


# ---------- Microsoft Graph ----------

def http_json(url, method="GET", token=None, body=None, form=None):
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    elif form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        sys.exit(f"{method} {url.split('?')[0]} failed ({e.code}): {detail}")


def get_graph_token(settings):
    url = f"https://login.microsoftonline.com/{settings['MS_TENANT_ID']}/oauth2/v2.0/token"
    result = http_json(url, method="POST", form={
        "client_id": settings["MS_CLIENT_ID"],
        "client_secret": settings["MS_CLIENT_SECRET"],
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    })
    return result["access_token"]


def get_site_id(token, hostname):
    return http_json(f"https://graph.microsoft.com/v1.0/sites/{hostname}:/", token=token)["id"]


def list_items(token, site_id, list_name, fields):
    url = (
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/lists/{list_name}/items"
        f"?expand=fields(select={','.join(fields)})&$top=200"
    )
    items = []
    while url:
        page = http_json(url, token=token)
        items.extend(page.get("value", []))
        url = page.get("@odata.nextLink")
    return items


# ---------- KPI facts ----------

def sp_date(value):
    # SharePoint stores date-only columns as local midnight in UTC
    # (e.g. 2026-09-22T07:00:00Z for Sep 22 Pacific), so the date part is the day.
    return value[:10] if value else None


def whole(value):
    # Graph returns SharePoint number columns as floats (4.0); counts and targets are whole.
    return int(value) if value is not None else None


def build_facts(target_items, ticket_items, report_date):
    current_targets = {
        i["fields"]["Title"]: whole(i["fields"].get("DailyTicketTarget")) for i in target_items
    }
    agent_names = sorted(current_targets)

    rows = {}  # (date, agent) -> {"tickets": n, "target": n}
    for item in ticket_items:
        f = item["fields"]
        day = sp_date(f.get("Date"))
        agent = f.get("AgentName")
        if not day or not agent:
            continue
        rows[(day, agent)] = {
            "tickets": whole(f.get("TicketCount")) or 0,
            "target": whole(f.get("Target")),
        }

    tracked_days = sorted({day for day, _ in rows if day <= report_date})
    if not tracked_days or tracked_days[-1] != report_date:
        sys.exit(f"No Ticket_KPI_Daily rows for {report_date}. Nothing to summarize.")

    def standing(day, agent):
        row = rows.get((day, agent))
        target = row["target"] if row and row["target"] is not None else current_targets[agent]
        tickets = row["tickets"] if row else 0
        return {"tickets": tickets, "target": target, "below": tickets < target}

    previous_day = tracked_days[-2] if len(tracked_days) > 1 else None

    agents = []
    for agent in agent_names:
        today = standing(report_date, agent)
        streak = 0
        for day in reversed(tracked_days):
            if not standing(day, agent)["below"]:
                break
            streak += 1
        yesterday = standing(previous_day, agent) if previous_day else None
        agents.append({
            "name": agent,
            "tickets": today["tickets"],
            "target": today["target"],
            "below": today["below"],
            "days_below_in_a_row": streak,
            "yesterday": (
                {"tickets": yesterday["tickets"], "target": yesterday["target"]}
                if yesterday else None
            ),
            "target_changed_today": bool(yesterday) and yesterday["target"] != today["target"],
        })

    last_day_all_at_target = next(
        (
            day for day in reversed(tracked_days)
            if not any(standing(day, agent)["below"] for agent in agent_names)
        ),
        None,
    )

    return {
        "date": report_date,
        "team": {
            "tickets": sum(a["tickets"] for a in agents),
            "target": sum(a["target"] for a in agents),
            "agents_below": sum(1 for a in agents if a["below"]),
            "agents_total": len(agents),
        },
        "agents": agents,
        "last_day_all_at_target": last_day_all_at_target,
    }


# ---------- Claude ----------

def write_insight(settings, facts):
    client = anthropic.Anthropic(api_key=settings["ANTHROPIC_API_KEY"])
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(facts, indent=2)}],
    )
    if response.stop_reason == "refusal":
        sys.exit(f"Claude declined the request ({response.stop_details}). Nothing written.")
    text = " ".join(b.text.strip() for b in response.content if b.type == "text").strip()
    if not text:
        sys.exit(f"Claude returned no text (stop_reason: {response.stop_reason}). Nothing written.")
    return text


# ---------- Daily_Summary ----------

def upsert_summary(token, site_id, report_date, text):
    existing = [
        i for i in list_items(token, site_id, SUMMARY_LIST, ["Title"])
        if i["fields"].get("Title") == report_date
    ]
    # Noon UTC keeps the date-only column on the same calendar day in the
    # site's US time zone (midnight UTC would land on the previous evening).
    fields = {"Title": report_date, "Date": f"{report_date}T12:00:00Z", "SummaryText": text}
    base = f"https://graph.microsoft.com/v1.0/sites/{site_id}/lists/{SUMMARY_LIST}/items"
    if existing:
        http_json(f"{base}/{existing[0]['id']}/fields", method="PATCH", token=token, body=fields)
        return "updated"
    http_json(base, method="POST", token=token, body={"fields": fields})
    return "created"


def main():
    parser = argparse.ArgumentParser(description="Write today's Daily Insight to the Daily_Summary list.")
    parser.add_argument("--date", help="Day to summarize, YYYY-MM-DD (default: latest day in Ticket_KPI_Daily).")
    args = parser.parse_args()

    settings = load_settings()
    token = get_graph_token(settings)
    site_id = get_site_id(token, settings["SHAREPOINT_HOSTNAME"])

    target_items = list_items(token, site_id, AGENT_TARGETS_LIST, ["Title", "DailyTicketTarget"])
    ticket_items = list_items(
        token, site_id, TICKET_LIST, ["AgentName", "Date", "TicketCount", "Target"]
    )

    report_date = args.date or max(
        sp_date(i["fields"].get("Date")) for i in ticket_items if i["fields"].get("Date")
    )
    facts = build_facts(target_items, ticket_items, report_date)
    print(json.dumps(facts, indent=2))

    text = write_insight(settings, facts)
    action = upsert_summary(token, site_id, report_date, text)
    print(f"\n{report_date} ({action} in {SUMMARY_LIST}):\n{text}")


if __name__ == "__main__":
    main()
