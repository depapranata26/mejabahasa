#!/usr/bin/env python3
"""Tests for the guest wait estimate (masalah restoran #9), now computed
from the coordinator's PARALLEL station plan (not the old sequential
model): dapur/bar tasks run on their own members, so a bar order never
waits for a dapur ticket ahead of it.

Roster (demo): dapur = Juru Masak 1/2, bar = Barista.
Prep (demo/menu.json): Nasi Goreng 15, Es Teh 3, Mie Goreng 12.

Usage: python3 backend/test_queue_estimate.py   (from the repo root)
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

from backend import agent, queue_estimate, seed, server  # noqa: E402

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


def make_ticket(base, oid):
    post(base, f"/api/orders/{oid}/confirm")
    post(base, f"/api/orders/{oid}/request_acc")
    post(base, f"/api/orders/{oid}/acc", {"accepted": True})
    post(base, f"/api/orders/{oid}/ticket")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-queue-"))
    db = str(tmp / "test.db")
    seed.create_db(db)

    print("[1] estimator modul: antrean kosong + fallback prep")
    e = queue_estimate.estimate_new_order(
        db, [{"menu_item_id": 1, "qty": 1}])
    check("antrean kosong: hanya prep sendiri (15, dapur)",
          e["estimated_minutes"] == 15
          and e["bottleneck_station"] == "dapur"
          and e["own_prep_minutes"] == 15, str(e))
    b = queue_estimate.estimate_backlog(db)
    check("backlog kosong: 0 tiket, 0 menit",
          b["unfinished_tickets"] == 0 and b["backlog_minutes"] == 0, str(b))
    menu = {"items": [{"id": 99, "name": "Tanpa Prep"}]}
    mpath = tmp / "menu-noprep.json"
    mpath.write_text(json.dumps(menu), encoding="utf-8")
    e2 = queue_estimate.estimate_new_order(
        db, [{"menu_item_id": 99, "qty": 2}], menu_path=str(mpath))
    check("item tanpa prep_minutes -> default 10/menit",
          e2["own_prep_minutes"] == 20, str(e2))
    check("order tak dikenal -> None",
          queue_estimate.estimate_for_order(db, 99999) is None)
    d = agent._TOOL_DISPATCH["estimate_wait"]({"order_id": 99999}, db)
    check("tool dispatch order tak dikenal -> ok False",
          d.get("ok") is False, str(d))
    d2 = agent._TOOL_DISPATCH["estimate_wait"]({}, db)
    check("tool dispatch tanpa argumen -> backlog",
          d2.get("ok") is True and "backlog_minutes" in d2, str(d2))

    print("[2] endpoint + antrean paralel via server in-process")
    httpd = server.make_server(0, db)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        oa = post(base, "/api/orders",
                  {"table_no": "B7", "language": "id",
                   "items": [{"menu_item_id": 1, "qty": 1}]})["order_id"]
        make_ticket(base, oa)
        d = get_order(base, oa)
        check("tiket dapur pertama: estimasi 15 (dapur)",
              d["estimate"]["estimated_minutes"] == 15
              and d["estimate"]["bottleneck_station"] == "dapur",
              str(d["estimate"]))
        ob = post(base, "/api/orders",
                  {"table_no": "B8", "language": "id",
                   "items": [{"menu_item_id": 9, "qty": 2}]})["order_id"]
        make_ticket(base, ob)
        d = get_order(base, ob)
        check("tiket bar TIDAK menunggu dapur: estimasi 6 (bar), bukan 21",
              d["estimate"]["estimated_minutes"] == 6
              and d["estimate"]["bottleneck_station"] == "bar",
              str(d["estimate"]))
        oc = post(base, "/api/orders",
                  {"table_no": "B9", "language": "id",
                   "items": [{"menu_item_id": 2, "qty": 1}]})["order_id"]
        d = get_order(base, oc)
        check("draf tanpa tiket: hipotesis di akhir antrean dapur (JM2 bebas) = 12",
              d["estimate"]["estimated_minutes"] == 12
              and d["estimate"]["bottleneck_station"] == "dapur",
              str(d["estimate"]))
        make_ticket(base, oc)
        d = get_order(base, oc)
        check("tiket dapur kedua PARALEL di JM2: target 12, bukan 27 sekuensial",
              d["estimate"]["estimated_minutes"] == 12, str(d["estimate"]))
        tickets = req("GET", base, "/api/tickets")[1]["tickets"]
        tid_a = [t for t in tickets if t["order_id"] == oa][0]["ticket_id"]
        tid_b = [t for t in tickets if t["order_id"] == ob][0]["ticket_id"]
        post(base, f"/api/tickets/{tid_a}/status", {"status": "cooking"})
        d = get_order(base, ob)
        check("tiket dapur cooking tidak mengubah estimasi bar (6)",
              d["estimate"]["estimated_minutes"] == 6, str(d["estimate"]))
        post(base, f"/api/tickets/{tid_a}/status", {"status": "ready"})
        d = get_order(base, oa)
        check("tiket ready -> estimasi 0",
              d["estimate"]["estimated_minutes"] == 0, str(d["estimate"]))
        post(base, f"/api/tickets/{tid_b}/status", {"status": "cooking"})
        post(base, f"/api/tickets/{tid_b}/status", {"status": "ready"})
        post(base, f"/api/tickets/{tid_b}/status", {"status": "served"})
        d = get_order(base, ob)
        check("order done -> estimasi 0",
              d["estimate"]["estimated_minutes"] == 0
              and d["status"] == "done", str(d["estimate"]))
        od = post(base, "/api/orders",
                  {"table_no": "B10", "language": "id",
                   "items": [{"menu_item_id": 9, "qty": 1}]})["order_id"]
        post(base, f"/api/orders/{od}/confirm")
        post(base, f"/api/orders/{od}/request_acc")
        post(base, f"/api/orders/{od}/acc", {"accepted": False})
        d = get_order(base, od)
        check("order ditolak -> estimasi None",
              d["estimate"]["estimated_minutes"] is None, str(d["estimate"]))
        status, html = req("GET", base, "/")
        check("halaman tamu memuat kode tampil estimasi",
              status == 200 and "Perkiraan siap" in html)
        print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
