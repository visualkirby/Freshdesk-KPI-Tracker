# Business Problem

[⬅ Back to README](README.md)

## The ask

A financial-services support team needed a supervisor to see, every day, how many tickets each agent handled against a personal target. Anyone falling below target should trigger a notification on the first day it happens, and a short written summary should explain the day before anyone opens a chart. Nobody on the team was supposed to pull a report by hand.

## Why this is a rebuild

The design comes from a real Upwork contract: helpdesk ticket data into a KPI list, automated notifications, a Power BI dashboard and a daily AI-written pattern summary. That client ran Zendesk, and their SSO configuration blocked the API connection the build needed. The client stays unnamed here.

This repo builds the same architecture on Freshdesk, whose API was open on a trial account, so every piece could run end to end.

## Stack decision

Three options were on the table:

| Option | Trade-off |
|---|---|
| Python -> Freshdesk API -> SQLite/CSV, Power BI Desktop | Portable, but the Power Automate side would only be documented, never live |
| Apps Script + Looker Studio | Cheap, but nothing like the contract's Microsoft stack |
| Microsoft 365 Business trial (SharePoint, Power Automate, Power BI Pro, Teams) | Closest to the real contract; limited to a 30-day trial |

The M365 trial won. One constraint showed up on day one: the generic HTTP action in Power Automate is a Premium connector, and M365 Business doesn't include Premium. Freshdesk's own connector is Standard tier but only does single-ticket CRUD plus triggers, with no search or list action. So instead of one flow that queries Freshdesk nightly, the design became two flows:

- **Flow A** counts each ticket as it's created (event-driven).
- **Flow B** runs once at 6pm, checks every agent against target and handles agents with zero tickets, which Flow A can never see.

## What's seeded and what's live

Seeded:
- The tickets themselves, created daily by `scripts/seed_daily_tickets.py` with realistic banking subjects (fraud disputes, stuck wires, card declines, lockouts).
- Two of the three agents (Maria Torres and James Okafor) are fictional and have no Teams identity, so alerts go to the supervisor.

Live:
- Freshdesk's own automation rules, which reassign some tickets before Power Automate sees them.
- Every flow run, list write, Teams post and Claude summary.
- The Power BI dashboard, refreshed from SharePoint.

The SLA Compliance tile is the one exception. It shows a fixed sample value and is labeled "(Sample)" on the dashboard, since a first-response-time pull from Freshdesk was never built.
