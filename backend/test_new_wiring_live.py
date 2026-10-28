#!/usr/bin/env python3
"""Tes LIVE wiring baru MejaBahasa lewat agen (model Baseten) + fallback JSON.

Skenario live (model sungguhan, DB temp terisolasi per skenario):
  (C1) compose_meal: tamu (Indonesia) menyebut budget Rp100.000 + alergi
      kacang -> agen HARUS memanggil compose_meal (bukan mengarang
      kombinasi), total <= budget, tanpa item beralergen kacang.
  (U1) update_order (2 turn): turn 1 tamu pesan Nasi Goreng + menjawab
      LENGKAP soal alergi dan pantangan (aturan 7: model baru boleh
      create_order setelah keduanya dijawab — pesan yang hanya menjawab
      alergi SAJA benar ditahan model untuk tanya diet dulu, itu perilaku
      benar, bukan bug) -> draft terbentuk; turn 2 tamu ganti ke Mie
      Goreng Jawa, dengan draft_order_id diinjeksikan persis seperti
      jalur produksi (server meneruskan last_order_id klien) -> agen
      HARUS memanggil update_order pada draft itu; DB akhir: 1 draft,
      isinya HANYA Mie Goreng Jawa x1, tanpa order kedua.
  (T1) jendela waktu (DALAM jendela, jam tes ~21:00 WIB): tamu pesan
      Ikan Bakar Jimbaran (17:00-22:00) + tanpa alergi -> draft HARUS
      terbentuk (item tersedia). Penolakan di LUAR jendela sudah
      dibuktikan deterministik oleh test_time.py (waktu injeksi);
      live di luar jendela hanya bisa jalan di luar 17:00-22:00 WIB,
      jadi skenario ini sengaja hanya mengunci sisi "tersedia".
  (F1) fallback JSON (crafted input, deterministik, tanpa model):
      _call_model dimonkeypatch mengembalikan blok ```json action
      tanpa native tool_calls -> path HARUS "json_fallback" dan tool
      dieksekusi. Blok JSON rusak -> tidak crash, path "direct",
      tanpa tool. Ini membuktikan JALUR KODE fallback; observasi live
      (C1/U1/T1) mencatat path yang dipakai model — model live
      sejauh ini selalu memakai native tool_calls, jadi fallback
      belum pernah terpicu oleh model sungguhan, dan itu dilaporkan
      apa adanya, bukan diklaim lolos live.

Aturan pelaporan: skenario GAGAL dilaporkan SEBAGAI GAGAL dengan output
lengkap — jangan diubah jadi lolos, jangan ganti skenario diam-diam.

Usage: python3 backend/test_new_wiring_live.py   (from the repo root)
Exit 0 hanya jika semua skenario lolos.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import agent, seed, tools  # noqa: E402

NASI_GORENG = 1
MIE_GORENG = 2
IKAN_BAKAR = 7


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    seed.create_db(db_path)
    return db_path


def _db_orders(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders")]
        items = [dict(r) for r in conn.execute("SELECT * FROM order_items")]
        return orders, items
    finally:
        conn.close()


def _dump(res):
    print(f"path: {res['path']}")
    for call in res["tool_calls"]:
        print(f"  tool {call['tool']} args={call['args']} -> {call['result']}")
    print(f"agent: {res['reply']}")


def scenario_c1():
    """Budget + alergi kacang -> compose_meal, total <= budget, tanpa kacang."""
    print("=== (C1) budget Rp100.000 + alergi kacang -> compose_meal ===")
    db_path = _fresh_db()
    msg = ("Budget saya Rp100.000. Saya alergi kacang. "
           "Tolong pilihkan paket makan buat saya.")
    print(f"guest: {msg}")
    res = agent.run_turn(msg, table_no="B7", db_path=db_path)
    _dump(res)

    compose = [c for c in res["tool_calls"] if c["tool"] == "compose_meal"]
    ok_call = bool(compose)
    ok_args = bool(compose) and compose[0]["args"].get("budget_idr") == 100000 \
        and "kacang" in (compose[0]["args"].get("allergy_notes") or "").lower()
    result = compose[0]["result"] if compose else {}
    items = result.get("items") or []
    total = result.get("total_idr")
    menu = {it["id"]: it for it in tools.get_menu(db_path=db_path)}
    peanut = [it for it in items
              if "kacang tanah" in (menu.get(it["id"], {}).get("allergens") or [])]
    ok_result = bool(items) and total is not None and total <= 100000 \
        and not peanut
    reply_low = res["reply"].lower()
    ok_reply = any(it["name"].lower() in reply_low for it in items) \
        if items else False
    print(f"compose_meal dipanggil={ok_call}, args benar={ok_args}")
    print(f"items={items}, total={total}, item kacang={peanut}")
    print(f"reply menyebut kombinasi={ok_reply}")
    ok = ok_call and ok_args and ok_result and ok_reply
    print(f"CHECK (C1): {'PASS' if ok else 'FAIL'}")
    return ok


def scenario_u1():
    """Draft lalu ganti pesanan -> update_order, DB hanya berisi revisi."""
    print("\n=== (U1) draft Nasi Goreng lalu ganti ke Mie Goreng ===")
    db_path = _fresh_db()
    msg1 = ("Saya mau pesan Nasi Goreng Spesial 1 porsi. "
            "Saya tidak punya alergi dan tidak ada pantangan diet.")
    print(f"guest: {msg1}")
    res1 = agent.run_turn(msg1, table_no="B7", db_path=db_path)
    print("--- turn 1 ---")
    _dump(res1)
    created = [c for c in res1["tool_calls"] if c["tool"] == "create_order"]
    if not created or created[0]["result"].get("error"):
        print("CHECK (U1) turn 1: FAIL — draft tidak terbentuk")
        return False
    order_id = created[0]["result"].get("order_id")
    print(f"draft order_id={order_id}")
    if not order_id:
        print("CHECK (U1) turn 1: FAIL — order_id tidak ada di hasil tool")
        return False

    msg2 = "Ganti jadi Mie Goreng Jawa 1 porsi saja ya."
    print(f"\nguest: {msg2}")
    history = [
        {"role": "user", "content": msg1},
        {"role": "assistant", "content": res1["reply"]},
    ]
    res2 = agent.run_turn(msg2, table_no="B7", history=history, db_path=db_path,
                          draft_order_id=order_id)
    print("--- turn 2 ---")
    _dump(res2)
    updated = [c for c in res2["tool_calls"] if c["tool"] == "update_order"]
    ok_call = bool(updated) and updated[0]["args"].get("order_id") == order_id
    orders, items = _db_orders(db_path)
    print(f"DB: {len(orders)} order(s), items={items}")
    ok_db = (len(orders) == 1 and orders[0]["status"] == "draft"
             and len(items) == 1 and items[0]["menu_item_id"] == MIE_GORENG
             and items[0]["qty"] == 1)
    ok = ok_call and ok_db
    print(f"update_order dipanggil dgn order_id benar={ok_call}, DB revisi={ok_db}")
    print(f"CHECK (U1): {'PASS' if ok else 'FAIL'}")
    return ok


def scenario_t1():
    """Dalam jendela 17:00-22:00 WIB: Ikan Bakar HARUS bisa dipesan."""
    now = time.strftime("%H:%M", time.localtime())
    print(f"\n=== (T1) Ikan Bakar Jimbaran dalam jendela (jam tes {now} WIB) ===")
    if not ("17:00" <= now < "22:00"):
        print(f"SKIP (T1): jam {now} di luar jendela 17:00-22:00 — "
              "penolakan luar jendela sudah dibuktikan test_time.py")
        return None
    db_path = _fresh_db()
    msg = ("Saya mau pesan Ikan Bakar Jimbaran 1 porsi. "
           "Saya tidak punya alergi dan tidak ada pantangan diet.")
    print(f"guest: {msg}")
    res = agent.run_turn(msg, table_no="B7", db_path=db_path)
    _dump(res)
    created = [c for c in res["tool_calls"] if c["tool"] == "create_order"]
    orders, items = _db_orders(db_path)
    reply_low = res["reply"].lower()
    refused = any(w in reply_low for w in ("tidak tersedia", "belum tersedia",
                                          "di luar", "nanti"))
    ok = (bool(created) and not created[0]["result"].get("error")
          and len(orders) == 1 and len(items) == 1
          and items[0]["menu_item_id"] == IKAN_BAKAR and not refused)
    print(f"draft Ikan Bakar={ok}, penolakan di reply={refused}")
    print(f"CHECK (T1): {'PASS' if ok else 'FAIL'}")
    return ok


def scenario_f1():
    """Fallback JSON: blok ```json action tanpa tool_calls -> dieksekusi."""
    print("\n=== (F1) fallback JSON (crafted input, tanpa model) ===")
    orig = agent._call_model
    calls = {"n": 0}

    def fake_model(messages, use_tools=True):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("Berikut hasil pengecekan:\n"
                    "```json\n{\"action\": \"get_menu\", \"args\": {}}\n```", [])
        return ("Ini menu kami, silakan pilih.", [])

    try:
        agent._call_model = fake_model
        res = agent.run_turn("Lihat menu dong", table_no="B7",
                             db_path=_fresh_db())
    finally:
        agent._call_model = orig
    _dump(res)
    ok = (res["path"] == "json_fallback"
          and any(c["tool"] == "get_menu" for c in res["tool_calls"])
          and "menu kami" in res["reply"])
    print(f"CHECK (F1) positif: {'PASS' if ok else 'FAIL'}")
    if not ok:
        return False

    def fake_broken(messages, use_tools=True):
        return ("```json\n{not json\n```", [])

    try:
        agent._call_model = fake_broken
        res2 = agent.run_turn("halo", table_no="B7", db_path=_fresh_db())
    finally:
        agent._call_model = orig
    ok2 = (res2["path"] == "direct" and not res2["tool_calls"])
    print(f"CHECK (F1) blok rusak -> direct tanpa crash: "
          f"{'PASS' if ok2 else 'FAIL'} (path={res2['path']})")
    return ok and ok2


def main():
    results = {}
    for name, fn in [("C1", scenario_c1), ("U1", scenario_u1),
                     ("T1", scenario_t1), ("F1", scenario_f1)]:
        try:
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001 - kegagalan harus dilaporkan
            print(f"CHECK ({name}): FAIL (exception: {exc})")
            results[name] = False
    scored = {k: v for k, v in results.items() if v is not None}
    skipped = [k for k, v in results.items() if v is None]
    print(f"\nHASIL: {sum(scored.values())}/{len(scored)} skenario lolos"
            + (f" (skip: {', '.join(skipped)})" if skipped else ""))
    return 0 if all(scored.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
