# MejaBahasa — Agen Restoran Multibahasa

> Read in English: [README.md](README.md)

**Status:** demo jalan (30 Oktober 2026) — chat tamu, dashboard staf, layar
dapur, koordinator lapangan, dan dashboard owner semuanya jalan di atas satu
database SQLite. 200 automated checks lolos.

## Masalah yang diselesaikan

Di restoran yang ramai, waktu waiter paling banyak habis untuk menjelaskan
makanan dan mencatat orderan — apalagi kalau tamu tidak bisa bahasa staf,
dan apalagi kalau restoran kekurangan staf. Tamu yang tidak bisa bahasa
Inggris (terutama dari China, Korea, dan Jepang) sering dapat orderan yang
salah, sementara SOP bisa melarang waiter main HP untuk translate.

MejaBahasa menjawabnya lewat pintu yang aman dari SOP: **QR di meja → web
chat di HP tamu sendiri**. Tamu pesan dengan bahasa sendiri — dengan mengetik
atau berbicara — dan AI mendeteksi bahasanya secara otomatis. Agen memvalidasi
setiap pesanan ke menu dan stok, bertanya balik kalau ada ambiguitas, lalu —
setelah konfirmasi tamu dan ACC staf — mengirim tiket dapur berbahasa
Indonesia yang bersih. Waiter tidak lagi tertahan menjelaskan menu dan bisa
kembali mengantar, membersihkan meja, dan semua pekerjaan lain yang
dibutuhkan lantai yang kekurangan staf.

## Alur tamu

```
Scan QR meja → pilih ngomong atau ngetik (toggle chrome ID/EN)
  → AI deteksi bahasa tamu otomatis (zh / ko / ja / id — live)
  → pesan dalam bahasa tamu (teks atau input suara)
  → agen validasi menu & stok → klarifikasi kalau ambigu
  → tamu konfirmasi → staf ACC → tiket dapur bahasa Indonesia
  → tamu pantau status order live; balasan bisa dibacakan
```

Tidak ada pilih bahasa manual: deteksi otomatis, dengan pilih bahasa manual
hanya sebagai fallback kalau deteksi salah. Audiensnya universal — tamu
siapa pun di meja itu, turis maupun lokal. Tamu juga bisa lihat daftar menu
tanpa chat dulu.

## Yang sudah dibangun

- **Pintu tamu** (`/`): chat multibahasa, input suara (Web Speech API,
  disembunyikan anggun kalau browser tak mendukung), bacakan balasan,
  browse menu, toggle chrome ID/EN, polling status order live, lapor keluhan.
- **Dashboard staf** (`/staff`): antrean order, ACC terima/tolak, kirim
  tiket dapur, triase keluhan, kelola menu (stok / harga).
- **Layar dapur** (`/kitchen`): lifecycle tiket open → cooking → ready →
  served.
- **Koordinator lapangan** (`/koordinator`): siapa masak/buat/antar apa per
  meja — assignment greedy ke beban paling ringan, target bottleneck per
  meja, tugas antar untuk waiter.
- **Dashboard owner** (`/owner`): omzet, order per status, 5 item terlaris,
  status tiket, stok menipis (≤10), keluhan per urgensi + yang open.
  Refresh tiap 10 detik.
- **Guardrails**: tanya alergi sebelum order (aturan 7), bentrok alergi
  menolak seluruh order; peracik budget; revisi + negosiasi parsial;
  stok anti-race (BEGIN IMMEDIATE); item menu berjendela waktu; semua aksi
  agen tercatat di `agent_action_log`.

## Konteks hackathon

- **Event:** Meta Global AI Developer Hackathon 2026
- **Jalur:** Indie Developer — tema *Build for Your World*
- **Target:** tantangan teknis + bonus *Best Agentic Use-Case*
- **Tenggat submit:** 5 November 2026, 09:00 WIB
- **Model:** open-weight Meta, dipakai secara load-bearing (bukan sekadar
  chatbot): agen merencanakan, memanggil tools, dan bertindak — validasi,
  klarifikasi, tiket, dan ACC terlihat di demo.

## Provider model (swappable)

Provider utama: **Baseten** dengan model `meta-models/muse-spark-open-1026`
(OpenAI-compatible, base URL `https://inference.baseten.co/v1`).

