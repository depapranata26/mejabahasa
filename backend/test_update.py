#!/usr/bin/env python3
"""Order revision + partial negotiation tests (no model calls).

Checks, against an isolated temp database:
  (a) revising a draft replaces its items and recomputes the total;
  (b) revising a non-draft order is rejected (state machine locked);
  (c) over-stock revision errors carry the available quantity AND
      same-category alternatives (negotiation data, not a bare refusal);
  (d) allergy conflicts on revision are rejected, naming item + allergen;
  (e) custom notes survive a revision.

Usage: python3 backend/test_update.py   (from the repo root)
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
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-update-"))
    db = str(tmp / "test.db")
    print(f"[1] seed -> {db}")
    count = seed.create_db(db)
    check("seed 14 item fiktif", count == 14, f"{count} item")

    print("[2] (a) revisi draft mengganti item + total dihitung ulang")
    order = tools.create_order(
        "A12",
        [{"menu_item_id": 1, "qty": 1},
         {"menu_item_id": 9, "qty": 1}],
        "id", db_path=db)
    oid = order["order_id"]
    check("draft awal total 57000", order["total_idr"] == 57000,
          f"total={order['total_idr']}")
    r = tools.update_order(
        oid,
        [{"menu_item_id": 1, "qty": 2, "custom_note": "kurang pedas"},
         {"menu_item_id": 11, "qty": 1}],
        db_path=db)
    check("revisi tetap draft", r["status"] == "draft")
    check("total baru 2x45000 + 28000 = 118000",
          r["total_idr"] == 118000, f"total={r['total_idr']}")
    check("Es Teh (id 9) hilang dari order",
          all(i["menu_item_id"] != 9 for i in r["items"]), str(r["items"]))
    check("custom_note tercatat di revisi",
          any(i["custom_note"] == "kurang pedas" for i in r["items"]))

    print("[3] (b) revisi order non-draft ditolak")
    tools.confirm_order(oid, db_path=db)
    expect_error("revisi order confirmed ditolak",
                 tools.update_order, oid,
                 [{"menu_item_id": 1, "qty": 1}], db_path=db)

    print("[4] (c) over-stock membawa data negosiasi")
    order2 = tools.create_order(
        "A13", [{"menu_item_id": 1, "qty": 1}], "id", db_path=db)
    err = expect_error(
        "revisi qty 99 Rendang ditolak",
        tools.update_order, order2["order_id"],
        [{"menu_item_id": 4, "qty": 99}], db_path=db)
    check("pesan menyebut stok tersedia (10)",
          "tersedia 10" in err, err)
    check("pesan menyebut alternatif se-kategori",
          "Nasi Goreng Spesial" in err, err)
    check("pesan menawarkan opsi (porsi tersedia / alternatif / batal)",
          "ganti alternatif" in err and "batal" in err, err)

    print("[5] (d) konflik alergi pada revisi ditolak")
    err = expect_error(
        "revisi ke Gado-Gado + alergi kacang ditolak",
        tools.update_order, order2["order_id"],
        [{"menu_item_id": 5, "qty": 1}], db_path=db,
        allergy_notes="kacang")
    check("pesan menyebut alergen kacang",
          "kacang" in err.lower(), err)
    check("pesan menyebut nama item",
          "Gado-Gado" in err, err)

    print("[6] create_order over-stock juga membawa data negosiasi")
    err = expect_error(
        "create_order qty 99 Rendang ditolak dengan alternatif",
        tools.create_order, "A14",
        [{"menu_item_id": 4, "qty": 99}], "id", db_path=db)
    check("pesan create_order menyebut stok tersedia + alternatif",
          "tersedia 10" in err and "Nasi Goreng Spesial" in err, err)

    print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
