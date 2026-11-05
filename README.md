# MejaBahasa — Multilingual Restaurant Agent

> Baca dalam Bahasa Indonesia: [README.id.md](README.id.md)

**Status:** working demo (5 November 2026) — guest chat with cart &
checkout, table-QR scanner, staff dashboard with live table occupancy,
kitchen display, floor coordinator, and owner dashboard all run against one
SQLite database. 200 automated checks pass.

**Live demo:** http://139.190.106.34:8000 (guest) and `/staff`,
`/kitchen`, `/koordinator`, `/owner` on the same host.

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
Scan table QR → welcome screen → pick language (47 supported, AI-translated
  on demand) → browse the visual menu (photos, TipTop-style cards)
  → add to cart 🛒 → checkout → AI asks about allergies, requests,
  disliked ingredients → AI asks dine-in or takeaway
  → dine-in: "got a table yet?" → scan the table QR with the in-app camera
    (table number is read from the QR itself, never typed) → food routed
    to that table
  → no table yet: takeaway, or join the waiting list (queue number issued;
    free tables are visible to waiting guests)
  → guest confirms → staff ACC → Indonesian kitchen ticket
  → guest watches live order status; reply can be read aloud
```

No manual language picker: detection is automatic, with a manual picker only
as a fallback if detection gets it wrong. The audience is universal — any
guest at the table, tourist or local. Guests can also browse the menu without
chatting first.

## What is built

- **Guest door** (`/`): welcome screen, 47-language picker (18 curated +
  29 AI-translated on demand), multilingual chat, voice input (Web Speech
  API, with graceful degradation), reply read-aloud, visual menu with food
  photos, **shopping cart** (add / qty / total / checkout), live
  order-status polling, complaint reporting.
- **Table QR scanner**: in-app camera scanner (jsQR, served locally, no
  CDN) reads the table number straight from the table's QR — the guest
  never types it. Manual entry remains as fallback.
- **Dine-in / takeaway / waiting list**: the agent asks after every draft;
  takeaway orders are flagged for the kitchen; the waiting list issues
  queue numbers and shows waiting guests which tables are free.
- **Staff dashboard** (`/staff`): order queue (takeaway flagged 🥡), ACC
  accept/reject, send kitchen ticket, complaint triage, menu admin
  (stock / price), **live table-occupancy board** (🟢 free / 🔴 occupied
  with order number), waiting-list management (call / seat / cancel).
- **Kitchen display** (`/kitchen`): ticket lifecycle open → cooking →
  ready → served.
- **Floor coordinator** (`/koordinator`): who cooks/makes/delivers what, per
  table — greedy assignment to the lightest load, bottleneck target per
  table, delivery tasks for waiters.
- **Owner dashboard** (`/owner`): revenue, orders by status, top 5 items,
  ticket statuses, low stock (≤10), complaints by urgency and open count.
  Refreshes every 10 seconds.
- **Guardrails**: allergy question before ordering (rule 7), clash rejects
  the whole order; disliked-ingredient handling ("tanpa bawang" → custom
  note, or alternative dish); ingredient Q&A answered from the database;
  budget composer; partial revision & negotiation; race-safe
  stock (BEGIN IMMEDIATE); time-window menu items; every agent action logged
  in `agent_action_log`.

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
(`BASETEN_API_KEY`), see `.env.example`. Without a key, the dashboards,
kitchen, coordinator, and owner pages all work; only the AI chat replies need
the key.

## Agent rules (guardrails — what makes this an agent, not a chatbot)

1. **Never guess.** When an order is ambiguous → ask back in the guest's
   language.
2. **Prices / stock / menu ONLY from the database.** No inventing.
3. **Orders and tickets only run after guest confirmation + staff ACC**
   (human-in-the-loop).
4. **Every agent action is logged** in `agent_action_log` so it can be
   audited.
5. **Allergy + dislikes before every draft** (rule 7/7b): allergies,
   restrictions, and disliked ingredients are asked once per draft; a
   clash rejects the whole order.
6. **Dine-in / takeaway / waiting list** (rule 17): asked before
   `create_order`; after the draft the agent asks "got a table yet?" and
   points at the QR scanner, or offers takeaway / waiting list.

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
  server.py            Stdlib demo server: guest, staff, kitchen,
                       coordinator, owner pages + JSON API
  report.py            Operational report computed from the database
  coordinator.py       Floor-coordinator task planner (greedy assignment)
  queue_estimate.py    Parallel per-station wait estimate
  complaints.py        Deterministic complaint triage (open→handling→resolved)
  menu_admin.py        Staff stock/price controls
  seed.py              Build/seed the database (safe re-runs, migrations)
  test_*.py            Test suites — *_live.py need a model key, the rest
                       run offline
data/
  schema.sql           SQLite schema: menu, stock, orders, tickets, action log,
                       waiting list, table occupancy
demo/
  menu.json            FICTITIOUS sample menu (not any real restaurant's data)
static/
  jsqr.min.js          QR-decoding library served locally (no CDN at runtime)
  menu/                Food photos for the visual menu
.env.example           Configuration placeholder (no real keys)
.gitignore
```

## Scope — NOT built in this hackathon (roadmap)

- **WhatsApp door for local guests** (menu questions, daily specials,
  reservations, order status) — phase 2 after the hackathon, same engine, a
  different door. WhatsApp Cloud API integration is deliberately not built
  here because it is a project of its own.
- **Bill explanation and waiter tutor** — video-roadmap only.
- **Real payment gateway (QRIS / transfer / deposit)** — only a simulated
  step in the demo, not a real transaction (needs a merchant account and
  business documents).
- **Reservations + floor plan editor, multi-restaurant, native apps.**

(The table waiting list was a stretch goal — it is now built: queue
numbers, staff call/seat/cancel, and free-table visibility for waiting
guests.)

## Demo data

`demo/menu.json` is a **fictitious** sample menu created for demo and testing
— not the menu, prices, or stock of any real restaurant.

## Running

```bash
cp .env.example .env   # fill in your own BASETEN_API_KEY, never commit .env
python3 backend/server.py 8000
# guest:       http://localhost:8000/
# staff:       http://localhost:8000/staff
# kitchen:     http://localhost:8000/kitchen
# coordinator: http://localhost:8000/koordinator
# owner:       http://localhost:8000/owner
```

The database (`data/mejabahasa.db`) is created locally from
`data/schema.sql` + `demo/menu.json` at first run. `*.db` and `.env` are
git-ignored — never committed.

**No authentication in this demo.** All pages are open on the local network
by design; the owner dashboard says so on the page itself.

**Live demo:** http://139.190.106.34:8000 — served from a VPS (Biznet
GioCloud, `backend/server.py` on port 8000). Table QR codes point at
`http://139.190.106.34:8000/?table=<TABLE_NO>` so the table number is
captured automatically on scan.
