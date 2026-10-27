#!/usr/bin/env python3
"""Operational report for MejaBahasa: computed from the database, never
invented.

daily_report(db_path) returns:
  total_orders, orders_by_status,
  revenue_idr (orders with tickets actually sent: sent_to_kitchen + done),
  top_items (by quantity sold on ticketed orders),
  tickets_by_status, low_stock (stock <= threshold, default 10).
"""
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.config import DATABASE_PATH  # noqa: E402

LOW_STOCK_THRESHOLD = 10
REVENUE_STATUSES = ("sent_to_kitchen", "done")


def _conn(db_path):
    conn = sqlite3.connect(db_path or DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def daily_report(db_path=None, low_stock_threshold=LOW_STOCK_THRESHOLD):
    conn = _conn(db_path)
    try:
        total_orders = conn.execute(
            "SELECT COUNT(*) FROM orders").fetchone()[0]
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM orders"
            " GROUP BY status").fetchall()}
        placeholders = ",".join("?" for _ in REVENUE_STATUSES)
        revenue = conn.execute(
            "SELECT COALESCE(SUM(oi.qty * mi.price_idr), 0)"
            " FROM order_items oi"
            " JOIN orders o ON o.id = oi.order_id"
            " JOIN menu_items mi ON mi.id = oi.menu_item_id"
            f" WHERE o.status IN ({placeholders})",
            REVENUE_STATUSES).fetchone()[0]
        top = conn.execute(
            "SELECT mi.id, mi.name, SUM(oi.qty) AS sold"
            " FROM order_items oi"
            " JOIN orders o ON o.id = oi.order_id"
            " JOIN menu_items mi ON mi.id = oi.menu_item_id"
            f" WHERE o.status IN ({placeholders})"
            " GROUP BY mi.id ORDER BY sold DESC, mi.id LIMIT 5",
            REVENUE_STATUSES).fetchall()
        tickets = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM kitchen_tickets"
            " GROUP BY status").fetchall()}
        low = conn.execute(
            "SELECT id, name, stock FROM menu_items WHERE stock <= ?"
            " ORDER BY stock, id", (low_stock_threshold,)).fetchall()
        return {
            "total_orders": total_orders,
            "orders_by_status": by_status,
            "revenue_idr": revenue,
            "top_items": [{"id": r["id"], "name": r["name"],
                           "sold": r["sold"]} for r in top],
            "tickets_by_status": tickets,
            "low_stock": [{"id": r["id"], "name": r["name"],
                           "stock": r["stock"]} for r in low],
        }
    finally:
        conn.close()
