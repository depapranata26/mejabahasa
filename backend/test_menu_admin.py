#!/usr/bin/env python3
"""HTTP test for the staff menu admin endpoints (no model calls).

Starts the server in-process on an ephemeral port against an isolated
temp database, then: set stock to 0 -> agent order path refuses the item
(stock guard still enforced) -> restock +10 -> order works again; price
change is reflected in /api/menu; invalid values and unknown items are
rejected with 400.

Usage: python3 backend/test_menu_admin.py   (from the repo root)
Exit code 0 only if all checks pass.
"""
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import seed, server  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    PASSED.append(name)
    print(f"  PASS {name}" + (f" — {detail}" if detail else ""))


def req(method, base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def menu_item(base, item_id):
    _, menu = req("GET", base, "/api/menu")
    for it in menu["items"]:
        if it["id"] == item_id:
            return it
    raise AssertionError(f"item {item_id} tidak ada di menu")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-menuadmin-"))
    db = str(tmp / "test.db")
    seed.create_db(db)
    httpd = server.make_server(0, db)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        print("[1] tandai habis -> order ditolak -> restock -> order jalan")
        before = menu_item(base, 1)
        check("stok awal item 1 > 0", before["stock"] > 0,
              f"stok={before['stock']}")
        status, r = req("POST", base, "/api/menu/1/stock", {"stock": 0})
        check("POST stock 0 -> ok", status == 200 and r["stock"] == 0,
              str(r))
        check("menu menampilkan stok 0", menu_item(base, 1)["stock"] == 0)
        status, r = req("POST", base, "/api/orders",
                        {"table_no": "B7", "language": "id",
                         "items": [{"menu_item_id": 1, "qty": 1}]})
        check("order item habis ditolak (400)",
              status == 400 and r.get("ok") is False, f"{status}: {r}")
        status, r = req("POST", base, "/api/menu/1/stock", {"delta": 10})
        check("restock +10 -> stok 10",
              status == 200 and r["stock"] == 10, str(r))
        status, r = req("POST", base, "/api/orders",
                        {"table_no": "B7", "language": "id",
                         "items": [{"menu_item_id": 1, "qty": 1}]})
        check("order jalan lagi setelah restock",
              status == 200 and r.get("ok") is True, f"{status}: {r}")

        print("[2] ubah harga tercermin di menu")
        status, r = req("POST", base, "/api/menu/9/price",
                        {"price_idr": 15000})
        check("POST price 15000 -> ok",
              status == 200 and r["price_idr"] == 15000, str(r))
        check("menu menampilkan harga baru",
              menu_item(base, 9)["price_idr"] == 15000)

        print("[3] nilai tidak valid ditolak")
        for path, body, label in [
                ("/api/menu/1/stock", {"stock": -1}, "stok negatif"),
                ("/api/menu/1/stock", {"stock": "banyak"}, "stok bukan angka"),
                ("/api/menu/1/price", {"price_idr": 0}, "harga nol"),
                ("/api/menu/1/price", {"price_idr": -5000}, "harga negatif"),
                ("/api/menu/999/stock", {"stock": 5}, "item tak dikenal"),
                ("/api/menu/999/price", {"price_idr": 1000},
                 "item tak dikenal (harga)"),
                ("/api/menu/1/rasa", {"stock": 5}, "aksi tak dikenal")]:
            status, r = req("POST", base, path, body)
            check(f"{label} -> 400/404",
                  status in (400, 404), f"{status}: {r}")

        print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
