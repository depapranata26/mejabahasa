#!/usr/bin/env python3
"""MejaBahasa demo server (Python standard library only, no new deps).

Serves the three faces of the same SQLite database:
  GET  /          guest chat (talks to the agent via POST /api/chat)
  GET  /staff     staff dashboard (order queue, ACC, send ticket)
  GET  /kitchen   kitchen display (ticket lifecycle)
  GET  /api/menu, /api/orders, /api/tickets
  POST /api/chat, /api/orders,
       /api/orders/<id>/confirm | request_acc | acc | ticket,
       /api/tickets/<id>/status

Usage: python3 backend/server.py [port] [db-path]
Defaults: port 8000, database from config.DATABASE_PATH.

Demo only: no authentication — run on a trusted network. The chat
endpoint needs the Baseten credential (Secure Vault, same pattern as
test_baseten.py); dashboard and kitchen work without any model key.
"""
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import complaints, menu_admin, report, seed, tools  # noqa: E402
from backend.config import DATABASE_PATH  # noqa: E402

CSS = """
body{font-family:system-ui,sans-serif;max-width:680px;margin:0 auto;
padding:16px;color:#222}h1{font-size:22px}.card{border:1px solid #ccc;
border-radius:8px;padding:12px;margin:10px 0}.warn{background:#fff3cd;
border:1px solid #e6c200;border-radius:6px;padding:8px;margin:8px 0}
button{padding:8px 14px;margin:4px 4px 4px 0;border-radius:6px;
border:1px solid #888;background:#f0f0f0;cursor:pointer}
button.primary{background:#222;color:#fff}
input,textarea{width:100%;padding:8px;margin:6px 0;box-sizing:border-box}
#log{border:1px solid #ccc;border-radius:8px;padding:10px;min-height:200px}
.msg{margin:8px 0}.msg b{display:block}.muted{color:#666;font-size:13px}
"""

