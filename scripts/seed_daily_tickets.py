"""
Freshdesk daily ticket seeder.

Creates a handful of realistic financial-services support tickets against the
Freshdesk trial account, spread across the 3 seeded agents. Run once a day for
3-5 real days (Session 2's real-volume requirement) rather than in one batch,
so ticket timestamps land on different real dates.

Windows Task Scheduler's automatic triggers are not reliable in this
environment (confirmed 2026-07-26), so this is not scheduled -- run it
yourself each day:

    python scripts/seed_daily_tickets.py
    python scripts/seed_daily_tickets.py --count 8

Setup (one time):
    1. Copy .env.example to .env in this scripts/ folder.
    2. Fill in FRESHDESK_API_KEY (from Freshdesk > Profile Settings > View API Key).
    3. .env is gitignored -- never commit it, never paste the key into chat.
"""

import argparse
import base64
import os
import random
import sys
import time
import urllib.request
import urllib.error
import json

FRESHDESK_DOMAIN = "visualdreamland.freshdesk.com"

AGENTS = [
    {"name": "Sawandi Kirby", "id": 39004116283},
    {"name": "Maria Torres", "id": 39004217559},
    {"name": "James Okafor", "id": 39004217578},
]

DEFAULT_TICKETS_PER_RUN = 5

# (subject, description, type) -- type must be one of this Freshdesk account's
# actual configured values: Question, Incident, Problem, Feature Request, Refund
TICKET_TEMPLATES = [
    ("Unauthorized charge on checking account", "Customer reports a charge they did not make on their checking account and wants it investigated.", "Incident"),
    ("Wire transfer stuck in pending", "Customer's outbound wire transfer has been in pending status for over 24 hours.", "Incident"),
    ("Debit card declined at point of sale", "Customer's debit card was declined despite having sufficient funds.", "Problem"),
    ("Password reset not receiving email", "Customer requested a password reset but has not received the confirmation email.", "Incident"),
    ("Suspicious login alert follow-up", "Customer received a suspicious login alert and wants to confirm their account is secure.", "Incident"),
    ("Statement discrepancy on monthly report", "Customer noticed a discrepancy between their monthly statement and their own records.", "Question"),
    ("Mobile app crashing on transfer screen", "Customer's mobile banking app crashes every time they try to open the transfer screen.", "Problem"),
    ("Account locked after failed login attempts", "Customer's account was locked after multiple failed login attempts and needs it unlocked.", "Incident"),
    ("Dispute on recurring subscription charge", "Customer wants to dispute a recurring subscription charge they no longer recognize.", "Refund"),
    ("Request for updated card due to expiration", "Customer's debit card is expiring soon and they are requesting a replacement.", "Question"),
]

PRIORITIES = [1, 1, 2, 2, 2, 3, 4]  # weighted toward Low/Medium, a few High/Urgent
STATUSES = [2, 2, 2, 3, 3, 4]        # weighted toward Open, some Pending/Resolved


def load_api_key():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == "FRESHDESK_API_KEY":
                    return value.strip()
    key = os.environ.get("FRESHDESK_API_KEY")
    if not key:
        sys.exit(
            "FRESHDESK_API_KEY not found. Copy scripts/.env.example to scripts/.env "
            "and fill in your API key, or set the FRESHDESK_API_KEY environment variable."
        )
    return key


def create_ticket(api_key, subject, description, ticket_type, priority, status, responder_id, requester_email):
    url = f"https://{FRESHDESK_DOMAIN}/api/v2/tickets"
    payload = {
        "subject": subject,
        "description": description,
        "email": requester_email,
        "priority": priority,
        "status": status,
        "type": ticket_type,
        "responder_id": responder_id,
    }
    body = json.dumps(payload).encode("utf-8")
    auth = base64.b64encode(f"{api_key}:X".encode("utf-8")).decode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("id")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"  FAILED ({e.code}): {detail}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Seed a handful of realistic Freshdesk tickets for today.")
    parser.add_argument("--count", type=int, default=DEFAULT_TICKETS_PER_RUN, help="Number of tickets to create (default 5).")
    args = parser.parse_args()

    api_key = load_api_key()
    created = []

    for i in range(args.count):
        subject, description, ticket_type = random.choice(TICKET_TEMPLATES)
        agent = AGENTS[i % len(AGENTS)]  # round-robin across the 3 agents
        priority = random.choice(PRIORITIES)
        status = random.choice(STATUSES)
        requester_email = f"customer.{random.randint(1000, 9999)}@example.com"

        ticket_id = create_ticket(
            api_key, subject, description, ticket_type,
            priority, status, agent["id"], requester_email,
        )
        if ticket_id:
            created.append(ticket_id)
            print(f"  Created ticket #{ticket_id}: \"{subject}\" -> {agent['name']}")
        time.sleep(0.5)  # stay polite to Freshdesk's rate limit

    print(f"\n{len(created)}/{args.count} tickets created for {FRESHDESK_DOMAIN} today.")


if __name__ == "__main__":
    main()
