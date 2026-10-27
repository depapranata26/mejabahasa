#!/usr/bin/env python3
"""Time-window availability tests for MejaBahasa (no model calls).

Covers, against an isolated temp database:
  (a) the window helper itself, including overnight windows and edges;
  (b) create_order rejects a windowed item outside its hours with a
      message naming the window, and accepts it inside the window;
  (c) get_menu marks available_now per item for an injected time;
  (d) update_order enforces windows on revision too.
Time is always INJECTED (now="HH:MM") — never the wall clock — so the
tests are deterministic whatever hour they run at.

Usage: python3 backend/test_time.py   (from the repo root)
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
    print("[1] helper _in_window")
    w = tools._in_window
    check("17:00-22:00: 12:00 di luar", w("12:00", "17:00", "22:00") is False)
    check("17:00-22:00: 16:59 di luar", w("16:59", "17:00", "22:00") is False)
    check("17:00-22:00: 17:00 masuk (awal inklusif)",
          w("17:00", "17:00", "22:00") is True)
    check("17:00-22:00: 21:59 masuk", w("21:59", "17:00", "22:00") is True)
    check("17:00-22:00: 22:00 di luar (akhir eksklusif)",
          w("22:00", "17:00", "22:00") is False)
    check("overnight 22:00-02:00: 23:30 masuk",
          w("23:30", "22:00", "02:00") is True)
    check("overnight 22:00-02:00: 01:30 masuk",
          w("01:30", "22:00", "02:00") is True)
    check("overnight 22:00-02:00: 12:00 di luar",
          w("12:00", "22:00", "02:00") is False)
    check("overnight 22:00-02:00: 02:00 di luar (akhir eksklusif)",
          w("02:00", "22:00", "02:00") is False)
    check("null = selalu tersedia", w("12:00", None, None) is True)
    check("satu sisi null = selalu tersedia",
          w("12:00", "17:00", None) is True)
    check("from == to = selalu tersedia",
          w("12:00", "17:00", "17:00") is True)

    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-time-"))
    db = str(tmp / "test.db")
    print(f"[2] seed -> {db}")
    count = seed.create_db(db)
    check("seed 14 item fiktif", count == 14, f"{count} item")
    menu = {it["id"]: it for it in tools.get_menu(db_path=db)}
    check("Ikan Bakar Jimbaran berjendela 17:00-22:00",
          menu[7]["available_from"] == "17:00"
          and menu[7]["available_to"] == "22:00",
          f"{menu[7]['available_from']}-{menu[7]['available_to']}")

    print("[3] (b) create_order di luar/di dalam jendela")
    err = expect_error(
        "Ikan pukul 12:00 ditolak",
        tools.create_order, "A12",
        [{"menu_item_id": 7, "qty": 1}], "id", db_path=db, now="12:00")
    check("pesan menyebut jendela 17:00-22:00",
          "17:00" in err and "22:00" in err, err)
    order = tools.create_order(
        "A12", [{"menu_item_id": 7, "qty": 1}], "id", db_path=db,
        now="18:00")
    check("Ikan pukul 18:00 LOLOS (draft)",
          order["status"] == "draft", f"order_id={order['order_id']}")

    print("[4] (c) get_menu menandai available_now")
    noon = {it["id"]: it for it in tools.get_menu(db_path=db, now="12:00")}
    check("pukul 12:00 Ikan available_now=False",
          noon[7]["available_now"] is False)
    check("pukul 12:00 Nasi available_now=True",
          noon[1]["available_now"] is True)
    evening = {it["id"]: it for it in tools.get_menu(db_path=db, now="18:00")}
    check("pukul 18:00 Ikan available_now=True",
          evening[7]["available_now"] is True)

    print("[5] (d) update_order menegakkan jendela")
    draft = tools.create_order(
        "A13", [{"menu_item_id": 1, "qty": 1}], "id", db_path=db)
    expect_error(
        "revisi ke Ikan pukul 12:00 ditolak",
        tools.update_order, draft["order_id"],
        [{"menu_item_id": 7, "qty": 1}], db_path=db, now="12:00")

    print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
