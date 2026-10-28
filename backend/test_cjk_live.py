#!/usr/bin/env python3
"""Tes LIVE cakupan CJK MejaBahasa (model Baseten, DB temp terisolasi).

Pola yang sama seperti test_agent.py (Jepang), diperluas ke Mandarin dan
Korea — dua bahasa pintu turis yang belum pernah diuji live:
  (zh) Tamu Mandarin pesan Nasi Goreng Spesial x1 + Es Teh Manis x1.
  (ko) Tamu Korea pesan item yang sama.
Kontrak tiap bahasa (aturan 1 + aturan 7):
  turn 1 (tanpa info alergi) -> agen HARUS bertanya alergi dalam bahasa
    tamu dan BELUM membuat order (0 order, tanpa create_order).
  turn 2 (tamu menjawab "tidak ada alergi/pantangan") -> draft dibuat:
    1 order status draft, item sesuai pesanan, create_order dipanggil
    dengan language yang benar ("zh"/"ko"), balasan dalam bahasa tamu.

Aturan pelaporan: skenario GAGAL dilaporkan SEBAGAI GAGAL dengan output
lengkap — jangan diubah jadi lolos, jangan ganti skenario diam-diam.

Usage: python3 backend/test_cjk_live.py   (from the repo root)
Exit 0 hanya jika semua skenario lolos.
"""
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import agent, seed  # noqa: E402

CHINESE = re.compile(r"[\u4e00-\u9faf]")
KOREAN = re.compile(r"[\uac00-\ud7a3]")

NASI_GORENG = 1
ES_TEH = 9


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    seed.create_db(db_path)
    return db_path


def _db_state(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders")]
        items = [dict(r) for r in conn.execute("SELECT * FROM order_items")]
        return orders, items
    finally:
        conn.close()


def _run_scenario(label, lang, script_re, allergy_word,
                  msg1, msg2, table_no):
    print(f"=== ({label}) tamu {lang}: pesan 2 item ===")
    db_path = _fresh_db()
    print(f"guest: {msg1}")
    res1 = agent.run_turn(msg1, table_no=table_no, db_path=db_path)
    print(f"path: {res1['path']}")
    for call in res1["tool_calls"]:
        print(f"  tool {call['tool']} args={call['args']} -> {call['result']}")
    print(f"agent: {res1['reply']}")
    orders, items = _db_state(db_path)
    print(f"DB turn 1: {len(orders)} order(s) (harus 0 — alergi belum dijawab)")
    ok_t1 = (
        len(orders) == 0
        and not any(c["tool"] == "create_order" for c in res1["tool_calls"])
        and bool(script_re.search(res1["reply"]))
        and allergy_word in res1["reply"]
    )
    print(f"CHECK ({label}) turn 1 (tanya alergi, belum buat): "
          f"{'PASS' if ok_t1 else 'FAIL'}")
    if not ok_t1:
        return False

    print(f"\nguest: {msg2}")
    history = [
        {"role": "user", "content": msg1},
        {"role": "assistant", "content": res1["reply"]},
    ]
    res2 = agent.run_turn(msg2, table_no=table_no, history=history,
                          db_path=db_path)
    print(f"path: {res2['path']}")
    for call in res2["tool_calls"]:
        print(f"  tool {call['tool']} args={call['args']} -> {call['result']}")
    print(f"agent: {res2['reply']}")
    orders, items = _db_state(db_path)
    print(f"DB: {len(orders)} order(s), {len(items)} order item(s): "
          f"{orders} {items}")
    created = [c for c in res2["tool_calls"] if c["tool"] == "create_order"]
    got = {(it["menu_item_id"], it["qty"]) for it in items}
    ok_t2 = (
        len(orders) == 1
        and orders[0]["status"] == "draft"
        and got == {(NASI_GORENG, 1), (ES_TEH, 1)}
        and bool(created)
        and created[0]["args"].get("language") == lang
        and bool(script_re.search(res2["reply"]))
    )
    print(f"CHECK ({label}) turn 2 (draft dibuat, bahasa {lang}): "
          f"{'PASS' if ok_t2 else 'FAIL'}")
    return ok_t2


def main():
    passed = []
    try:
        passed.append(_run_scenario(
            "zh", "zh", CHINESE, "过敏",
            "我要一份 Nasi Goreng Spesial，再加一杯 Es Teh Manis。",
            "我没有任何过敏，也没有饮食限制。",
            "C3"))
    except Exception as exc:  # noqa: BLE001 - kegagalan harus dilaporkan
        print(f"CHECK (zh): FAIL (exception: {exc})")
        passed.append(False)
    try:
        passed.append(_run_scenario(
            "ko", "ko", KOREAN, "알레르기",
            "Nasi Goreng Spesial 1개와 Es Teh Manis 1개 주세요.",
            "알레르기가 없고 식단 제한도 없습니다.",
            "C4"))
    except Exception as exc:  # noqa: BLE001 - kegagalan harus dilaporkan
        print(f"CHECK (ko): FAIL (exception: {exc})")
        passed.append(False)
    print(f"\nHASIL: {sum(passed)}/{len(passed)} skenario lolos")
    return 0 if all(passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
