#!/usr/bin/env python3
"""Staff menu admin (masalah restoran #7, lanjutan): staf bisa menandai
item habis, restock, dan mengubah harga dari dashboard — tanpa menyentuh
database langsung. Nilai divalidasi di sini, bukan di browser.
"""
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.config import DATABASE_PATH  # noqa: E402


def _connect(db_path):
    conn = sqlite3.connect(db_path or DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_menu_row(conn, menu_id):
    row = conn.execute(
        "SELECT * FROM menu_items WHERE id = ?", (menu_id,)).fetchone()
    if row is None:
        raise ValueError(f"Item menu id {menu_id} tidak ada di database.")
    return row


def _int_value(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} harus bilangan bulat, bukan {value!r}.")
    return value


def set_stock(menu_id, stock, db_path=None):
    """Set stock to an exact value (>= 0)."""
    stock = _int_value(stock, "stok")
    if stock < 0:
        raise ValueError(f"Stok tidak boleh negatif, bukan {stock}.")
    conn = _connect(db_path)
    try:
        _get_menu_row(conn, menu_id)
        conn.execute(
            "UPDATE menu_items SET stock = ? WHERE id = ?", (stock, menu_id))
        conn.commit()
        return {"id": menu_id, "stock": stock}
    finally:
        conn.close()


def adjust_stock(menu_id, delta, db_path=None):
    """Shift stock by delta (floor at 0); used by the '+10' button."""
    delta = _int_value(delta, "delta stok")
    conn = _connect(db_path)
    try:
        row = _get_menu_row(conn, menu_id)
        new_stock = max(0, row["stock"] + delta)
        conn.execute(
            "UPDATE menu_items SET stock = ? WHERE id = ?",
            (new_stock, menu_id))
        conn.commit()
        return {"id": menu_id, "stock": new_stock}
    finally:
        conn.close()


def set_price(menu_id, price_idr, db_path=None):
    """Set price (> 0 rupiah)."""
    price_idr = _int_value(price_idr, "harga")
    if price_idr <= 0:
        raise ValueError(f"Harga harus > 0, bukan {price_idr}.")
    conn = _connect(db_path)
    try:
        _get_menu_row(conn, menu_id)
        conn.execute(
            "UPDATE menu_items SET price_idr = ? WHERE id = ?",
            (price_idr, menu_id))
        conn.commit()
        return {"id": menu_id, "price_idr": price_idr}
    finally:
        conn.close()
