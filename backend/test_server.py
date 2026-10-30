#!/usr/bin/env python3
"""Smoke test for backend/server.py (no model calls).

Starts the server in-process on an ephemeral port against an isolated
temp database, then: 3 pages return 200, menu lists 14 items, and one
full order flows end-to-end over HTTP: draft -> confirmed -> acc_pending
-> acc_accepted -> ticket open -> cooking -> ready -> served -> order done.

Usage: python3 backend/test_server.py   (from the repo root)
Exit code 0 = all checks passed.
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


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-server-"))
    db = str(tmp / "test.db")
    seed.create_db(db)
    httpd = server.make_server(0, db)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        print("[1] halaman-halaman merespons 200")
        for path, needle in [("/", "MejaBahasa"), ("/staff", "Dashboard"),
                             ("/kitchen", "Dapur"),
                             ("/koordinator", "Koordinator"),
                             ("/owner", "Omzet")]:
            status, body = req("GET", base, path)
            check(f"GET {path} 200 + memuat '{needle}'",
                  status == 200 and needle in body, f"status={status}")

        print("[2] API menu")
        status, menu = req("GET", base, "/api/menu")
        check("GET /api/menu 14 item",
              status == 200 and len(menu["items"]) == 14,
              f"n={len(menu.get('items', []))}")

        print("[3] alur order end-to-end lewat HTTP")
        status, o = req("POST", base, "/api/orders",
                        {"table_no": "A12", "language": "id",
                         "items": [{"menu_item_id": 1, "qty": 1}]})
        check("POST /api/orders draft",
              status == 200 and o["status"] == "draft", str(o))
        oid = o["order_id"]
        status, o = req("POST", base, f"/api/orders/{oid}/confirm")
        check("confirm -> confirmed",
              status == 200 and o["status"] == "confirmed", str(o))
        status, o = req("POST", base, f"/api/orders/{oid}/request_acc")
        check("request_acc -> acc_pending",
              status == 200 and o["status"] == "acc_pending", str(o))
        status, o = req("POST", base, f"/api/orders/{oid}/acc",
                        {"accepted": True})
        check("ACC terima -> acc_accepted",
              status == 200 and o["status"] == "acc_accepted", str(o))
        status, t = req("POST", base, f"/api/orders/{oid}/ticket")
        check("ticket -> open",
              status == 200 and t["status"] == "open", str(t))
        tid = t["ticket_id"]
        for st in ("cooking", "ready", "served"):
            status, t = req("POST", base, f"/api/tickets/{tid}/status",
                            {"status": st})
            check(f"tiket -> {st}",
                  status == 200 and t["status"] == st, str(t))
        status, orders = req("GET", base, "/api/orders")
        mine = [x for x in orders["orders"] if x["id"] == oid][0]
        check("order selesai (done) setelah tiket disajikan",
              mine["status"] == "done", mine["status"])

        print("[4] dashboard owner + angka report")
        status, page = req("GET", base, "/owner")
        check("GET /owner 200 + memuat angka omzet",
              status == 200 and "Omzet" in page and "Rp" in page,
              f"status={status}")
        status, rep = req("GET", base, "/api/report")
        check("GET /api/report total_orders>=1, revenue>0",
              status == 200 and rep["total_orders"] >= 1
              and rep["revenue_idr"] > 0,
              f"total={rep.get('total_orders')} "
              f"revenue={rep.get('revenue_idr')}")
    finally:
        httpd.shutdown()
        httpd.server_close()

    print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
