"""Agent tool implementations for MejaBahasa (ordering core, day 2).

These are the ONLY actions the planner may take, and every call is
recorded in ``agent_action_log`` (see data/schema.sql). Guardrails from
the PRD, enforced here — not by prompt hope:

1. Never guess: on any ambiguity, ask back in the guest's language instead
   of calling a mutating tool.
2. Price / stock / menu ONLY from the database. Never invent values.
   An item that is not in the database is REJECTED with a clear error —
   never fuzzy-matched or guessed.
3. An order becomes real only after guest confirmation AND staff ACC
   (human-in-the-loop). ``create_order`` stores a draft; the guest confirms
   (``confirm_order``); ``request_acc`` asks staff; staff decides
   (``decide_acc``); ``send_kitchen_ticket`` fires only for ACC-accepted
   orders and deducts stock atomically.
4. Every tool call — success or failure — is logged with its input and
   output for audit.
5. Allergy safety (PRD v0.5): every menu item carries ingredients,
   allergens, spice level, and a vegetarian flag. create_order matches
   the guest's allergy notes against each ordered item's allergens and
   REJECTS the whole order — naming the item and the allergen — on any
   conflict. A conflict is never silently passed through. Kitchen
   tickets render allergy/diet notes PROMINENTLY at the very top, plus
   each item's custom note (e.g. "kurang pedas", "tanpa bawang").

Order status flow (enforced by the functions below):
  draft -> confirmed -> acc_pending -> acc_accepted -> sent_to_kitchen
  acc_pending -> acc_rejected (staff declines; no ticket is sent)

``confirm_order`` and ``decide_acc`` are NOT agent tools: they are the
human steps (guest tap, staff tap on the dashboard) that the UI calls.
They live here so the state machine is traversable and testable before
the UI exists.

All functions accept an optional ``db_path`` (default: config.DATABASE_PATH)
so tests can use an isolated database.
"""

import json
import re
import sqlite3

try:
    from backend.config import DATABASE_PATH, SUPPORTED_LANGUAGES
except ImportError:  # direct script execution from inside backend/
    from config import DATABASE_PATH, SUPPORTED_LANGUAGES

TOOL_SPECS = [
    {
        "name": "get_menu",
        "description": (
            "List menu items from the database, optionally filtered by "
            "category. The ONLY source of menu truth; never invent dishes "
            "or prices."
        ),
        "parameters": {
            "category": "optional: makanan_utama | minuman | penutup",
        },
        "returns": "list of {id, name, category, price_idr, stock, description,"
                   " ingredients, allergens, spice_level, is_vegetarian}",
    },
    {
        "name": "check_stock",
        "description": (
            "Check whether a menu item has enough stock for a requested "
            "quantity. Reads the database; never assume availability."
        ),
        "parameters": {
            "menu_item_id": "integer, from get_menu",
            "qty": "integer > 0",
        },
        "returns": "{available: bool, stock: int}",
    },
    {
        "name": "create_order",
        "description": (
            "Store a DRAFT order (table_no, guest language, items). A draft "
            "is not a real order: it must be confirmed by the guest and "
            "then ACC-accepted by staff before any kitchen ticket is sent."
        ),
        "parameters": {
            "table_no": "string, e.g. 'A12'",
            "language": "zh | ko | ja | id",
            "items": "list of {menu_item_id, qty, custom_note?}",
            "allergy_notes": "string, e.g. 'alergi kacang' — checked "
                             "against every item's allergens; any conflict "
                             "rejects the order",
            "dietary_notes": "string, e.g. 'vegetarian' — recorded and "
                             "shown on the kitchen ticket",
        },
        "returns": "{order_id, status: 'draft'}",
    },
    {
        "name": "compose_meal",
        "description": (
            "Compose one meal (main + drink + dessert) under a guest "
            "budget, from database prices only, excluding items that "
            "conflict with the guest's allergies. Never invent prices."
        ),
        "parameters": {
            "budget_idr": "integer > 0",
            "allergy_notes": "string, e.g. 'alergi kacang'",
            "dietary_notes": "string, e.g. 'vegetarian'",
        },
        "returns": "{items: [{id, name, category, price_idr}],"
                   " total_idr, budget_idr, remaining_idr}",
    },
    {
        "name": "update_order",
        "description": (
            "Revise a DRAFT order's items (guest changed their mind). "
            "The new list replaces the old one. Re-validates stock and "
            "allergies; over-stock errors carry the available quantity "
            "plus alternatives so the agent can negotiate."
        ),
        "parameters": {
            "order_id": "integer, must be a draft",
            "items": "list of {menu_item_id, qty, custom_note?}",
            "allergy_notes": "optional override, else stored notes",
            "dietary_notes": "optional override, else stored notes",
        },
        "returns": "{order_id, status: 'draft', total_idr, items}",
    },
    {
        "name": "request_acc",
        "description": (
            "Ask staff to approve (ACC) a guest-confirmed order. Moves the "
            "order to 'acc_pending' and surfaces it on the staff dashboard."
        ),
        "parameters": {"order_id": "integer"},
        "returns": "{order_id, status: 'acc_pending'}",
    },
    {
        "name": "send_kitchen_ticket",
        "description": (
            "Send the Indonesian kitchen ticket for an ACC-ACCEPTED order "
            "only. Refused for any other status. The ticket lists table "
            "number, items, quantities, and special notes in Indonesian."
        ),
        "parameters": {"order_id": "integer"},
        "returns": "{ticket_id, status: 'open', text: rendered Indonesian "
                   "ticket with allergy/diet notes at the very top}",
    },
]

