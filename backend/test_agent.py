#!/usr/bin/env python3
"""Simulated agent tests against an isolated temp DB (never the real DB).

Max 2 scenarios (time-boxed sprint):
  (a) Japanese guest orders 2 items that ARE on the menu -> a draft order
      is created and the agent replies in Japanese.
  (b) Guest orders an item NOT on the menu -> the agent asks a
      clarifying question and NO order is created.

Usage: python3 backend/test_agent.py   (from the repo root)
Prints the model replies, the tool calls, and the DB state afterwards.
Exit 0 only if both scenarios pass.
"""
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import agent, seed  # noqa: E402

JAPANESE = re.compile(r"[\u3040-\u30ff\u4e00-\u9faf]")


def _db_state(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders")]
        items = [dict(r) for r in conn.execute("SELECT * FROM order_items")]
        return orders, items
    finally:
        conn.close()


def main():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    count = seed.create_db(db_path)
    print(f"seeded temp DB: {count} menu items\n")
    passed = []

    # --- (a) Japanese guest orders 2 real items -------------------------
    print("=== (a) tamu Jepang pesan 2 item yang ADA di menu ===")
    msg_a = "ナシゴレン・スペシャルを2つとエス・テ・マニスを1つください"
    print(f"guest: {msg_a}")
    res_a = agent.run_turn(msg_a, table_no="A12", db_path=db_path)
    print(f"path: {res_a['path']}")
    for call in res_a["tool_calls"]:
        print(f"  tool {call['tool']} args={call['args']} -> {call['result']}")
    print(f"agent: {res_a['reply']}")
    orders, items = _db_state(db_path)
    print(f"DB: {len(orders)} order(s), {len(items)} order item(s): {orders} {items}")
    ok_a = (
        len(orders) == 1
        and orders[0]["status"] == "draft"
        and len(items) == 2
        and bool(JAPANESE.search(res_a["reply"]))
        and any(c["tool"] == "create_order" for c in res_a["tool_calls"])
    )
    print(f"CHECK (a): {'PASS' if ok_a else 'FAIL'}\n")
    passed.append(ok_a)

    # --- (b) guest orders an item NOT on the menu -----------------------
    print("=== (b) tamu pesan item yang TIDAK ADA di menu ===")
    msg_b = "北京ダックを1つください"
    print(f"guest: {msg_b}")
    res_b = agent.run_turn(msg_b, table_no="A12", db_path=db_path)
    print(f"path: {res_b['path']}")
    for call in res_b["tool_calls"]:
        print(f"  tool {call['tool']} args={call['args']} -> {call['result']}")
    print(f"agent: {res_b['reply']}")
    orders, items = _db_state(db_path)
    print(f"DB: {len(orders)} order(s) total (harus tetap 1 dari skenario a)")
    ok_b = (
        len(orders) == 1  # no new order created by scenario (b)
        and not any(c["tool"] == "create_order" for c in res_b["tool_calls"])
        and bool(res_b["reply"].strip())
    )
    print(f"CHECK (b): {'PASS' if ok_b else 'FAIL'}\n")
    passed.append(ok_b)

    print(f"HASIL: {sum(passed)}/2 skenario lolos")
    return 0 if all(passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