GUEST_HTML = """<!DOCTYPE html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MejaBahasa — Pesan dari Meja</title><style>""" + CSS + """</style></head>
<body><h1>🍽️ MejaBahasa</h1><p class="muted">Scan QR meja, lalu pesan
dengan bahasa kamu sendiri. AI mendeteksi bahasanya
otomatis.</p>
<label>Nomor meja <input id="table" value="A12"></label>
<div id="log"></div>
<div id="actions"></div>
<div id="statusBox"></div>
<input id="msg" placeholder="Tulis pesananmu di sini…">
<button id="sendBtn" class="primary" onclick="send()">Kirim</button>
<div style="margin-top:16px">
<input id="complaintMsg" placeholder="Ada keluhan? Tulis di sini…">
<button onclick="sendComplaint()">Laporkan keluhan</button>
<div id="complaintStatus" class="muted"></div></div>
<script>
let history=[];let lastOrder=null;let statusTimer=null;let complaintTimer=null;
let qrTable=new URLSearchParams(location.search).get('table');
if(qrTable)document.getElementById('table').value=qrTable;
function esc(s){return String(s).replace(/[&<>"']/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function show(role,text){let d=document.createElement('div');d.className='msg';
  d.innerHTML='<b>'+esc(role)+'</b>'+esc(text);document.getElementById('log').appendChild(d)}
async function api(path,body){let r=await fetch(path,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  return r.json()}
async function send(){let inp=document.getElementById('msg');
  let text=inp.value.trim();if(!text)return;
  let btn=document.getElementById('sendBtn');
  if(btn.disabled)return;
  btn.disabled=true;inp.disabled=true;
  let log=document.getElementById('log');
  let typing=document.createElement('div');typing.className='msg muted';
  typing.textContent='MejaBahasa sedang mengetik…';log.appendChild(typing);
  try{inp.value='';
    show('Kamu',text);history.push({role:'user',content:text});
    let data=await api('/api/chat',{table_no:document.getElementById('table').value,
      message:text,history:history.slice(0,-1),last_order_id:lastOrder});
    if(!data.ok){show('Sistem','Maaf, terjadi kesalahan: '+esc(data.error));return}
    show('MejaBahasa',data.reply);history.push({role:'assistant',content:data.reply});
    if(data.last_order_id){lastOrder=data.last_order_id;
      document.getElementById('actions').innerHTML='<p class="muted">Pesanan draft #'+
      lastOrder+' dibuat. Tekan Konfirmasi kalau sudah benar.</p>'+
      '<button class="primary" onclick="confirmOrder()">Konfirmasi pesanan</button>'}}
  finally{typing.remove();btn.disabled=false;inp.disabled=false;inp.focus()}}
async function confirmOrder(){let d=await api('/api/orders/'+lastOrder+'/confirm');
  if(!d.ok){show('Sistem','Gagal konfirmasi: '+esc(d.error));return}
  show('Sistem','Pesanan dikonfirmasi. Meminta ACC staf…');
  let d2=await api('/api/orders/'+lastOrder+'/request_acc');
  show('Sistem',d2.ok?'Menunggu ACC staf. Dapur akan menerima tiket setelah staf menyetujui.'
    :'Gagal minta ACC: '+esc(d2.error));
  document.getElementById('actions').innerHTML='';
  if(d2.ok)startStatusPoll()}
function orderLabel(o){if(o.status==='acc_rejected')
  return '❌ Ditolak staf — silakan chat lagi untuk pesan ulang.';
  if(o.status==='done'||o.ticket_status==='served')
    return '✅ Selesai disajikan. Selamat menikmati!';
  if(o.ticket_status==='ready')return '🍽️ Siap diantar ke meja kamu.';
  if(o.ticket_status==='cooking')return '🔥 Sedang dimasak di dapur.';
  if(o.ticket_status==='open')return '🧑‍🍳 Tiket sudah di dapur, menunggu dimasak.';
  if(o.status==='sent_to_kitchen')return '📨 Tiket dikirim ke dapur.';
  if(o.status==='acc_accepted')return '✅ Diterima staf, menunggu tiket dapur.';
  if(o.status==='acc_pending'||o.status==='confirmed')
    return '⏳ Menunggu ACC staf…';
  return '📝 Draft dibuat.'}
async function pollOrder(){if(!lastOrder)return;
  let d=await fetch('/api/orders/'+lastOrder).then(r=>r.json());
  if(!d.ok)return;let o=d.order;
  document.getElementById('statusBox').innerHTML='<div class="card">📍 Status pesanan #'+
    o.id+': <b>'+esc(orderLabel(o))+'</b></div>';
  if(o.status==='done'||o.status==='acc_rejected'){
    clearInterval(statusTimer);statusTimer=null}}
function startStatusPoll(){if(statusTimer)clearInterval(statusTimer);
  pollOrder();statusTimer=setInterval(pollOrder,5000)}
async function sendComplaint(){let inp=document.getElementById('complaintMsg');
  let text=inp.value.trim();if(!text)return;inp.value='';
  let box=document.getElementById('complaintStatus');
  let d=await api('/api/complaints',
    {table_no:document.getElementById('table').value,
     order_id:lastOrder,message:text});
  if(!d.ok){box.textContent='Gagal kirim keluhan: '+d.error;return}
  let cid=d.id;
  box.textContent='Keluhan #'+cid+' diterima (urgensi: '+d.urgency+
    '). Staf akan menindaklanjuti.';
  if(complaintTimer)clearInterval(complaintTimer);
  complaintTimer=setInterval(async ()=>{
    let c=await fetch('/api/complaints/'+cid).then(r=>r.json());
    if(!c.ok)return;let s=c.complaint.status;
    let label=s==='open'?'menunggu ditangani':
      s==='handling'?'sedang ditangani':'selesai ✅';
    box.textContent='Keluhan #'+cid+' ('+d.urgency+'): '+label;
    if(s==='resolved')clearInterval(complaintTimer)},5000)}
document.getElementById('msg').addEventListener('keydown',
  e=>{if(e.key==='Enter')send()});
</script></body></html>"""