CATEGORIES = ("makanan_utama", "minuman", "penutup")

# Allergy keyword aliases: guests often say "seafood" or "peanut" instead
# of the Indonesian allergen names in the database. Aliases expand the
# guest's words into database allergen names before matching.
_ALLERGY_ALIASES = {
    "peanut": "kacang tanah",
    "seafood": ("udang", "cumi", "kerang", "ikan"),
    "shellfish": ("udang", "cumi", "kerang"),
    "milk": "susu",
    "egg": "telur",
    "soy": "kedelai",
    "wheat": "gluten/gandum",
    "gluten": "gluten/gandum",
    "coconut": "kelapa",
    "fish": "ikan",
    "shrimp": "udang",
    "squid": "cumi",
}


def _allergy_conflicts(allergy_notes, allergens):
    """Return the subset of allergens conflicting with the guest's notes.

    Matching is lowercase two-way substring: a guest word matches an
    allergen if either contains the other ("kacang" matches
    "kacang tanah" and vice versa). Alias words are expanded first.
    Tokens shorter than 3 characters are ignored to avoid false
    positives (e.g. "a" matching everything).
    """
    text = (allergy_notes or "").lower()
    if not text or not allergens:
        return []
    words = {w for w in re.split(r"[^a-z0-9/]+", text) if w}
    expanded = set(words)
    for w in words:
        alias = _ALLERGY_ALIASES.get(w)
        if alias:
            if isinstance(alias, tuple):
                expanded.update(alias)
            else:
                expanded.add(alias)
    hits = []
    for allergen in allergens:
        a = allergen.lower()
        if a in text or any(
            len(w) >= 3 and (w in a or a in w) for w in expanded
        ):
            hits.append(allergen)
    return hits


def _overstock_message(conn, mrow, qty):
    """Negotiation-ready over-stock error: available qty + alternatives.

    The message is data the agent negotiates with — never a bare refusal:
    take the available quantity, switch to an alternative, or cancel.
    """
    alts = conn.execute(
        "SELECT name, price_idr FROM menu_items"
        " WHERE category = ? AND id != ? AND stock > 0"
        " ORDER BY id LIMIT 3",
        (mrow["category"], mrow["id"]),
    ).fetchall()
    alt_text = ", ".join(
        f"{a['name']} (Rp{a['price_idr']})" for a in alts
    ) or "tidak ada alternatif se-kategori saat ini"
    return (
        f"Stok '{mrow['name']}' tidak cukup: diminta {qty}, "
        f"tersedia {mrow['stock']}. Bisa dipesan {mrow['stock']} porsi, "
        f"atau alternatif se-kategori: {alt_text}. Tanyakan ke tamu: "
        f"mau {mrow['stock']} porsi saja, ganti alternatif, atau batal?"
    )


