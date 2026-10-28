#!/usr/bin/env python3
"""Deterministic kitchen-queue wait estimate (masalah restoran #9).

This estimates the wait for FOOD orders (kitchen ticket queue) — it is
NOT the seating waitlist, which stays a stretch goal / video roadmap
item. The distinction matters: this answers "pesanan saya berapa lama
lagi?", not "kapan saya dapat meja?".

Model (documented simplification; the UI labels the result
"perkiraan", never a promise):
- The kitchen works tickets SEQUENTIALLY in creation order (one
  station). A ticket's prep time = sum(qty * prep_minutes) over its
  order items.
- prep_minutes comes from demo/menu.json (demo data). An item without
  one falls back to DEFAULT_PREP_MINUTES.
- Order WITH a ticket: ahead = unfinished (open/cooking) tickets with a
  smaller id; estimated = prep(ahead tickets) + own prep.
- Order WITHOUT a ticket yet (draft..acc_accepted): ahead = ALL
  unfinished tickets; estimated = prep(ahead) + own prep; the order
  joins the queue at position ahead_count + 1.
- Ticket ready/served, or order done -> estimated 0 (nothing to wait
  for). Order acc_rejected -> estimate None (it will never be cooked).

No model calls here: the agent quotes these numbers through the
estimate_wait tool instead of inventing a wait time.
"""
import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREP_MINUTES = 10
UNFINISHED_TICKET_STATUSES = ("open", "cooking")


def load_prep(menu_path=None):
    """menu_item_id -> prep_minutes from demo/menu.json (demo data)."""
    path = Path(menu_path or REPO_ROOT / "demo" / "menu.json")
    menu = json.loads(path.read_text(encoding="utf-8"))
    return {it["id"]: it.get("prep_minutes", DEFAULT_PREP_MINUTES)
            for it in menu["items"]}


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ticket_prep(conn, order_id, prep):
    """Prep minutes for one order's items (0 if it has no items)."""
    rows = conn.execute(
        "SELECT oi.qty, oi.menu_item_id FROM order_items oi"
        " WHERE oi.order_id = ?", (order_id,)).fetchall()
    return sum(r["qty"] * prep.get(r["menu_item_id"],
                                   DEFAULT_PREP_MINUTES) for r in rows)


def _unfinished_tickets(conn):
    """(ticket_id, order_id) for open/cooking tickets, oldest first."""
    ph = ",".join("?" for _ in UNFINISHED_TICKET_STATUSES)
    return conn.execute(
        f"SELECT id, order_id FROM kitchen_tickets WHERE status IN ({ph})"
        " ORDER BY id", UNFINISHED_TICKET_STATUSES).fetchall()


def estimate_for_order(db_path, order_id, menu_path=None):
    """Wait estimate for one existing order. None if the order is unknown."""
    prep = load_prep(menu_path)
    conn = _conn(db_path)
    try:
        order = conn.execute(
            "SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            return None
        if order["status"] == "acc_rejected":
            return {"ok": True, "order_id": order_id,
                    "estimated_minutes": None, "position": 0,
                    "tickets_ahead": 0, "own_prep_minutes": 0,
                    "queue_ahead_minutes": 0,
                    "note": "ditolak staf — tidak dimasak"}
        ticket = conn.execute(
            "SELECT id, status FROM kitchen_tickets WHERE order_id = ?",
            (order_id,)).fetchone()
        own = _ticket_prep(conn, order_id, prep)
        unfinished = _unfinished_tickets(conn)
        if ticket is not None and ticket["status"] in ("ready", "served"):
            return {"ok": True, "order_id": order_id,
                    "estimated_minutes": 0, "position": 0,
                    "tickets_ahead": 0, "own_prep_minutes": own,
                    "queue_ahead_minutes": 0, "note": "siap/diantar"}
        if order["status"] == "done":
            return {"ok": True, "order_id": order_id,
                    "estimated_minutes": 0, "position": 0,
                    "tickets_ahead": 0, "own_prep_minutes": own,
                    "queue_ahead_minutes": 0, "note": "selesai disajikan"}
        if ticket is not None:
            ahead = [t for t in unfinished if t["id"] < ticket["id"]]
        else:
            ahead = list(unfinished)
        ahead_minutes = sum(
            _ticket_prep(conn, t["order_id"], prep) for t in ahead)
        return {"ok": True, "order_id": order_id,
                "estimated_minutes": ahead_minutes + own,
                "position": len(ahead) + 1,
                "tickets_ahead": len(ahead),
                "own_prep_minutes": own,
                "queue_ahead_minutes": ahead_minutes,
                "note": "perkiraan — dapur bekerja sekuensial"}
    finally:
        conn.close()


def estimate_backlog(db_path, menu_path=None):
    """Current kitchen backlog (no specific order): tickets + minutes."""
    prep = load_prep(menu_path)
    conn = _conn(db_path)
    try:
        unfinished = _unfinished_tickets(conn)
        total = sum(
            _ticket_prep(conn, t["order_id"], prep) for t in unfinished)
        return {"ok": True, "unfinished_tickets": len(unfinished),
                "backlog_minutes": total,
                "note": "perkiraan — dapur bekerja sekuensial"}
    finally:
        conn.close()


def estimate_new_order(db_path, items, menu_path=None):
    """Wait estimate for a hypothetical new order (items not stored).

    items: [{menu_item_id, qty}]. The order would join the queue last.
    """
    prep = load_prep(menu_path)
    conn = _conn(db_path)
    try:
        unfinished = _unfinished_tickets(conn)
        ahead_minutes = sum(
            _ticket_prep(conn, t["order_id"], prep) for t in unfinished)
        own = sum(it["qty"] * prep.get(it["menu_item_id"],
                                       DEFAULT_PREP_MINUTES)
                  for it in items)
        return {"ok": True, "estimated_minutes": ahead_minutes + own,
                "position": len(unfinished) + 1,
                "tickets_ahead": len(unfinished),
                "own_prep_minutes": own,
                "queue_ahead_minutes": ahead_minutes,
                "note": "perkiraan — dapur bekerja sekuensial"}
    finally:
        conn.close()
