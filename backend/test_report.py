#!/usr/bin/env python3
"""Operational report tests for MejaBahasa (no model calls).

Builds a controlled day in an isolated temp database: one order ticketed
(sent_to_kitchen), one order fully served (done), one draft. Asserts the
report's revenue, status counts, top items, ticket counts, and low-stock
list are computed from the database — never invented.

Usage: python3 backend/test_report.py   (from the repo root)
Exit code 0 = all checks passed.
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import report, seed, tools  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    PASSED.append(name)
    print(f"  PASS {name}" + (f" — {detail}" if detail else ""))


def drive_to_ticket(order_id, db, serve=False):
    tools.confirm_order(order_id, db_path=db)
    tools.request_acc(order_id, db_path=db)
    tools.decide_acc(order_id, True, db_path=db)
    ticket = tools.send_kitchen_ticket(order_id, db_path=db)
    if serve:
        for st in ("cooking", "ready", "served"):
            tools.set_ticket_status(ticket["ticket_id"], st, db_path=db)
    return ticket


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-report-"))
    db = str(tmp / "test.db")
    print(f"[1] seed -> {db}")
    count = seed.create_db(db)
    check("seed 14 item fiktif", count == 14, f"{count} item")

    print("[2] bangun satu hari terkendali")
    o1 = tools.create_order(
        "A12",
        [{"menu_item_id": 1, "qty": 2}, {"menu_item_id": 9, "qty": 1}],
        "id", db_path=db)["order_id"]
    drive_to_ticket(o1, db)  # sent_to_kitchen, total 102000
    o2 = tools.create_order(
        "A13", [{"menu_item_id": 4, "qty": 1}], "id",
        db_path=db)["order_id"]
    drive_to_ticket(o2, db, serve=True)  # done, total 65000
    tools.create_order(
        "A14", [{"menu_item_id": 2, "qty": 1}], "id",
        db_path=db)  # draft, tidak masuk pendapatan

    print("[3] laporan harian")
    r = report.daily_report(db)
    print(f"  laporan: {r}")
    check("total order = 3", r["total_orders"] == 3,
          f"total={r['total_orders']}")
    check("status: 1 sent_to_kitchen, 1 done, 1 draft",
          r["orders_by_status"] == {"sent_to_kitchen": 1, "done": 1,
                                    "draft": 1},
          str(r["orders_by_status"]))
    check("pendapatan = 102000 + 65000 = 167000 (draft tidak dihitung)",
          r["revenue_idr"] == 167000, f"revenue={r['revenue_idr']}")
    check("item terlaris #1: Nasi Goreng, terjual 2",
          r["top_items"] and r["top_items"][0]["name"] == "Nasi Goreng Spesial"
          and r["top_items"][0]["sold"] == 2, str(r["top_items"]))
    check("tiket: 1 open, 1 served",
          r["tickets_by_status"] == {"open": 1, "served": 1},
          str(r["tickets_by_status"]))
    low_names = {i["name"] for i in r["low_stock"]}
    check("stok menipis: Ikan (8) + Rendang (9 setelah tiket)",
          low_names == {"Ikan Bakar Jimbaran", "Rendang Sapi"},
          str(low_names))
    check("Es Teh (49) tidak masuk stok menipis",
          "Es Teh Manis" not in low_names)

    print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
