#!/usr/bin/env python3
"""Tests for the floor coordinator plan (koordinator lapangan).

Verifies the deterministic assignment: tickets split into station
tasks, each task to the least-loaded member (ties -> first in roster),
per-table targets from the bottleneck station, waiter deliveries for
ready tickets, and the /api/plan + /koordinator surfaces.

Roster (demo): dapur = Juru Masak 1/2, bar = Barista,
waiter = Waiter 1/2. Prep: Nasi Goreng 15, Rendang Sapi 20, Es Teh 3,
Mie Goreng 12.

Usage: python3 backend/test_coordinator.py   (from the repo root)
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

from backend import agent, coordinator, seed, server, tools  # noqa: E402

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


def post(base, path, body=None):
    status, resp = req("POST", base, path, body)
    assert status == 200, f"POST {path} -> {status}: {resp}"
    return resp


def ticket_for(base, table, items):
    oid = post(base, "/api/orders",
               {"table_no": table, "language": "id",
                "items": items})["order_id"]
    post(base, f"/api/orders/{oid}/confirm")
    post(base, f"/api/orders/{oid}/request_acc")
    post(base, f"/api/orders/{oid}/acc", {"accepted": True})
    post(base, f"/api/orders/{oid}/ticket")
    return oid


def member(plan, name):
    for m in plan["members"]:
        if m["member"] == name:
            return m
    raise AssertionError(f"member {name} missing")


def order(plan, oid):
    for o in plan["orders"]:
        if o["order_id"] == oid:
            return o
    raise AssertionError(f"order {oid} missing from plan")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mejabahasa-coord-"))
    db = str(tmp / "test.db")
    seed.create_db(db)

    print("[1] rencana kosong + determinisme")
    p = coordinator.build_plan(db)
    check("rencana kosong: 0 tiket, semua beban 0",
          p["unfinished_tickets"] == 0 and p["orders"] == []
          and all(m["load_minutes"] == 0 for m in p["members"]), str(p))
    p2 = coordinator.build_plan(db)
    check("rencana deterministik (dua kali bangun = identik)",
          json.dumps(p, sort_keys=True) == json.dumps(p2, sort_keys=True))

    print("[2] assignment + target via server in-process")
    httpd = server.make_server(0, db)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        oa = ticket_for(base, "B7", [{"menu_item_id": 1, "qty": 1}])
        ob = ticket_for(base, "B8", [{"menu_item_id": 4, "qty": 1},
                                     {"menu_item_id": 9, "qty": 1}])
        plan = req("GET", base, "/api/plan")[1]
        jm1 = member(plan, "Juru Masak 1")
        jm2 = member(plan, "Juru Masak 2")
        bar = member(plan, "Barista")
        check("JM1 dapat Nasi Goreng B7 0–15",
              len(jm1["tasks"]) == 1 and jm1["tasks"][0]["item"] == "Nasi Goreng Spesial"
              and jm1["tasks"][0]["start_minutes"] == 0
              and jm1["tasks"][0]["finish_minutes"] == 15, str(jm1))
        check("JM2 dapat Rendang B8 0–20 (dapur kedua, beban ringan)",
              len(jm2["tasks"]) == 1 and jm2["tasks"][0]["item"] == "Rendang Sapi"
              and jm2["tasks"][0]["finish_minutes"] == 20, str(jm2))
        check("Barista dapat Es Teh B8 0–3",
              len(bar["tasks"]) == 1 and bar["tasks"][0]["item"] == "Es Teh Manis"
              and bar["tasks"][0]["finish_minutes"] == 3, str(bar))
        check("target B7 = 15 (dapur), B8 = 20 (dapur bottleneck)",
              order(plan, oa)["target_minutes"] == 15
              and order(plan, ob)["target_minutes"] == 20
              and order(plan, ob)["bottleneck_station"] == "dapur",
              str(plan["orders"]))
        oc = ticket_for(base, "B9", [{"menu_item_id": 2, "qty": 1}])
        plan = req("GET", base, "/api/plan")[1]
        jm1 = member(plan, "Juru Masak 1")
        check("tiket dapur ketiga ke JM1 (15 < 20): tugas 15–27",
              len(jm1["tasks"]) == 2
              and jm1["tasks"][1]["item"] == "Mie Goreng Jawa"
              and jm1["tasks"][1]["start_minutes"] == 15
              and jm1["tasks"][1]["finish_minutes"] == 27, str(jm1))
        check("target B9 = 27",
              order(plan, oc)["target_minutes"] == 27,
              str(order(plan, oc)))
        tickets = req("GET", base, "/api/tickets")[1]["tickets"]
        tid_a = [t for t in tickets if t["order_id"] == oa][0]["ticket_id"]
        post(base, f"/api/tickets/{tid_a}/status", {"status": "cooking"})
        post(base, f"/api/tickets/{tid_a}/status", {"status": "ready"})
        plan = req("GET", base, "/api/plan")[1]
        w1 = member(plan, "Waiter 1")
        check("tiket ready -> tugas antar untuk Waiter 1 (0–3)",
              len(w1["tasks"]) == 1
              and w1["tasks"][0]["item"] == "Antar pesanan"
              and w1["tasks"][0]["table_no"] == "B7"
              and w1["tasks"][0]["finish_minutes"] == 3, str(w1))
        check("order ready: target 0, status ready",
              order(plan, oa)["target_minutes"] == 0
              and order(plan, oa)["ticket_status"] == "ready",
              str(order(plan, oa)))
        plan_b8 = req("GET", base, "/api/plan?table=B8")[1]
        check("filter ?table=B8: hanya order B8, target tetap queue-aware (20)",
              [o["order_id"] for o in plan_b8["orders"]] == [ob]
              and plan_b8["orders"][0]["target_minutes"] == 20,
              str(plan_b8["orders"]))
        status, html = req("GET", base, "/koordinator")
        check("halaman /koordinator 200 + memuat 'Koordinator'",
              status == 200 and "Koordinator" in html)
        d = agent._TOOL_DISPATCH["coordinator_plan"]({}, db)
        check("tool coordinator_plan -> ok + members + orders",
              d.get("ok") is True and "members" in d and "orders" in d,
              str(list(d.keys())))
        d2 = agent._TOOL_DISPATCH["coordinator_plan"]({"table_no": "B8"}, db)
        check("tool coordinator_plan table_no=B8 -> hanya B8",
              [o["order_id"] for o in d2["orders"]] == [ob],
              str(d2["orders"]))
        print(f"\nSEMUA LOLOS: {len(PASSED)} checks")
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
