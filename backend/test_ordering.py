#!/usr/bin/env python3
"""End-to-end test of the MejaBahasa ordering core (no model calls).

Flow: seed -> get_menu -> create_order -> check_stock -> confirm ->
request_acc -> staff decide -> send_kitchen_ticket, plus negative cases:
unknown menu item, over-stock quantity, out-of-order state transitions,
and double ticket send. Uses an isolated temp database; nothing is
written to the real data/mejabahasa.db.

Usage: python3 backend/test_ordering.py   (from the repo root)
Exit code 0 = all checks passed.
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import seed, tools  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    PASSED.append(name)
    print(f"  PASS {name}" + (f" — {detail}" if detail else ""))


def expect_error(name, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError as e:
        PASSED.append(name)
        print(f"  PASS {name} — ditolak: {e}")
        return str(e)
    raise AssertionError(f"FAILED: {name} — seharusnya ditolak, tapi lolos")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-test-"))
    db = str(tmp / "test.db")
    print(f"[1] seed -> {db}")
    count = seed.create_db(db)
    check("seed 14 item fiktif", count == 14, f"{count} item")

    print("[2] get_menu")
    menu = tools.get_menu(db_path=db)
    check("get_menu semua = 14", len(menu) == 14)
    drinks = tools.get_menu("minuman", db_path=db)
    check("get_menu minuman = 4", len(drinks) == 4)
    expect_error("get_menu kategori tak dikenal ditolak",
                 tools.get_menu, "sushi", db_path=db)

    print("[3] check_stock")
    st = tools.check_stock(1, 2, db_path=db)
    check("stok Nasi Goreng cukup untuk 2",
          st["available"] is True and st["stock"] == 20, f"stok={st['stock']}")
    st = tools.check_stock(4, 99, db_path=db)
    check("stok Rendang TIDAK cukup untuk 99",
          st["available"] is False and st["stock"] == 10, f"stok={st['stock']}")
    expect_error("check_stock item tak dikenal ditolak",
                 tools.check_stock, 999, 1, db_path=db)

    print("[4] create_order (draft, tamu Jepang, meja A12)")
    order = tools.create_order(
        "A12",
        [{"menu_item_id": 1, "qty": 2}, {"menu_item_id": 9, "qty": 1}],
        "ja",
        db_path=db,
    )
    check("draft dibuat", order["status"] == "draft",
          f"order_id={order['order_id']}")
    check("total dari harga DB (2x45000 + 1x12000)",
          order["total_idr"] == 102000, f"total={order['total_idr']}")
    oid = order["order_id"]

    print("[5] kasus negatif create_order")
    expect_error("item tidak ada di menu ditolak",
                 tools.create_order, "A12",
                 [{"menu_item_id": 999, "qty": 1}], "ja", db_path=db)
    expect_error("qty melebihi stok ditolak",
                 tools.create_order, "A12",
                 [{"menu_item_id": 4, "qty": 99}], "ko", db_path=db)
    expect_error("bahasa tak didukung ditolak",
                 tools.create_order, "A12",
                 [{"menu_item_id": 1, "qty": 1}], "xx", db_path=db)

    print("[6] state machine: transisi di luar urutan ditolak")
    expect_error("request_acc sebelum konfirmasi tamu ditolak",
                 tools.request_acc, oid, db_path=db)
    expect_error("tiket sebelum ACC ditolak",
                 tools.send_kitchen_ticket, oid, db_path=db)

    print("[7] konfirmasi tamu -> ACC -> keputusan staf -> tiket")
    r = tools.confirm_order(oid, db_path=db)
    check("tamu konfirmasi", r["status"] == "confirmed")
    r = tools.request_acc(oid, db_path=db)
    check("masuk antre ACC", r["status"] == "acc_pending")
    expect_error("tiket saat masih acc_pending ditolak",
                 tools.send_kitchen_ticket, oid, db_path=db)
    r = tools.decide_acc(oid, True, db_path=db)
    check("staf ACC terima", r["status"] == "acc_accepted")
    ticket = tools.send_kitchen_ticket(oid, db_path=db)
    check("tiket dapur dibuat",
          ticket["status"] == "open" and ticket["table_no"] == "A12",
          f"ticket_id={ticket['ticket_id']}")
    check("tiket berisi 2 jenis item", len(ticket["items"]) == 2)
    expect_error("tiket kedua untuk order yang sama ditolak",
                 tools.send_kitchen_ticket, oid, db_path=db)

    print("[8] efek samping: stok berkurang hanya saat tiket terkirim")
    st = tools.check_stock(1, 1, db_path=db)
    check("stok Nasi Goreng 20 -> 18", st["stock"] == 18,
          f"stok={st['stock']}")
    st = tools.check_stock(9, 1, db_path=db)
    check("stok Es Teh Manis 50 -> 49", st["stock"] == 49,
          f"stok={st['stock']}")

    print("[9] audit log")
    import sqlite3
    conn = sqlite3.connect(db)
    log_count = conn.execute(
        "SELECT COUNT(*) FROM agent_action_log").fetchone()[0]
    conn.close()
    check("agent_action_log terisi", log_count >= 15, f"{log_count} entri")

    print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
