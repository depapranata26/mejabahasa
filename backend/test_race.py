#!/usr/bin/env python3
"""Race-condition test: two tickets, one last portion (no model calls).

Two orders (different tables) each want the last Nasi Goreng Spesial.
Both are driven to acc_accepted, stock is set to 1, then both
send_kitchen_ticket calls fire simultaneously from two threads behind a
Barrier. Exactly one must succeed; the loser must get a clean rejection;
stock must end at 0, never negative; exactly one ticket row exists.

Why Nasi Goreng (id 1): it has no time window, so this test stays valid
whatever hour it runs at (unlike windowed items added later).

Usage: python3 backend/test_race.py   (from the repo root)
Exit code 0 = all checks passed.
"""
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import seed, tools  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    PASSED.append(name)
    print(f"  PASS {name}" + (f" — {detail}" if detail else ""))


def drive_to_acc(order_id, db):
    tools.confirm_order(order_id, db_path=db)
    tools.request_acc(order_id, db_path=db)
    tools.decide_acc(order_id, True, db_path=db)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-race-"))
    db = str(tmp / "test.db")
    print(f"[1] seed -> {db}")
    count = seed.create_db(db)
    check("seed 14 item fiktif", count == 14, f"{count} item")

    print("[2] dua order 1x Nasi Goreng, keduanya ke acc_accepted")
    o1 = tools.create_order(
        "A1", [{"menu_item_id": 1, "qty": 1}], "id", db_path=db)["order_id"]
    o2 = tools.create_order(
        "A2", [{"menu_item_id": 1, "qty": 1}], "id", db_path=db)["order_id"]
    drive_to_acc(o1, db)
    drive_to_acc(o2, db)

    print("[3] stok Nasi Goreng diset ke 1 (porsi terakhir)")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE menu_items SET stock = 1 WHERE id = 1")
    conn.commit()
    conn.close()

    print("[4] dua thread kirim tiket BERSAMAAN (Barrier)")
    barrier = threading.Barrier(2)
    results = {}

    def fire(oid):
        barrier.wait()
        try:
            t = tools.send_kitchen_ticket(oid, db_path=db)
            results[oid] = ("ok", t["ticket_id"])
        except ValueError as e:
            results[oid] = ("fail", str(e))

    t1 = threading.Thread(target=fire, args=(o1,))
    t2 = threading.Thread(target=fire, args=(o2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    print(f"  hasil: order {o1} -> {results[o1]}")
    print(f"  hasil: order {o2} -> {results[o2]}")
    oks = [oid for oid, (s, _) in results.items() if s == "ok"]
    fails = [oid for oid, (s, _) in results.items() if s == "fail"]
    check("TEPAT SATU tiket berhasil", len(oks) == 1, f"ok={oks}")
    check("yang kalah ditolak bersih (pesan stok tidak cukup)",
          len(fails) == 1 and "tidak cukup" in results[fails[0]][1],
          results[fails[0]][1] if fails else "tidak ada yang gagal")

    print("[5] keadaan akhir database")
    conn = sqlite3.connect(db)
    stock = conn.execute(
        "SELECT stock FROM menu_items WHERE id = 1").fetchone()[0]
    ntickets = conn.execute(
        "SELECT COUNT(*) FROM kitchen_tickets").fetchone()[0]
    st = {r[0]: r[1] for r in conn.execute(
        "SELECT id, status FROM orders").fetchall()}
    conn.close()
    check("stok akhir = 0, tidak pernah minus", stock == 0, f"stok={stock}")
    check("tepat 1 tiket di database", ntickets == 1, f"tiket={ntickets}")
    check("pemenang sent_to_kitchen, pecundang tetap acc_accepted",
          st[oks[0]] == "sent_to_kitchen"
          and st[fails[0]] == "acc_accepted", str(st))

    print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
