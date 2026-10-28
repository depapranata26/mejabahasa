#!/usr/bin/env python3
"""Complaint tracker (masalah restoran #10): tamu lapor, staf triase.

Urgency is classified DETERMINISTICALLY from keywords at creation time
— never guessed by the model, so a food-safety complaint can never be
silently downgraded. Staff only moves the status forward:
  open -> handling -> resolved
Any other transition (including skipping a step) is rejected.
"""
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.config import DATABASE_PATH  # noqa: E402

HIGH_KEYWORDS = (
    "alergi", "allergy", "keracunan", "poisoning", "muntah", "vomit",
    "kotor", "dirty", "rambut", "hair", "serangga", "insect",
    "kecoa", "cockroach", "lalat", "basi", "mentah", "sakit", "sick",
)
MEDIUM_KEYWORDS = (
    "lama", "slow", "dingin", "cold", "salah", "wrong", "kurang",
    "asin", "pedas", "spicy", "lambat", "tunggu", "wait", "antre",
    "queue", "kasar", "rude",
)
VALID_STATUS = ("open", "handling", "resolved")
_TRANSITIONS = {"open": "handling", "handling": "resolved"}


def classify_urgency(message):
    """Keyword rules over the lowercased message (deterministic)."""
    text = (message or "").lower()
    if any(k in text for k in HIGH_KEYWORDS):
        return "tinggi"
    if any(k in text for k in MEDIUM_KEYWORDS):
        return "sedang"
    return "rendah"


def _connect(db_path):
    conn = sqlite3.connect(db_path or DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_complaint(table_no, message, order_id=None, db_path=None):
    """Store a complaint; returns {id, urgency, status: 'open'}."""
    if not table_no or not isinstance(table_no, str):
        raise ValueError("Nomor meja wajib diisi (string, mis. 'A12').")
    if not message or not isinstance(message, str) or not message.strip():
        raise ValueError("Isi keluhan tidak boleh kosong.")
    conn = _connect(db_path)
    try:
        if order_id is not None:
            row = conn.execute(
                "SELECT id FROM orders WHERE id = ?", (order_id,)).fetchone()
            if row is None:
                raise ValueError(f"Order {order_id} tidak ditemukan.")
        urgency = classify_urgency(message)
        cur = conn.execute(
            "INSERT INTO complaints (table_no, order_id, message, urgency)"
            " VALUES (?, ?, ?, ?)",
            (table_no, order_id, message.strip(), urgency))
        conn.commit()
        return {"id": cur.lastrowid, "urgency": urgency, "status": "open"}
    finally:
        conn.close()


def list_complaints(db_path=None):
    """Staff queue: urgent first, oldest first within the same urgency."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM complaints ORDER BY"
            " CASE urgency WHEN 'tinggi' THEN 0"
            " WHEN 'sedang' THEN 1 ELSE 2 END, id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_complaint(complaint_id, db_path=None):
    """One complaint as a dict, or None when the id does not exist."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def set_complaint_status(complaint_id, status, db_path=None):
    """Move a complaint exactly one step forward, or reject."""
    if status not in VALID_STATUS:
        raise ValueError(
            f"Status keluhan '{status}' tidak dikenal. "
            f"Pilihan: {', '.join(VALID_STATUS)}.")
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if row is None:
            raise ValueError(f"Keluhan {complaint_id} tidak ditemukan.")
        expected = _TRANSITIONS.get(row["status"])
        if status != expected:
            raise ValueError(
                f"Tidak bisa {status}: keluhan {complaint_id} berstatus "
                f"'{row['status']}', harus '{expected}'.")
        conn.execute(
            "UPDATE complaints SET status = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (status, complaint_id))
        conn.commit()
        return {"id": complaint_id, "status": status}
    finally:
        conn.close()
