# MejaBahasa — Multilingual Restaurant Agent

> Baca dalam Bahasa Indonesia: [README.id.md](README.id.md)

**Status:** Day 1 (27 October 2026) — initial scaffold. The ordering core follows on day 2.

## Problem

In busy restaurants, waiters lose most of their time explaining dishes and
taking orders — especially when guests do not speak the staff's language, and
especially when the restaurant is short-staffed. Guests who cannot speak
English (notably from China, Korea, and Japan) often end up with the wrong
order, while house rules may forbid waiters from using a phone to translate.

MejaBahasa fixes this with a door that is safe under those rules: **a QR code
on the table → a web chat on the guest's own phone**. Guests order in their
own language — by typing or speaking — and the AI detects the language
automatically. The agent validates every order against the real menu and
stock, asks back when anything is ambiguous, and — only after the guest
confirms and staff approves (ACC) — sends a clean Indonesian ticket to the
kitchen. Waiters stop being stuck explaining menus and go back to serving,
clearing tables, and everything else a short-staffed floor needs.

## Guest flow

```
Scan table QR → choose speak or type
  → AI auto-detects the guest's language
  → order in the guest's language (text; voice as an enhancement)
  → agent validates menu & stock → clarifies when ambiguous
  → guest confirms → staff ACC → Indonesian kitchen ticket
```

No manual language picker: detection is automatic, with a manual picker only
as a fallback if detection gets it wrong. The audience is universal — any
guest at the table, tourist or local.

## Hackathon context

- **Event:** Meta Global AI Developer Hackathon 2026
- **Track:** Indie Developer — theme *Build for Your World*
- **Target:** technical challenge + *Best Agentic Use-Case* bonus
- **Submission deadline:** 5 November 2026, 09:00 WIB
- **Model:** a Meta open-weight model, used load-bearing (not a chatbot
  wrapper): the agent plans, calls tools, and acts — validation,
  clarification, tickets, and ACC are all visible in the demo.

## Model provider (swappable)

Primary provider: **Baseten** with model `meta-models/muse-spark-open-1026`
(OpenAI-compatible, base URL `https://inference.baseten.co/v1`).

The provider layer is designed to be swapped (`backend/provider.py`):
Fireworks and the Meta Model API are prepared as the same interface. API keys
are **never** written in this repo — keys are read from the environment
(`BASETEN_API_KEY`), see `.env.example`.

## Agent rules (guardrails — what makes this an agent, not a chatbot)

1. **Never guess.** When an order is ambiguous → ask back in the guest's
   language.
2. **Prices / stock / menu ONLY from the database.** No inventing.
3. **Orders and tickets only run after guest confirmation + staff ACC**
   (human-in-the-loop).
4. **Every agent action is logged** in `agent_action_log` so it can be
   audited.

## Repo structure

```
README.md              Project overview, scope, how to run (English)
README.id.md           Same overview in Bahasa Indonesia
backend/
  __init__.py
  config.py            Configuration from environment (no secrets)
  provider.py          Model provider interface (Baseten primary, swappable)
  tools.py             Agent tools (menu, stock, orders, tickets, budget
                       composer, revision, time windows) + human steps
                       (confirm, ACC) + ticket lifecycle
  agent.py             Agent loop: model -> tool calls -> reply in guest language
  server.py            Stdlib demo server: guest chat, staff dashboard, kitchen
  report.py            Operational report computed from the database
  seed.py              Build/seed the database (safe re-runs, migrations)
  test_*.py            Test suites (ordering, allergy, race, server, budget,
                       revision, report, time windows)
data/
  schema.sql           SQLite schema: menu, stock, orders, tickets, action log
demo/
  menu.json            FICTITIOUS sample menu (not any real restaurant's data)
.env.example           Configuration placeholder (no real keys)
.gitignore
```

## Scope — NOT built in this hackathon (roadmap)

- **WhatsApp door for local guests** (menu questions, daily specials,
  reservations, order status) — phase 2 after the hackathon, same engine, a
  different door. WhatsApp Cloud API integration is deliberately not built
  here because it is a project of its own.
- **Table waitlist** — stretch goal, only if the core finishes early.
- **Real payment gateway (QRIS / transfer / deposit)** — only a simulated
  step in the demo, not a real transaction (needs a merchant account and
  business documents).
- **Reservations + floor plan editor, multi-restaurant, native apps.**

## Demo data

`demo/menu.json` is a **fictitious** sample menu created for demo and testing
— not the menu, prices, or stock of any real restaurant.

## Running (grows with the build)

```bash
cp .env.example .env   # fill in your own BASETEN_API_KEY, never commit .env
python3 -m backend.tools  # day-1 placeholder
```

Note: `*.db` and `.env` are git-ignored (see `.gitignore`) — the database is
created locally from `data/schema.sql` + `demo/menu.json` at runtime.
