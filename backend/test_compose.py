#!/usr/bin/env python3
"""Budget meal composer tests for MejaBahasa (no model calls).

Checks, against an isolated temp database:
  (a) budget Rp100.000 + allergy "kacang" -> a combination UNDER budget,
      with no peanut allergen, total equal to the sum of DB prices, and
      close to the budget (maximizing, not cheapest-picking);
  (b) budget too small -> empty combination, total 0;
  (c) dietary note "vegetarian" -> every item vegetarian;
  (d) invalid budget -> rejected.

Usage: python3 backend/test_compose.py   (from the repo root)
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
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-compose-"))
    db = str(tmp / "test.db")
    print(f"[1] seed -> {db}")
    count = seed.create_db(db)
    check("seed 14 item fiktif", count == 14, f"{count} item")
    menu = {it["id"]: it for it in tools.get_menu(db_path=db)}

    print("[2] (a) budget Rp100.000 + alergi kacang")
    r = tools.compose_meal(100000, allergy_notes="kacang", db_path=db)
    print(f"  kombinasi: {[(i['name'], i['price_idr']) for i in r['items']]}"
          f" total={r['total_idr']}")
    check("kombinasi tidak kosong", len(r["items"]) >= 2,
          f"{len(r['items'])} item")
    check("total TIDAK MELEBIHI budget", 0 < r["total_idr"] <= 100000,
          f"total={r['total_idr']}")
    check("total memaksimalkan (dekat budget, >= 90000)",
          r["total_idr"] >= 90000, f"total={r['total_idr']}")
    check("total = jumlah harga DB",
          r["total_idr"] == sum(i["price_idr"] for i in r["items"]))
    check("tidak ada item mengandung kacang tanah",
          all("kacang tanah" not in menu[i["id"]]["allergens"]
              for i in r["items"]))
    check("sisa = budget - total",
          r["remaining_idr"] == 100000 - r["total_idr"],
          f"sisa={r['remaining_idr']}")

    print("[3] (b) budget terlalu kecil")
    r = tools.compose_meal(5000, db_path=db)
    check("budget 5000 -> kombinasi kosong",
          r["items"] == [] and r["total_idr"] == 0, str(r))

    print("[4] (c) pantangan vegetarian")
    r = tools.compose_meal(100000, dietary_notes="vegetarian", db_path=db)
    check("semua item vegetarian",
          all(menu[i["id"]]["is_vegetarian"] for i in r["items"]),
          str([(i["name"]) for i in r["items"]]))
    check("total vegetarian TIDAK MELEBIHI budget",
          0 < r["total_idr"] <= 100000, f"total={r['total_idr']}")

    print("[5] (d) budget tidak valid ditolak")
    expect_error("budget 0 ditolak",
                 tools.compose_meal, 0, db_path=db)
    expect_error("budget negatif ditolak",
                 tools.compose_meal, -1000, db_path=db)

    print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
