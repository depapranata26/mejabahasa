#!/usr/bin/env python3
"""Floor coordinator (koordinator lapangan) for MejaBahasa.

Turns live kitchen tickets into a per-person work plan across three
stations — dapur, bar, waiter — plus a ready-time target per table.
This is the staff-facing side of the order queue; the guest-facing
wait estimate (queue_estimate) is computed from this same plan, so the
two screens never contradict each other.

Model (deterministic, demo-honest):
- Every ticket's items become tasks on their menu station (dapur for
  cooked food, bar for drinks + es campur). A task's size is
  qty * prep_minutes from demo/menu.json (demo data; an item without
  one falls back to DEFAULT_PREP_MINUTES).
- Tasks are assigned GREEDILY in ticket order (oldest first) to the
  station member with the smallest current load (ties -> first member
  in roster order). A member's load is the sum of their tasks' prep.
  A task runs start_minutes -> finish_minutes on its member's timeline.
- A table's target = the latest finish among its dapur/bar tasks
  (the bottleneck station). Ready tickets have target 0 (food is done);
  their delivery becomes a waiter task (DELIVERY_MINUTES each).
- Orders without a ticket yet (draft..acc_accepted) are NOT work yet,
  so they do not appear in the plan. For an estimate they are appended
  hypothetically at the end of the queue (see estimate_for_order).

Roster is demo roles, not invented people: a real deployment replaces
ROSTER with the shift's actual staff list (same shape).
"""
import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREP_MINUTES = 10
DELIVERY_MINUTES = 3

ROSTER = {
    "dapur": ["Juru Masak 1", "Juru Masak 2"],
    "bar": ["Barista"],
    "waiter": ["Waiter 1", "Waiter 2"],
}

UNFINISHED_TICKET_STATUSES = ("open", "cooking")


def load_menu(menu_path=None):
    """menu_item_id -> {prep, station, name} from demo/menu.json."""
    path = Path(menu_path or REPO_ROOT / "demo" / "menu.json")
    menu = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for it in menu["items"]:
        station = it.get("station")
        if station is None:
            station = "bar" if it.get("category") == "minuman" else "dapur"
        out[it["id"]] = {
            "prep": it.get("prep_minutes", DEFAULT_PREP_MINUTES),
            "station": station,
            "name": it["name"],
        }
    return out


def load_prep(menu_path=None):
    """menu_item_id -> prep_minutes (kept for queue_estimate)."""
    return {mid: info["prep"] for mid, info in
            load_menu(menu_path).items()}


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _tickets_with_items(conn):
    """Unfinished tickets (oldest first) with their order items."""
    ph = ",".join("?" for _ in UNFINISHED_TICKET_STATUSES)
    tickets = conn.execute(
        f"SELECT kt.id AS ticket_id, kt.status AS ticket_status,"
        " o.id AS order_id, o.table_no FROM kitchen_tickets kt"
        " JOIN orders o ON o.id = kt.order_id"
        f" WHERE kt.status IN ({ph}) ORDER BY kt.id",
        UNFINISHED_TICKET_STATUSES).fetchall()
    out = []
    for t in tickets:
        items = conn.execute(
            "SELECT oi.id, oi.menu_item_id, oi.qty FROM order_items oi"
            " WHERE oi.order_id = ? ORDER BY oi.id",
            (t["order_id"],)).fetchall()
        out.append({"ticket_id": t["ticket_id"],
                    "ticket_status": t["ticket_status"],
                    "order_id": t["order_id"],
                    "table_no": t["table_no"],
                    "items": [dict(r) for r in items]})
    return out


def _ready_tickets(conn):
    """Tickets with status ready (food done, delivery pending)."""
    rows = conn.execute(
        "SELECT kt.id AS ticket_id, o.id AS order_id, o.table_no"
        " FROM kitchen_tickets kt JOIN orders o ON o.id = kt.order_id"
        " WHERE kt.status = 'ready' ORDER BY kt.id").fetchall()
    return [dict(r) for r in rows]


def _assign(loads, members, station, prep):
    """Greedy assignment: least-loaded member; returns (member, start, finish)."""
    member = min(members, key=lambda m: (loads[m], members.index(m)))
    start = loads[member]
    loads[member] = start + prep
    return member, start, loads[member]


