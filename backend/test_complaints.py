#!/usr/bin/env python3
"""Unit test for backend/complaints.py (no model calls, no HTTP).

Covers: deterministic urgency classification, creation validation,
forward-only status transitions, and staff queue ordering (urgent first,
oldest first within the same urgency).

Usage: python3 backend/test_complaints.py   (from the repo root)
Exit code 0 only if all checks pass.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import complaints, seed  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    PASSED.append(name)
    print(f"  PASS {name}" + (f" — {detail}" if detail else ""))


def expect_value_error(name, fn):
    try:
        fn()
    except ValueError as e:
        check(name, True, str(e))
        return
    check(name, False, "tidak menolak")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-complaints-"))
    db = str(tmp / "test.db")
    seed.create_db(db)

    print("[1] klasifikasi urgensi deterministik")
    check("alergi -> tinggi",
          complaints.classify_urgency("Saya alergi kacang!") == "tinggi")
    check("ALERGI kapital -> tinggi",
          complaints.classify_urgency("ADA RAMBUT di makanan") == "tinggi")
    check("makanan basi -> tinggi",
          complaints.classify_urgency("nasinya basi") == "tinggi")
    check("food poisoning EN -> tinggi",
          complaints.classify_urgency("food poisoning") == "tinggi")
    check("lama -> sedang",
          complaints.classify_urgency("pesanan lama sekali") == "sedang")
    check("salah pesan -> sedang",
          complaints.classify_urgency("pesanannya salah") == "sedang")
    check("cold food EN -> sedang",
          complaints.classify_urgency("the food is cold") == "sedang")
    check("umum -> rendah",
          complaints.classify_urgency("musiknya terlalu keras") == "rendah")
    check("tinggi menang atas sedang",
          complaints.classify_urgency("lama dan ada rambut") == "tinggi")

    print("[2] pembuatan keluhan + validasi")
    c1 = complaints.create_complaint("B7", "Ada rambut di makanan",
                                      db_path=db)
    check("buat keluhan -> id + tinggi + open",
          c1["urgency"] == "tinggi" and c1["status"] == "open"
          and isinstance(c1["id"], int), str(c1))
    expect_value_error("pesan kosong ditolak",
                       lambda: complaints.create_complaint(
                           "B7", "   ", db_path=db))
    expect_value_error("meja kosong ditolak",
                       lambda: complaints.create_complaint(
                           "", "lama", db_path=db))
    expect_value_error("order_id tak dikenal ditolak",
                       lambda: complaints.create_complaint(
                           "B7", "lama", order_id=9999, db_path=db))
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO orders (table_no, language, status)"
                 " VALUES ('B7', 'id', 'done')")
    conn.commit()
    oid = conn.execute("SELECT MAX(id) FROM orders").fetchone()[0]
    conn.close()
    c2 = complaints.create_complaint("B7", "pesanan lama", order_id=oid,
                                      db_path=db)
    check("keluhan terikat order yang ada",
          c2["id"] != c1["id"] and complaints.get_complaint(
              c2["id"], db)["order_id"] == oid)

    print("[3] transisi status hanya maju satu langkah")
    expect_value_error("open -> resolved (loncat) ditolak",
                       lambda: complaints.set_complaint_status(
                           c1["id"], "resolved", db_path=db))
    expect_value_error("status tak dikenal ditolak",
                       lambda: complaints.set_complaint_status(
                           c1["id"], "closed", db_path=db))
    expect_value_error("id tak dikenal ditolak",
                       lambda: complaints.set_complaint_status(
                           9999, "handling", db_path=db))
    r = complaints.set_complaint_status(c1["id"], "handling", db_path=db)
    check("open -> handling", r["status"] == "handling")
    expect_value_error("handling -> open (mundur) ditolak",
                       lambda: complaints.set_complaint_status(
                           c1["id"], "open", db_path=db))
    r = complaints.set_complaint_status(c1["id"], "resolved", db_path=db)
    check("handling -> resolved", r["status"] == "resolved")
    expect_value_error("resolved -> handling ditolak",
                       lambda: complaints.set_complaint_status(
                           c1["id"], "handling", db_path=db))

    print("[4] antrean staf: urgensi dulu, tertua dulu")
    complaints.create_complaint("C1", "musiknya terlalu keras", db_path=db)
    complaints.create_complaint("C2", "makanan kotor", db_path=db)
    queue = complaints.list_complaints(db)
    urgencies = [c["urgency"] for c in queue]
    check("tinggi di depan sedang di depan rendah",
          urgencies == sorted(urgencies,
                              key={"tinggi": 0, "sedang": 1,
                                   "rendah": 2}.get),
          str(urgencies))
    ids_tinggi = [c["id"] for c in queue if c["urgency"] == "tinggi"]
    check("dalam urgensi sama, id menaik (tertua dulu)",
          ids_tinggi == sorted(ids_tinggi), str(ids_tinggi))

    print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