STAFF_HTML = """<!DOCTYPE html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MejaBahasa — Dashboard Staf</title><style>""" + CSS + """</style></head>
<body><h1>🧑‍🍳 Dashboard Staf</h1><p class="muted">Antrean order masuk.
ACC pesanan tamu, lalu kirim tiket ke dapur. Memperbarui otomatis.</p>
<div id="list"></div>
<h2>📣 Keluhan tamu</h2>
<div id="complaints"></div>
<h2>🗂️ Kelola menu</h2>
<p class="muted">Tandai habis, restock, atau ubah harga. Agen langsung
memakai data ini — item yang ditandai habis akan ditolak saat dipesan.</p>
<button onclick="refreshMenu()">Muat ulang menu</button>
<div id="menuAdmin"></div>
<script>
function esc(s){return String(s).replace(/[&<>"']/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function api(path,body){let r=await fetch(path,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  return r.json()}
async function refresh(){let d=await fetch('/api/orders').then(r=>r.json());
  let html='';
  for(let o of d.orders){html+='<div class="card"><b>Order #'+o.id+' — Meja '+
    esc(o.table_no)+'</b> <span class="muted">'+esc(o.status)+
    ' • '+esc(o.language)+'</span>';
    if(o.allergy_notes||o.dietary_notes){html+='<div class="warn">⚠️ '+
      esc(o.allergy_notes||'')+' '+esc(o.dietary_notes||'')+'</div>'}
    html+='<div>'+o.items.map(i=>esc(i.qty)+'x '+esc(i.name)+
      (i.custom_note?' ['+esc(i.custom_note)+']':'')).join('<br>')+
      '</div><div class="muted">Total Rp'+o.total_idr.toLocaleString('id-ID')+'</div>';
    if(o.status==='acc_pending'){html+='<button class="primary" onclick="acc('+o.id+',true)">✅ Terima (ACC)</button>'+
      '<button onclick="acc('+o.id+',false)">❌ Tolak</button>'}
    if(o.status==='acc_accepted'){html+='<button class="primary" onclick="ticket('+o.id+')">📨 Kirim tiket dapur</button>'}
    html+='</div>'}
  document.getElementById('list').innerHTML=html||'<p class="muted">Belum ada order.</p>'}
async function acc(id,ok){await api('/api/orders/'+id+'/acc',{accepted:ok});refresh()}
async function ticket(id){await api('/api/orders/'+id+'/ticket');refresh()}
const URG_LABEL={tinggi:'🔴 tinggi',sedang:'🟡 sedang',rendah:'🟢 rendah'};
async function refreshComplaints(){let d=await fetch('/api/complaints').then(r=>r.json());
  let html='';
  for(let c of d.complaints){html+='<div class="card"><b>Keluhan #'+c.id+
    ' — Meja '+esc(c.table_no)+'</b> <span class="muted">'+URG_LABEL[c.urgency]+
    ' • '+esc(c.status)+'</span><div>'+esc(c.message)+'</div>';
    if(c.order_id)html+='<div class="muted">Order #'+c.order_id+'</div>';
    if(c.status==='open'){html+='<button class="primary" onclick="complaintStatus('+
      c.id+",'handling')\">Tangani</button>"}
    if(c.status==='handling'){html+='<button class="primary" onclick="complaintStatus('+
      c.id+",'resolved')\">Tandai selesai</button>"}
    if(c.status==='resolved')html+='<span class="muted">Selesai ✅</span>';
    html+='</div>'}
  document.getElementById('complaints').innerHTML=html||
    '<p class="muted">Belum ada keluhan.</p>'}
async function complaintStatus(id,st){await api('/api/complaints/'+id+'/status',
  {status:st});refreshComplaints()}
async function refreshMenu(){let d=await fetch('/api/menu').then(r=>r.json());
  let html='';
  for(let it of d.items){html+='<div class="card"><b>'+esc(it.name)+
    '</b> <span class="muted">Rp'+it.price_idr.toLocaleString('id-ID')+
    ' • stok '+it.stock+'</span><br>'+
    '<button onclick="menuStock('+it.id+',0)">Tandai habis</button>'+
    '<button onclick="menuStockDelta('+it.id+',10)">Stok +10</button> '+
    '<input id="price-'+it.id+'" type="number" min="1" value="'+it.price_idr+
    '" style="width:120px"> '+
    '<button onclick="menuPrice('+it.id+')">Ubah harga</button></div>'}
  document.getElementById('menuAdmin').innerHTML=html}
async function menuStock(id,stock){await api('/api/menu/'+id+'/stock',
  {stock:stock});refreshMenu()}
async function menuStockDelta(id,delta){await api('/api/menu/'+id+'/stock',
  {delta:delta});refreshMenu()}
async function menuPrice(id){let v=parseInt(
  document.getElementById('price-'+id).value,10);
  await api('/api/menu/'+id+'/price',{price_idr:v});refreshMenu()}
refresh();refreshComplaints();refreshMenu();
// Menu TIDAK ikut interval 3 detik: input harga yang sedang diketik
// staf akan terhapus kalau daftarnya di-render ulang terus.
setInterval(()=>{refresh();refreshComplaints()},3000);
</script></body></html>"""