def _plan_tasks(tickets, menu):
    """Assign all ticket tasks. Returns (member_tasks, order_targets).

    member_tasks: {station: {member: [task, ...]}} with running loads.
    order_targets: {order_id: (target_minutes, bottleneck_station)}.
    """
    loads = {st: {m: 0 for m in members}
             for st, members in ROSTER.items()}
    member_tasks = {st: {m: [] for m in members}
                    for st, members in ROSTER.items()}
    order_finish = {}
    for t in tickets:
        finishes = []
        for item in t["items"]:
            info = menu.get(item["menu_item_id"])
            if info is None:
                continue
            prep = item["qty"] * info["prep"]
            member, start, finish = _assign(
                loads[info["station"]], ROSTER[info["station"]],
                info["station"], prep)
            member_tasks[info["station"]][member].append({
                "order_id": t["order_id"], "table_no": t["table_no"],
                "item": info["name"], "qty": item["qty"],
                "station": info["station"], "member": member,
                "start_minutes": start, "finish_minutes": finish})
            finishes.append((finish, info["station"]))
        if finishes:
            top_finish, bottleneck = max(finishes)
            order_finish[t["order_id"]] = (top_finish, bottleneck)
        else:
            order_finish[t["order_id"]] = (0, None)
    return member_tasks, loads, order_finish


def build_plan(db_path, menu_path=None, table_no=None):
    """Full coordinator plan (optionally narrowed to one table's view).

    A table filter narrows which orders/tasks are SHOWN, but targets
    stay queue-aware (computed from the full queue, not recomputed).
    """
    menu = load_menu(menu_path)
    conn = _conn(db_path)
    try:
        tickets = _tickets_with_items(conn)
        member_tasks, loads, order_finish = _plan_tasks(tickets, menu)
        # Waiter deliveries for ready tickets (least-loaded waiter).
        for t in _ready_tickets(conn):
            member, start, finish = _assign(
                loads["waiter"], ROSTER["waiter"], "waiter",
                DELIVERY_MINUTES)
            member_tasks["waiter"][member].append({
                "order_id": t["order_id"], "table_no": t["table_no"],
                "item": "Antar pesanan", "qty": 1, "station": "waiter",
                "member": member, "start_minutes": start,
                "finish_minutes": finish})
        orders = []
        for t in tickets:
            target, bottleneck = order_finish[t["order_id"]]
            orders.append({"order_id": t["order_id"],
                           "table_no": t["table_no"],
                           "ticket_status": t["ticket_status"],
                           "target_minutes": target,
                           "bottleneck_station": bottleneck})
        for t in _ready_tickets(conn):
            orders.append({"order_id": t["order_id"],
                           "table_no": t["table_no"],
                           "ticket_status": "ready",
                           "target_minutes": 0,
                           "bottleneck_station": None})
        if table_no is not None:
            keep = {o["order_id"] for o in orders
                    if o["table_no"] == table_no}
            orders = [o for o in orders if o["order_id"] in keep]
            member_tasks = {
                st: {m: [tk for tk in tasks if tk["order_id"] in keep]
                     for m, tasks in members.items()}
                for st, members in member_tasks.items()}
        members = []
        for station, roster in ROSTER.items():
            for member in roster:
                members.append({
                    "station": station, "member": member,
                    "load_minutes": loads[station][member],
                    "tasks": member_tasks[station][member]})
        return {"ok": True, "members": members, "orders": orders,
                "unfinished_tickets": len(tickets),
                "note": "rencana deterministik — dapur/bar/waiter, beban paling ringan dulu"}
    finally:
        conn.close()