Lapisan provider dirancang bisa diganti (`backend/provider.py`): cadangan
Fireworks dan Meta Model API disiapkan sebagai interface yang sama. API key
**tidak pernah** ditulis di repo ini — key dibaca dari environment
(`BASETEN_API_KEY`), lihat `.env.example`. Tanpa key, dashboard, dapur,
koordinator, dan owner tetap jalan; hanya balasan chat AI yang butuh key.

## Aturan agen (guardrails — yang membuat ini agen, bukan chatbot)

1. **Jangan pernah menebak.** Kalau pesanan ambigu → tanya balik dalam bahasa tamu.
2. **Harga / stok / menu HANYA dari database.** Dilarang mengarang.
3. **Pesanan dan tiket hanya jalan setelah konfirmasi tamu + ACC staf**
   (human-in-the-loop).
4. **Semua aksi agen tercatat** di `agent_action_log` supaya bisa diaudit.

## Struktur repo

```
README.md              Gambaran proyek, scope, dan cara jalan (Bahasa Inggris)
README.id.md           Gambaran yang sama dalam Bahasa Indonesia
backend/
  __init__.py
  config.py            Konfigurasi dari environment (tanpa secret)
  provider.py          Interface provider model (Baseten utama, swappable)
  tools.py             Tools agen (menu, stok, order, tiket, peracik budget,
                       revisi, jendela waktu) + langkah manusia (konfirmasi,
                       ACC) + lifecycle tiket
  agent.py             Loop agen: model -> tool calls -> balasan bahasa tamu
  server.py            Server demo stdlib: tamu, staf, dapur, koordinator,
                       owner + API JSON
  report.py            Laporan operasional yang dihitung dari database
  coordinator.py       Perencana tugas koordinator lapangan (greedy)
  queue_estimate.py    Estimasi tunggu paralel per stasiun
  complaints.py        Triase keluhan deterministik (open→handling→resolved)
  menu_admin.py        Kontrol stok/harga oleh staf
  seed.py              Bangun/seed database (aman di-run ulang, migrasi)
  test_*.py            Suite tes — *_live.py butuh model key, sisanya
                       jalan offline
data/
  schema.sql           Skema SQLite: menu, stok, pesanan, tiket, log aksi
demo/
  menu.json            Menu contoh FIKTIF (bukan data restoran manapun)
.env.example           Placeholder konfigurasi (tanpa key asli)
.gitignore
```

## Scope — yang TIDAK dibangun di hackathon ini (roadmap)

- **Pintu WhatsApp untuk pelanggan lokal** (tanya menu, spesial harian,
  reservasi, status pesanan) — fase 2 pasca-hackathon, mesin yang sama, pintu
  berbeda. Integrasi WhatsApp Cloud API sengaja tidak dibangun di sini karena
  scope-nya satu proyek sendiri.
- **Waitlist meja** — stretch goal, hanya kalau inti selesai lebih cepat.
- **Penjelasan tagihan dan tutor waiter** — roadmap video saja.
- **Payment gateway beneran (QRIS / transfer / DP)** — di demo hanya langkah
  simulasi, bukan transaksi beneran (butuh akun merchant & dokumen usaha).
- **Reservasi + floor plan editor, multi-restoran, aplikasi native.**

## Data demo

`demo/menu.json` adalah menu contoh **fiktif** yang dibuat untuk keperluan demo
dan pengujian — bukan menu, harga, atau stok restoran manapun.

## Menjalankan

```bash
cp .env.example .env   # isi BASETEN_API_KEY milik sendiri, jangan commit .env
python3 backend/server.py 8000
# tamu:        http://localhost:8000/
# staf:        http://localhost:8000/staff
# dapur:       http://localhost:8000/kitchen
# koordinator: http://localhost:8000/koordinator
# owner:       http://localhost:8000/owner
```

Database (`data/mejabahasa.db`) dibuat lokal dari `data/schema.sql` +
`demo/menu.json` saat pertama jalan. File `*.db` dan `.env` diabaikan git —
jangan pernah di-commit.

**Demo ini tanpa login.** Semua halaman terbuka di jaringan lokal by design;
halaman dashboard owner menyatakannya langsung di halamannya.

**Belum ada hosting live.** README ini mendeskripsikan demo lokal; URL publik
akan diumumkan terpisah kalau sudah ada.