KITCHEN_HTML = """<!DOCTYPE html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MejaBahasa — Dapur</title><style>""" + CSS + """</style></head>
<body><h1>🔥 Dapur</h1><p class="muted">Tiket masuk dari staf.
Ubah statusnya seiring masakan berjalan. Memperbarui otomatis.</p>
<div id="list"></div>
<script>
function esc(s){return String(s).replace(/[&<>"']/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function api(path,body){let r=await fetch(path,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  return r.json()}
const NEXT={open:['cooking','Mulai masak'],cooking:['ready','Siap diantar'],
  ready:['served','Sajikan']};
async function refresh(){let d=await fetch('/api/tickets').then(r=>r.json());
  let html='';
  for(let t of d.tickets){html+='<div class="card"><b>Tiket #'+t.ticket_id+
    ' — Meja '+esc(t.table_no)+'</b> <span class="muted">'+esc(t.status)+'</span>';
    if(t.allergy_notes||t.dietary_notes){html+='<div class="warn">⚠️ '+
      esc(t.allergy_notes||'')+' '+esc(t.dietary_notes||'')+'</div>'}
    html+='<div>'+t.items.map(i=>esc(i.qty)+'x '+esc(i.name)+
      (i.custom_note?' ['+esc(i.custom_note)+']':'')).join('<br>')+'</div>';
    if(NEXT[t.status]){let [st,label]=NEXT[t.status];
      html+='<button class="primary" onclick="setStatus('+t.ticket_id+",'"+st+"')\">"+label+'</button>'}
    else{html+='<span class="muted">Selesai ✅</span>'}
    html+='</div>'}
  document.getElementById('list').innerHTML=html||'<p class="muted">Belum ada tiket.</p>'}
async function setStatus(id,st){await api('/api/tickets/'+id+'/status',{status:st});refresh()}
refresh();setInterval(refresh,3000);
</script></body></html>"""


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def list_orders(db_path):
    conn = _conn(db_path)
    try:
        orders = conn.execute(
            "SELECT * FROM orders ORDER BY id DESC").fetchall()
        out = []
        for o in orders:
            items = conn.execute(
                "SELECT oi.qty, oi.custom_note, mi.name, mi.price_idr"
                " FROM order_items oi JOIN menu_items mi"
                " ON mi.id = oi.menu_item_id WHERE oi.order_id = ?",
                (o["id"],)).fetchall()
            total = sum(i["qty"] * i["price_idr"] for i in items)
            out.append({
                "id": o["id"], "table_no": o["table_no"],
                "language": o["language"], "status": o["status"],
                "allergy_notes": o["allergy_notes"],
                "dietary_notes": o["dietary_notes"],
                "total_idr": total,
                "items": [{"name": i["name"], "qty": i["qty"],
                           "custom_note": i["custom_note"],
                           "price_idr": i["price_idr"]} for i in items],
            })
        return out
    finally:
        conn.close()


def get_order_detail(db_path, order_id):
    """One order with its items, total, and kitchen ticket status (or None)."""
    conn = _conn(db_path)
    try:
        o = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if o is None:
            return None
        items = conn.execute(
            "SELECT oi.qty, oi.custom_note, mi.name, mi.price_idr"
            " FROM order_items oi JOIN menu_items mi"
            " ON mi.id = oi.menu_item_id WHERE oi.order_id = ?",
            (o["id"],)).fetchall()
        ticket = conn.execute(
            "SELECT status FROM kitchen_tickets WHERE order_id = ?",
            (o["id"],)).fetchone()
        return {
            "id": o["id"], "table_no": o["table_no"],
            "language": o["language"], "status": o["status"],
            "allergy_notes": o["allergy_notes"],
            "dietary_notes": o["dietary_notes"],
            "ticket_status": ticket["status"] if ticket else None,
            "total_idr": sum(i["qty"] * i["price_idr"] for i in items),
            "items": [{"name": i["name"], "qty": i["qty"],
                       "custom_note": i["custom_note"],
                       "price_idr": i["price_idr"]} for i in items],
        }
    finally:
        conn.close()