def _order_row(conn, order_id):
    return conn.execute(
        "SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()


def _own_items(conn, order_id):
    return [dict(r) for r in conn.execute(
        "SELECT oi.menu_item_id, oi.qty FROM order_items oi"
        " WHERE oi.order_id = ? ORDER BY oi.id", (order_id,)).fetchall()]


def estimate_for_order(db_path, order_id, menu_path=None):
    """Guest wait estimate for one order, from the coordinator plan.

    Same model as build_plan; an order without a ticket is appended
    hypothetically at the end of the queue. None if the order is unknown.
    """
    menu = load_menu(menu_path)
    conn = _conn(db_path)
    try:
        row = _order_row(conn, order_id)
        if row is None:
            return None
        if row["status"] == "acc_rejected":
            return {"ok": True, "order_id": order_id,
                    "estimated_minutes": None, "own_prep_minutes": 0,
                    "bottleneck_station": None,
                    "note": "ditolak staf — tidak dimasak"}
        ticket = conn.execute(
            "SELECT id, status FROM kitchen_tickets WHERE order_id = ?",
            (order_id,)).fetchone()
        if ticket is not None and ticket["status"] in ("ready", "served"):
            return {"ok": True, "order_id": order_id,
                    "estimated_minutes": 0, "own_prep_minutes": 0,
                    "bottleneck_station": None, "note": "siap/diantar"}
        if row["status"] == "done":
            return {"ok": True, "order_id": order_id,
                    "estimated_minutes": 0, "own_prep_minutes": 0,
                    "bottleneck_station": None, "note": "selesai disajikan"}
        tickets = _tickets_with_items(conn)
        own_items = _own_items(conn, order_id)
        own_prep = sum(
            it["qty"] * menu[it["menu_item_id"]]["prep"]
            for it in own_items if it["menu_item_id"] in menu)
        if ticket is not None:
            _, _, order_finish = _plan_tasks(tickets, menu)
            target, bottleneck = order_finish.get(order_id, (own_prep, None))
            return {"ok": True, "order_id": order_id,
                    "estimated_minutes": target,
                    "own_prep_minutes": own_prep,
                    "bottleneck_station": bottleneck,
                    "note": "perkiraan — dari rencana koordinator"}
        # No ticket yet: append own items hypothetically at queue end.
        member_tasks, loads, _ = _plan_tasks(tickets, menu)
        finishes = []
        for it in own_items:
            info = menu.get(it["menu_item_id"])
            if info is None:
                continue
            _, _, finish = _assign(loads[info["station"]],
                                   ROSTER[info["station"]],
                                   info["station"],
                                   it["qty"] * info["prep"])
            finishes.append((finish, info["station"]))
        if finishes:
            target, bottleneck = max(finishes)
        else:
            target, bottleneck = 0, None
        return {"ok": True, "order_id": order_id,
                "estimated_minutes": target,
                "own_prep_minutes": own_prep,
                "bottleneck_station": bottleneck,
                "note": "perkiraan — dari rencana koordinator"}
    finally:
        conn.close()


def estimate_backlog(db_path, menu_path=None):
    """Current queue backlog: unfinished tickets + time to clear."""
    menu = load_menu(menu_path)
    conn = _conn(db_path)
    try:
        tickets = _tickets_with_items(conn)
        _, loads, _ = _plan_tasks(tickets, menu)
        clear = max([loads["dapur"][m] for m in ROSTER["dapur"]]
                    + [loads["bar"][m] for m in ROSTER["bar"]] + [0])
        return {"ok": True, "unfinished_tickets": len(tickets),
                "backlog_minutes": clear,
                "note": "perkiraan — dari rencana koordinator"}
    finally:
        conn.close()


def estimate_new_order(db_path, items, menu_path=None):
    """Wait estimate for a hypothetical new order (items not stored)."""
    menu = load_menu(menu_path)
    conn = _conn(db_path)
    try:
        tickets = _tickets_with_items(conn)
        _, loads, _ = _plan_tasks(tickets, menu)
        finishes = []
        own = 0
        for it in items:
            info = menu.get(it["menu_item_id"])
            if info is None:
                continue
            prep = it["qty"] * info["prep"]
            own += prep
            _, _, finish = _assign(loads[info["station"]],
                                   ROSTER[info["station"]],
                                   info["station"], prep)
            finishes.append((finish, info["station"]))
        if finishes:
            target, bottleneck = max(finishes)
        else:
            target, bottleneck = 0, None
        return {"ok": True, "estimated_minutes": target,
                "own_prep_minutes": own,
                "bottleneck_station": bottleneck,
                "note": "perkiraan — dari rencana koordinator"}
    finally:
        conn.close()
