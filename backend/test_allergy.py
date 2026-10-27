#!/usr/bin/env python3
"""Allergy/diet/custom-order tests for MejaBahasa PRD v0.5 (no model calls).

Checks, against an isolated temp database:
  (a) ordering Gado-Gado with allergy note "kacang" is REJECTED with a
      message naming the allergen (never silently passed through);
  (b) ordering Nasi Goreng with allergy note "kacang" SUCCEEDS
      (no peanut in it);
  (c) custom_note + allergy_notes are recorded and appear on the kitchen
      ticket text, with the allergy section at the very TOP;
  (d) get_menu exposes the new fields (ingredients, allergens,
      spice_level, is_vegetarian) with correct values.

Usage: python3 backend/test_allergy.py   (from the repo root)
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
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-allergy-"))
    db = str(tmp / "test.db")
    print(f"[1] seed -> {db}")
    count = seed.create_db(db)
    check("seed 14 item fiktif", count == 14, f"{count} item")

    print("[2] get_menu mengembalikan field baru")
    menu = {it["id"]: it for it in tools.get_menu(db_path=db)}
    gado = menu[5]
    check("Gado-Gado mengandung kacang tanah",
          "kacang tanah" in gado["allergens"], str(gado["allergens"]))
    check("Gado-Gado mengandung telur (telur rebus)",
          "telur" in gado["allergens"])
    check("Gado-Gado vegetarian + pedas sedang",
          gado["is_vegetarian"] is True and gado["spice_level"] == "sedang")
    check("Capcay Seafood mengandung udang + cumi",
          "udang" in menu[8]["allergens"]
          and "cumi" in menu[8]["allergens"], str(menu[8]["allergens"]))
    check("Jus Alpukat mengandung susu",
          "susu" in menu[11]["allergens"])
    check("Rendang Sapi mengandung kelapa (santan)",
          "kelapa" in menu[4]["allergens"])
    check("Nasi Goreng TIDAK mengandung kacang",
          "kacang tanah" not in menu[1]["allergens"],
          str(menu[1]["allergens"]))

    print("[3] (a) order Gado-Gado + alergi 'kacang' DITOLAK")
    err = expect_error(
        "Gado-Gado + alergi kacang ditolak",
        tools.create_order, "A12",
        [{"menu_item_id": 5, "qty": 1}], "id", db_path=db,
        allergy_notes="kacang")
    check("pesan penolakan menyebut 'kacang'",
          "kacang" in err.lower(), err)
    check("pesan penolakan menyebut nama item",
          "Gado-Gado" in err, err)

    print("[4] (b) order Nasi Goreng + alergi 'kacang' LOLOS")
    order = tools.create_order(
        "A12", [{"menu_item_id": 1, "qty": 1}], "id", db_path=db,
        allergy_notes="kacang")
    check("draft Nasi Goreng dibuat", order["status"] == "draft",
          f"order_id={order['order_id']}")

    print("[5] (c) custom_note + allergy_notes tercatat di tiket dapur")
    order = tools.create_order(
        "B07",
        [{"menu_item_id": 1, "qty": 1, "custom_note": "kurang pedas"},
         {"menu_item_id": 9, "qty": 2, "custom_note": "tanpa gula"}],
        "id", db_path=db,
        allergy_notes="alergi susu", dietary_notes="tidak makan pedas")
    oid = order["order_id"]
    tools.confirm_order(oid, db_path=db)
    tools.request_acc(oid, db_path=db)
    tools.decide_acc(oid, True, db_path=db)
    ticket = tools.send_kitchen_ticket(oid, db_path=db)
    check("tiket dibuat", ticket["status"] == "open",
          f"ticket_id={ticket['ticket_id']}")
    check("tiket menyimpan allergy_notes",
          ticket["allergy_notes"] == "alergi susu")
    text = ticket["text"]
    pos_alergi = text.find("ALERGI")
    pos_item = text.find("Nasi Goreng")
    check("teks tiket: alergi di BAGIAN ATAS (sebelum item)",
          0 <= pos_alergi < pos_item,
          f"pos_alergi={pos_alergi}, pos_item={pos_item}")
    check("teks tiket memuat custom_note 'kurang pedas'",
          "kurang pedas" in text)
    check("teks tiket memuat custom_note 'tanpa gula'",
          "tanpa gula" in text)
    check("teks tiket memuat pantangan diet",
          "tidak makan pedas" in text)

    print("[6] alergi lain: Capcay + 'seafood' ditolak (alias)")
    expect_error("Capcay + alergi seafood ditolak",
                 tools.create_order, "A12",
                 [{"menu_item_id": 8, "qty": 1}], "id", db_path=db,
                 allergy_notes="seafood")

    print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