def list_tickets(db_path):
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            "SELECT kt.id AS ticket_id, kt.status, o.id AS order_id,"
            " o.table_no, o.allergy_notes, o.dietary_notes"
            " FROM kitchen_tickets kt JOIN orders o ON o.id = kt.order_id"
            " ORDER BY kt.id DESC").fetchall()
        out = []
        for t in rows:
            items = conn.execute(
                "SELECT oi.qty, oi.custom_note, mi.name"
                " FROM order_items oi JOIN menu_items mi"
                " ON mi.id = oi.menu_item_id WHERE oi.order_id = ?",
                (t["order_id"],)).fetchall()
            out.append({
                "ticket_id": t["ticket_id"], "status": t["status"],
                "order_id": t["order_id"], "table_no": t["table_no"],
                "allergy_notes": t["allergy_notes"],
                "dietary_notes": t["dietary_notes"],
                "items": [{"name": i["name"], "qty": i["qty"],
                           "custom_note": i["custom_note"]} for i in items],
            })
        return out
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep stdout clean
        pass

    def _send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Body harus JSON yang valid.")

    @property
    def db_path(self):
        return self.server.db_path

    def do_GET(self):
        path = urlsplit(self.path).path
        parts = [p for p in path.split("/") if p]
        try:
            if path == "/":
                self._send_html(200, GUEST_HTML)
            elif path == "/staff":
                self._send_html(200, STAFF_HTML)
            elif path == "/kitchen":
                self._send_html(200, KITCHEN_HTML)
            elif path == "/api/menu":
                self._send_json(200, {"items": tools.get_menu(
                    db_path=self.db_path)})
            elif path == "/api/orders":
                self._send_json(200, {"orders": list_orders(self.db_path)})
            elif len(parts) == 3 and parts[0] == "api" \
                    and parts[1] == "orders":
                try:
                    detail = get_order_detail(self.db_path, int(parts[2]))
                except ValueError:
                    self._send_json(400, {"ok": False,
                                          "error": "id order harus angka"})
                    return
                if detail is None:
                    self._send_json(404, {"ok": False,
                                          "error": "order tidak ditemukan"})
                else:
                    self._send_json(200, {"ok": True, "order": detail})
            elif path == "/api/tickets":
                self._send_json(200, {"tickets": list_tickets(self.db_path)})
            elif path == "/api/complaints":
                self._send_json(200, {"complaints":
                                      complaints.list_complaints(
                                          self.db_path)})
            elif len(parts) == 3 and parts[0] == "api" \
                    and parts[1] == "complaints":
                try:
                    c = complaints.get_complaint(int(parts[2]),
                                                 self.db_path)
                except ValueError:
                    self._send_json(400, {"ok": False,
                                          "error": "id keluhan harus angka"})
                    return
                if c is None:
                    self._send_json(404, {"ok": False,
                                          "error": "keluhan tidak ditemukan"})
                else:
                    self._send_json(200, {"ok": True, "complaint": c})
            elif path == "/api/report":
                self._send_json(200, report.daily_report(self.db_path))
            else:
                self._send_json(404, {"ok": False, "error": "rute tak dikenal"})
        except Exception as e:  # noqa: BLE001 — demo server, report plainly
            self._send_json(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        path = urlsplit(self.path).path
        parts = [p for p in path.split("/") if p]
        try:
            body = self._read_json()
            if path == "/api/chat":
                self._handle_chat(body)
            elif path == "/api/orders":
                result = tools.create_order(
                    body["table_no"], body["items"], body["language"],
                    db_path=self.db_path,
                    allergy_notes=body.get("allergy_notes", ""),
                    dietary_notes=body.get("dietary_notes", ""))
                self._send_json(200, {"ok": True, **result})
            elif len(parts) == 4 and parts[0] == "api" \
                    and parts[1] == "orders":
                oid = int(parts[2])
                action = parts[3]
                if action == "confirm":
                    r = tools.confirm_order(oid, db_path=self.db_path)
                elif action == "request_acc":
                    r = tools.request_acc(oid, db_path=self.db_path)
                elif action == "acc":
                    r = tools.decide_acc(oid, bool(body.get("accepted")),
                                         db_path=self.db_path)
                elif action == "ticket":
                    r = tools.send_kitchen_ticket(oid, db_path=self.db_path)
                else:
                    self._send_json(404, {"ok": False,
                                          "error": "aksi tak dikenal"})
                    return
                self._send_json(200, {"ok": True, **r})
            elif len(parts) == 4 and parts[0] == "api" \
                    and parts[1] == "tickets" and parts[3] == "status":
                tid = int(parts[2])
                r = tools.set_ticket_status(tid, body["status"],
                                            db_path=self.db_path)
                self._send_json(200, {"ok": True, **r})
            elif path == "/api/complaints":
                r = complaints.create_complaint(
                    body["table_no"], body["message"],
                    order_id=body.get("order_id"), db_path=self.db_path)
                self._send_json(200, {"ok": True, **r})
            elif len(parts) == 4 and parts[0] == "api" \
                    and parts[1] == "complaints" and parts[3] == "status":
                r = complaints.set_complaint_status(
                    int(parts[2]), body["status"], db_path=self.db_path)
                self._send_json(200, {"ok": True, **r})
            elif len(parts) == 4 and parts[0] == "api" \
                    and parts[1] == "menu":
                mid = int(parts[2])
                action = parts[3]
                if action == "stock":
                    if "delta" in body:
                        r = menu_admin.adjust_stock(
                            mid, body["delta"], db_path=self.db_path)
                    else:
                        r = menu_admin.set_stock(
                            mid, body["stock"], db_path=self.db_path)
                elif action == "price":
                    r = menu_admin.set_price(
                        mid, body["price_idr"], db_path=self.db_path)
                else:
                    self._send_json(404, {"ok": False,
                                          "error": "aksi tak dikenal"})
                    return
                self._send_json(200, {"ok": True, **r})
            else:
                self._send_json(404, {"ok": False, "error": "rute tak dikenal"})
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except KeyError as e:
            self._send_json(400, {"ok": False,
                                  "error": f"field wajib hilang: {e}"})
        except Exception as e:  # noqa: BLE001 — demo server, report plainly
            self._send_json(500, {"ok": False, "error": str(e)})

    def _handle_chat(self, body):
        # Lazy import: dashboard/kitchen must work even if the model
        # credential helper is unavailable in this environment.
        try:
            from backend import agent
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"ok": False,
                                  "error": f"agen tidak bisa dimuat: {e}"})
            return
        result = agent.run_turn(
            body["message"], table_no=body.get("table_no", "A12"),
            history=body.get("history") or [], db_path=self.db_path,
            draft_order_id=body.get("last_order_id"))
        last_order_id = None
        for call in result.get("tool_calls") or []:
            if call.get("tool") == "create_order":
                res = call.get("result") or {}
                if isinstance(res, dict) and res.get("order_id"):
                    last_order_id = res["order_id"]
        self._send_json(200, {"ok": True, "reply": result["reply"],
                              "path": result.get("path"),
                              "last_order_id": last_order_id})


def make_server(port, db_path):
    # Bind 0.0.0.0 (bukan loopback): platform hosting seperti Render
    # meneruskan traffic dari luar ke $PORT; bind loopback akan
    # membuat service tidak bisa dijangkau dan health check gagal.
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.daemon_threads = True
    httpd.db_path = db_path
    return httpd


def main(argv):
    # Port dari argumen CLI, atau env PORT (Replit/Koyeb/HF menyetelnya),
    # atau 8000 kalau tidak ada keduanya.
    if len(argv) > 1:
        port = int(argv[1])
    else:
        import os
        port = int(os.environ.get("PORT", "8000"))
    db_path = argv[2] if len(argv) > 2 else DATABASE_PATH
    count = seed.ensure_db(db_path)
    print(f"MejaBahasa server: http://127.0.0.1:{port}/ "
          f"(menu {count} item, db {db_path})")
    print(f"  tamu:    http://127.0.0.1:{port}/")
    print(f"  staf:    http://127.0.0.1:{port}/staff")
    print(f"  dapur:   http://127.0.0.1:{port}/kitchen")
    make_server(port, db_path).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