def _parse_hhmm(value):
    """Parse 'HH:MM' -> (hour, minute) or None if invalid."""
    try:
        h, m = value.split(":")
        h, m = int(h), int(m)
    except (AttributeError, ValueError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return (h, m)


def _now_hhmm():
    import time
    return time.strftime("%H:%M", time.localtime())


def _in_window(now, frm, to):
    """True if HH:MM 'now' falls in [frm, to). Null/degenerate = always.

    Overnight windows (frm > to, e.g. 22:00-02:00) wrap past midnight.
    Unparseable data never blocks an order (fail-open on bad data).
    """
    if not frm or not to:
        return True
    f, t, n = _parse_hhmm(frm), _parse_hhmm(to), _parse_hhmm(now)
    if f is None or t is None or n is None:
        return True
    if f == t:
        return True
    if f < t:
        return f <= n < t
    return n >= f or n < t


def _row_get(row, key, default=None):
    """sqlite Row access tolerating databases predating a column."""
    try:
        keys = row.keys()
    except AttributeError:
        return default
    return row[key] if key in keys else default


def _connect(db_path):
    conn = sqlite3.connect(db_path or DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _log(db_path, order_id, action, tool, input_data, output_data):
    """Audit log: own connection, committed immediately (survives rollback)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_action_log"
            " (order_id, action, tool, input_json, output_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                order_id,
                action,
                tool,
                json.dumps(input_data, ensure_ascii=False),
                json.dumps(output_data, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _fail(db_path, order_id, tool, input_data, message):
    """Log a rejected call, then raise the same rejection to the caller."""
    _log(db_path, order_id, tool, tool, input_data, {"error": message})
    raise ValueError(message)


def _get_order(conn, order_id):
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Order {order_id} tidak ditemukan.")
    return row


def _require_status(row, expected, action):
    if row["status"] != expected:
        raise ValueError(
            f"Tidak bisa {action}: order {row['id']} berstatus "
            f"'{row['status']}', harus '{expected}'."
        )


# ---------------------------------------------------------------- agent tools

def get_menu(category=None, db_path=None, now=None):
    """List menu items from the database, optionally filtered by category.

    Each item carries available_now (time-window check against `now`,
    defaulting to the wall clock) plus its available_from/available_to.
    """
    if category is not None and category not in CATEGORIES:
        _fail(db_path, None, "get_menu", {"category": category},
              f"Kategori '{category}' tidak dikenal. "
              f"Pilihan: {', '.join(CATEGORIES)}.")
    now_hhmm = now or _now_hhmm()
    conn = _connect(db_path)
    try:
        if category:
            rows = conn.execute(
                "SELECT * FROM menu_items WHERE category = ? ORDER BY id",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM menu_items ORDER BY id"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for key, out in (("ingredients_json", "ingredients"),
                             ("allergens_json", "allergens")):
                try:
                    d[out] = json.loads(d.pop(key) or "[]")
                except json.JSONDecodeError:
                    d[out] = []
            d["is_vegetarian"] = bool(d["is_vegetarian"])
            d["available_now"] = _in_window(
                now_hhmm, d.get("available_from"), d.get("available_to"))
            result.append(d)
    finally:
        conn.close()
    _log(db_path, None, "get_menu", "get_menu",
         {"category": category}, {"count": len(result)})
    return result


def check_stock(menu_item_id, qty, db_path=None):
    """Check whether a menu item has enough stock for qty."""
    if not isinstance(qty, int) or qty <= 0:
        _fail(db_path, None, "check_stock",
              {"menu_item_id": menu_item_id, "qty": qty},
              f"Jumlah harus bilangan bulat > 0, bukan {qty!r}.")
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM menu_items WHERE id = ?", (menu_item_id,)
        ).fetchone()
        if row is None:
            _fail(db_path, None, "check_stock",
                  {"menu_item_id": menu_item_id, "qty": qty},
                  f"Item menu id {menu_item_id} tidak ada di database — "
                  "pesanan ditolak, tidak ditebak.")
        result = {
            "menu_item_id": row["id"],
            "name": row["name"],
            "available": row["stock"] >= qty,
            "stock": row["stock"],
        }
    finally:
        conn.close()
    _log(db_path, None, "check_stock", "check_stock",
         {"menu_item_id": menu_item_id, "qty": qty}, result)
    return result


def create_order(table_no, items, language, db_path=None,
                 allergy_notes="", dietary_notes="", now=None):
    """Store a DRAFT order. Validates everything before inserting anything.

    Stock is checked but NOT deducted here — a draft is not a real order.
    Deduction happens atomically in send_kitchen_ticket. Allergy notes
    are matched against every ordered item's allergens; ANY conflict
    rejects the whole order with the item name and allergen named.
    """
    inputs = {"table_no": table_no, "language": language, "items": items,
              "allergy_notes": allergy_notes, "dietary_notes": dietary_notes}
    if not table_no or not isinstance(table_no, str):
        _fail(db_path, None, "create_order", inputs,
              "Nomor meja wajib diisi (string, mis. 'A12').")
    if language not in SUPPORTED_LANGUAGES:
        _fail(db_path, None, "create_order", inputs,
              f"Bahasa '{language}' tidak didukung. "
              f"Pilihan: {', '.join(SUPPORTED_LANGUAGES)}.")
    if not items or not isinstance(items, list):
        _fail(db_path, None, "create_order", inputs,
              "Order harus berisi minimal satu item.")

    now_hhmm = now or _now_hhmm()
    conn = _connect(db_path)
    try:
        # Validate ALL items first: unknown item or bad qty rejects the
        # whole order — no partial orders, no guessing.
        resolved = []
        total_idr = 0
        for item in items:
            menu_item_id = item.get("menu_item_id")
            qty = item.get("qty")
            custom_note = item.get("custom_note") or item.get("note", "") or ""
            if not isinstance(qty, int) or qty <= 0:
                _fail(db_path, None, "create_order", inputs,
                      f"Jumlah untuk item {menu_item_id} harus bilangan "
                      f"bulat > 0, bukan {qty!r}.")
            row = conn.execute(
                "SELECT * FROM menu_items WHERE id = ?", (menu_item_id,)
            ).fetchone()
            if row is None:
                _fail(db_path, None, "create_order", inputs,
                      f"Item menu id {menu_item_id} tidak ada di database — "
                      "pesanan ditolak, tidak ditebak.")
            frm = _row_get(row, "available_from")
            to = _row_get(row, "available_to")
            if not _in_window(now_hhmm, frm, to):
                _fail(db_path, None, "create_order", inputs,
                      f"'{row['name']}' hanya tersedia {frm}–{to} "
                      f"(sekarang {now_hhmm}).")
            if row["stock"] < qty:
                _fail(db_path, None, "create_order", inputs,
                      _overstock_message(conn, row, qty))
            allergens = json.loads(row["allergens_json"] or "[]")
            conflicts = _allergy_conflicts(allergy_notes, allergens)
            if conflicts:
                _fail(db_path, None, "create_order", inputs,
                      f"ALERGI: '{row['name']}' mengandung "
                      f"{', '.join(conflicts)} yang bentrok dengan alergi "
                      f"tamu ('{allergy_notes}'). Item ini ditolak — "
                      "tawarkan alternatif dari menu, jangan loloskan.")
            resolved.append((row["id"], qty, custom_note))
            total_idr += row["price_idr"] * qty

        cur = conn.execute(
            "INSERT INTO orders (table_no, language, status,"
            " allergy_notes, dietary_notes)"
            " VALUES (?, ?, 'draft', ?, ?)",
            (table_no, language, allergy_notes or "",
             dietary_notes or ""),
        )
        order_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO order_items"
            " (order_id, menu_item_id, qty, note, custom_note)"
            " VALUES (?, ?, ?, ?, ?)",
            [(order_id, mid, qty, note, note) for mid, qty, note in resolved],
        )
        conn.commit()
    finally:
        conn.close()
    result = {"order_id": order_id, "status": "draft", "total_idr": total_idr}
    _log(db_path, order_id, "create_order", "create_order", inputs, result)
    return result


def update_order(order_id, items, db_path=None, allergy_notes=None,
                 dietary_notes=None, now=None):
    """Revise a DRAFT order's items (guest changed their mind).

    Only drafts can be revised — confirmed/ACC/ticketed orders are
    locked. The new item list REPLACES the old one (omitting an item
    removes it). Every new item is re-validated exactly like
    create_order: known item, qty > 0, stock sufficient (with available
    qty + alternatives in the error, so the agent can negotiate),
    allergy conflicts against the given notes (or the order's stored
    notes when not provided). Returns the new total.
    """
    inputs = {"order_id": order_id, "items": items,
              "allergy_notes": allergy_notes, "dietary_notes": dietary_notes}
    if not items or not isinstance(items, list):
        _fail(db_path, order_id, "update_order", inputs,
              "Order revisi harus berisi minimal satu item.")
    now_hhmm = now or _now_hhmm()
    conn = _connect(db_path)
    try:
        row = _get_order(conn, order_id)
        try:
            _require_status(row, "draft", "revisi order")
        except ValueError as e:
            conn.close()
            _fail(db_path, order_id, "update_order", inputs, str(e))
        notes = allergy_notes if allergy_notes is not None \
            else row["allergy_notes"]
        diet = dietary_notes if dietary_notes is not None \
            else row["dietary_notes"]
        resolved = []
        total_idr = 0
        for item in items:
            menu_item_id = item.get("menu_item_id")
            qty = item.get("qty")
            custom_note = item.get("custom_note") or item.get("note", "") or ""
            if not isinstance(qty, int) or qty <= 0:
                conn.close()
                _fail(db_path, order_id, "update_order", inputs,
                      f"Jumlah untuk item {menu_item_id} harus bilangan "
                      f"bulat > 0, bukan {qty!r}.")
            mrow = conn.execute(
                "SELECT * FROM menu_items WHERE id = ?", (menu_item_id,)
            ).fetchone()
            if mrow is None:
                conn.close()
                _fail(db_path, order_id, "update_order", inputs,
                      f"Item menu id {menu_item_id} tidak ada di database — "
                      "pesanan ditolak, tidak ditebak.")
            frm = _row_get(mrow, "available_from")
            to = _row_get(mrow, "available_to")
            if not _in_window(now_hhmm, frm, to):
                conn.close()
                _fail(db_path, order_id, "update_order", inputs,
                      f"'{mrow['name']}' hanya tersedia {frm}–{to} "
                      f"(sekarang {now_hhmm}).")
            if mrow["stock"] < qty:
                msg = _overstock_message(conn, mrow, qty)
                conn.close()
                _fail(db_path, order_id, "update_order", inputs, msg)
            allergens = json.loads(mrow["allergens_json"] or "[]")
            conflicts = _allergy_conflicts(notes, allergens)
            if conflicts:
                conn.close()
                _fail(db_path, order_id, "update_order", inputs,
                      f"ALERGI: '{mrow['name']}' mengandung "
                      f"{', '.join(conflicts)} yang bentrok dengan alergi "
                      f"tamu ('{notes}'). Item ini ditolak — "
                      "tawarkan alternatif dari menu, jangan loloskan.")
            resolved.append((mrow["id"], qty, custom_note))
            total_idr += mrow["price_idr"] * qty
        conn.execute("DELETE FROM order_items WHERE order_id = ?",
                     (order_id,))
        conn.executemany(
            "INSERT INTO order_items"
            " (order_id, menu_item_id, qty, note, custom_note)"
            " VALUES (?, ?, ?, ?, ?)",
            [(order_id, mid, qty, note, note) for mid, qty, note in resolved],
        )
        conn.execute(
            "UPDATE orders SET allergy_notes = ?, dietary_notes = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (notes or "", diet or "", order_id),
        )
        conn.commit()
    finally:
        conn.close()
    result = {"order_id": order_id, "status": "draft",
              "total_idr": total_idr,
              "items": [{"menu_item_id": mid, "qty": qty,
                         "custom_note": note}
                        for mid, qty, note in resolved]}
    _log(db_path, order_id, "update_order", "update_order", inputs, result)
    return result


def request_acc(order_id, db_path=None):
    """Move a guest-confirmed order to acc_pending (staff approval queue)."""
    inputs = {"order_id": order_id}
    conn = _connect(db_path)
    try:
        row = _get_order(conn, order_id)
        try:
            _require_status(row, "confirmed", "meminta ACC")
        except ValueError as e:
            conn.close()
            _fail(db_path, order_id, "request_acc", inputs, str(e))
        conn.execute(
            "UPDATE orders SET status = 'acc_pending',"
            " updated_at = datetime('now') WHERE id = ?",
            (order_id,),
        )
        conn.commit()
    finally:
        conn.close()
    result = {"order_id": order_id, "status": "acc_pending"}
    _log(db_path, order_id, "request_acc", "request_acc", inputs, result)
    return result


def send_kitchen_ticket(order_id, db_path=None):
    """Send the Indonesian kitchen ticket for an ACC-ACCEPTED order only.

    Race safety (two tables, one last portion): the whole
    read-check-deduct sequence runs inside a single ``BEGIN IMMEDIATE``
    transaction. The write lock is taken BEFORE reading stock, so a
    concurrent ticket for the same last portion blocks here and then
    re-reads post-commit stock — it gets a clean rejection, never a
    negative stock. The conditional UPDATE (``stock >= qty`` + rowcount
    check) is belt-and-suspenders on top of the lock.
    """
    inputs = {"order_id": order_id}
    conn = sqlite3.connect(db_path or DATABASE_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.isolation_level = None  # autocommit; we manage BEGIN/COMMIT
    ticket = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Order {order_id} tidak ditemukan.")
        if row["status"] != "acc_accepted":
            raise ValueError(
                f"Tidak bisa mengirim tiket dapur: order {row['id']} "
                f"berstatus '{row['status']}', harus 'acc_accepted'.")
        items = conn.execute(
            "SELECT oi.qty, oi.note, oi.custom_note, mi.name, mi.stock,"
            " mi.id AS menu_item_id"
            " FROM order_items oi JOIN menu_items mi"
            " ON mi.id = oi.menu_item_id WHERE oi.order_id = ?",
            (order_id,),
        ).fetchall()
        for it in items:
            cur = conn.execute(
                "UPDATE menu_items SET stock = stock - ?"
                " WHERE id = ? AND stock >= ?",
                (it["qty"], it["menu_item_id"], it["qty"]),
            )
            if cur.rowcount != 1:
                raise ValueError(
                    f"Stok '{it['name']}' tidak cukup saat kirim tiket: "
                    f"diminta {it['qty']}, tersedia {it['stock']}.")
        cur = conn.execute(
            "INSERT INTO kitchen_tickets (order_id, status)"
            " VALUES (?, 'open')",
            (order_id,),
        )
        ticket_id = cur.lastrowid
        conn.execute(
            "UPDATE orders SET status = 'sent_to_kitchen',"
            " updated_at = datetime('now') WHERE id = ?",
            (order_id,),
        )
        conn.execute("COMMIT")
        # Rendered ticket text: allergy/diet notes FIRST and prominent —
        # the kitchen reads fast under pressure, so safety info must be
        # the first thing on the ticket, never a footnote.
        lines = []
        allergy = row["allergy_notes"] or ""
        diet = row["dietary_notes"] or ""
        if allergy or diet:
            lines.append("⚠️  PERHATIAN ALERGI / PANTANGAN:")
            if allergy:
                lines.append(f"   ALERGI: {allergy}")
            if diet:
                lines.append(f"   PANTANGAN/DIET: {diet}")
        else:
            lines.append("Alergi/pantangan: tidak ada yang dicatat.")
        lines.append(f"MEJA {row['table_no']} — TIKET #{ticket_id}")
        for it in items:
            line = f"  {it['qty']}x {it['name']}"
            if it["custom_note"]:
                line += f"  [{it['custom_note']}]"
            lines.append(line)
        ticket = {
            "ticket_id": ticket_id,
            "order_id": order_id,
            "status": "open",
            "table_no": row["table_no"],
            "allergy_notes": allergy,
            "dietary_notes": diet,
            "items": [
                {"name": it["name"], "qty": it["qty"],
                 "note": it["custom_note"],
                 "custom_note": it["custom_note"]}
                for it in items
            ],
            "text": "\n".join(lines),
        }
    except (ValueError, sqlite3.Error) as e:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        conn.close()
        _fail(db_path, order_id, "send_kitchen_ticket", inputs, str(e))
    conn.close()
    _log(db_path, order_id, "send_kitchen_ticket", "send_kitchen_ticket",
         inputs, ticket)
    return ticket


def compose_meal(budget_idr, allergy_notes="", dietary_notes="",
                 db_path=None):
    """Compose one meal (main + drink + dessert) under a budget, from DB.

    Brute-forces every one-per-category combination (8 x 4 x 2 = 64) and
    keeps the most expensive total that stays UNDER the budget, so the
    guest gets the most for their money — never an invented price.
    Items conflicting with the guest's allergies are excluded, as are
    items with zero stock; 'vegetarian'/'vegan' notes filter accordingly.
    Items outside their time window are excluded when the menu reports
    available_now (time-window feature).
    """
    inputs = {"budget_idr": budget_idr, "allergy_notes": allergy_notes,
              "dietary_notes": dietary_notes}
    if not isinstance(budget_idr, int) or budget_idr <= 0:
        _fail(db_path, None, "compose_meal", inputs,
              f"Budget harus bilangan bulat > 0, bukan {budget_idr!r}.")
    menu = get_menu(db_path=db_path)
    notes = (dietary_notes or "").lower()
    vegan = "vegan" in notes
    vegetarian = vegan or "vegetarian" in notes
    safe = []
    for it in menu:
        if not it.get("available_now", True):
            continue
        if it["stock"] <= 0:
            continue
        if _allergy_conflicts(allergy_notes, it["allergens"]):
            continue
        if vegetarian and not it["is_vegetarian"]:
            continue
        if vegan and set(it["allergens"]) & {"telur", "susu"}:
            continue
        safe.append(it)
    by_cat = {c: [it for it in safe if it["category"] == c]
              for c in CATEGORIES}
    best = None
    for main in [None] + by_cat["makanan_utama"]:
        for drink in [None] + by_cat["minuman"]:
            for dessert in [None] + by_cat["penutup"]:
                combo = [x for x in (main, drink, dessert) if x]
                if not combo:
                    continue
                total = sum(x["price_idr"] for x in combo)
                if total > budget_idr:
                    continue
                key = (-total, -len(combo),
                       tuple(x["id"] for x in combo))
                if best is None or key < best[0]:
                    best = (key, combo, total)
    if best is None:
        result = {"items": [], "total_idr": 0, "budget_idr": budget_idr,
                  "remaining_idr": budget_idr,
                  "message": "Tidak ada kombinasi menu yang muat dalam "
                             "budget dan pantangan tersebut."}
    else:
        _, combo, total = best
        result = {
            "items": [{"id": x["id"], "name": x["name"],
                       "category": x["category"],
                       "price_idr": x["price_idr"]} for x in combo],
            "total_idr": total,
            "budget_idr": budget_idr,
            "remaining_idr": budget_idr - total,
        }
    _log(db_path, None, "compose_meal", "compose_meal", inputs, result)
    return result


# ------------------------------------------------------- human steps (not tools)

def confirm_order(order_id, db_path=None):
    """Guest confirms a draft order. Called by the guest chat UI."""
    inputs = {"order_id": order_id}
    conn = _connect(db_path)
    try:
        row = _get_order(conn, order_id)
        try:
            _require_status(row, "draft", "konfirmasi tamu")
        except ValueError as e:
            conn.close()
            _fail(db_path, order_id, "confirm_order", inputs, str(e))
        conn.execute(
            "UPDATE orders SET status = 'confirmed',"
            " updated_at = datetime('now') WHERE id = ?",
            (order_id,),
        )
        conn.commit()
    finally:
        conn.close()
    result = {"order_id": order_id, "status": "confirmed"}
    _log(db_path, order_id, "guest_confirm", "confirm_order", inputs, result)
    return result


def decide_acc(order_id, accepted, db_path=None):
    """Staff accepts or rejects an acc_pending order. Dashboard calls this."""
    inputs = {"order_id": order_id, "accepted": accepted}
    conn = _connect(db_path)
    try:
        row = _get_order(conn, order_id)
        try:
            _require_status(row, "acc_pending", "keputusan ACC staf")
        except ValueError as e:
            conn.close()
            _fail(db_path, order_id, "decide_acc", inputs, str(e))
        status = "acc_accepted" if accepted else "acc_rejected"
        conn.execute(
            "UPDATE orders SET status = ?, updated_at = datetime('now')"
            " WHERE id = ?",
            (status, order_id),
        )
        conn.commit()
    finally:
        conn.close()
    result = {"order_id": order_id, "status": status}
    _log(db_path, order_id, "staff_decide", "decide_acc", inputs, result)
    return result


# ------------------------------------------------- kitchen lifecycle (UI steps)

_TICKET_TRANSITIONS = {"open": "cooking", "cooking": "ready", "ready": "served"}


def set_ticket_status(ticket_id, status, db_path=None):
    """Move a kitchen ticket along its lifecycle. Called by kitchen UI.

    Allowed transitions only: open -> cooking -> ready -> served.
    When a ticket is served, its order becomes 'done' (end of the
    state machine: sent_to_kitchen -> done).
    """
    inputs = {"ticket_id": ticket_id, "status": status}
    if status not in _TICKET_TRANSITIONS.values():
        _fail(db_path, None, "set_ticket_status", inputs,
              f"Status tiket '{status}' tidak dikenal. "
              f"Pilihan: cooking, ready, served.")
    expected_prev = {v: k for k, v in _TICKET_TRANSITIONS.items()}[status]
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM kitchen_tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            conn.close()
            _fail(db_path, None, "set_ticket_status", inputs,
                  f"Tiket {ticket_id} tidak ditemukan.")
        if row["status"] != expected_prev:
            conn.close()
            _fail(db_path, None, "set_ticket_status", inputs,
                  f"Tidak bisa {status}: tiket {ticket_id} berstatus "
                  f"'{row['status']}', harus '{expected_prev}'.")
        conn.execute(
            "UPDATE kitchen_tickets SET status = ? WHERE id = ?",
            (status, ticket_id),
        )
        if status == "served":
            conn.execute(
                "UPDATE orders SET status = 'done',"
                " updated_at = datetime('now') WHERE id = ?",
                (row["order_id"],),
            )
        conn.commit()
    finally:
        conn.close()
    result = {"ticket_id": ticket_id, "status": status}
    _log(db_path, row["order_id"], "ticket_status", "set_ticket_status",
         inputs, result)
    return result
