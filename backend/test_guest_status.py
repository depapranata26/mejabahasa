#!/usr/bin/env python3
"""HTTP test for the guest live order status endpoint (no model calls).

Starts the server in-process on an ephemeral port against an isolated
temp database, then walks one order end-to-end and checks
GET /api/orders/<id> at every step: draft (no ticket) -> acc_pending ->
ticket open -> cooking -> ready -> served (order done). Also checks
404 for an unknown id and 400 for a non-numeric id.

Usage: python3 backend/test_guest_status.py   (from the repo root)
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


def get_order(base, oid):
    status, body = req("GET", base, f"/api/orders/{oid}")
    assert status == 200, f"GET /api/orders/{oid} -> {status}: {body}"
    return body["order"]


def post(base, path, body=None):
    status, resp = req("POST", base, path, body)
    assert status == 200, f"POST {path} -> {status}: {resp}"
    return resp


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-status-"))
    db = str(tmp / "test.db")
    seed.create_db(db)
    httpd = server.make_server(0, db)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        print("[1] detail order di setiap tahap alur")
        o = post(base, "/api/orders",
                 {"table_no": "B7", "language": "id",
                  "items": [{"menu_item_id": 1, "qty": 1}]})
        oid = o["order_id"]
        d = get_order(base, oid)
        check("draft: status draft, tanpa tiket",
              d["status"] == "draft" and d["ticket_status"] is None, str(d))
        post(base, f"/api/orders/{oid}/confirm")
        post(base, f"/api/orders/{oid}/request_acc")
        d = get_order(base, oid)
        check("acc_pending: status acc_pending, tanpa tiket",
              d["status"] == "acc_pending" and d["ticket_status"] is None)
        post(base, f"/api/orders/{oid}/acc", {"accepted": True})
        post(base, f"/api/orders/{oid}/ticket")
        d = get_order(base, oid)
        check("tiket terkirim: sent_to_kitchen + tiket open",
              d["status"] == "sent_to_kitchen" and d["ticket_status"] == "open",
              str(d))
        tid = req("GET", base, "/api/tickets")[1]["tickets"][0]["ticket_id"]
        for nxt, label in [("cooking", "cooking"),
                           ("ready", "ready"),
                           ("served", "served")]:
            post(base, f"/api/tickets/{tid}/status", {"status": nxt})
            d = get_order(base, oid)
            check(f"tiket {label}: ticket_status ikut berubah",
                  d["ticket_status"] == label, str(d))
        check("setelah served: order done",
              d["status"] == "done", str(d))

        print("[2] order ditolak staf terlihat tamu")
        o2 = post(base, "/api/orders",
                  {"table_no": "B8", "language": "id",
                   "items": [{"menu_item_id": 9, "qty": 1}]})
        oid2 = o2["order_id"]
        post(base, f"/api/orders/{oid2}/confirm")
        post(base, f"/api/orders/{oid2}/request_acc")
        post(base, f"/api/orders/{oid2}/acc", {"accepted": False})
        d = get_order(base, oid2)
        check("acc_rejected terlihat di detail",
              d["status"] == "acc_rejected" and d["ticket_status"] is None)

        print("[3] id bermasalah")
        status, body = req("GET", base, "/api/orders/99999")
        check("id tak dikenal -> 404", status == 404, str(body))
        status, body = req("GET", base, "/api/orders/bukan-angka")
        check("id bukan angka -> 400", status == 400, str(body))

        print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
