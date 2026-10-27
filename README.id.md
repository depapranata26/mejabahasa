# MejaBahasa — Agen Restoran Multibahasa

> Read in English: [README.md](README.md)

**Status:** Hari pertama (27 Oktober 2026) — scaffold awal. Ordering inti menyusul hari ke-2.

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
Scan QR meja → pilih ngomong atau ngetik
  → AI deteksi bahasa tamu secara otomatis
  → pesan dalam bahasa tamu (teks; suara sebagai peningkatan)
  → agen validasi menu & stok → klarifikasi kalau ambigu
  → tamu konfirmasi → staf ACC → tiket dapur bahasa Indonesia
```

Tidak ada pilih bahasa manual: deteksi otomatis, dengan pilih bahasa manual
hanya sebagai fallback kalau deteksi salah. Audiensnya universal — tamu
siapa pun di meja itu, turis maupun lokal.

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
(`BASETEN_API_KEY`), lihat `.env.example`.

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
  server.py            Server demo stdlib: chat tamu, dashboard staf, dapur
  report.py            Laporan operasional yang dihitung dari database
  seed.py              Bangun/seed database (aman di-run ulang, migrasi)
  test_*.py            Suite tes (ordering, alergi, race, server, budget,
                       revisi, laporan, jendela waktu)
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
- **Payment gateway beneran (QRIS / transfer / DP)** — di demo hanya langkah
  simulasi, bukan transaksi beneran (butuh akun merchant & dokumen usaha).
- **Reservasi + floor plan editor, multi-restoran, aplikasi native.**

## Data demo

`demo/menu.json` adalah menu contoh **fiktif** yang dibuat untuk keperluan demo
dan pengujian — bukan menu, harga, atau stok restoran manapun.

## Menjalankan (menyusul seiring build)

```bash
cp .env.example .env   # isi BASETEN_API_KEY milik sendiri, jangan commit .env
python3 -m backend.tools  # placeholder hari pertama
```

Catatan: file `*.db` dan `.env` diabaikan git (lihat `.gitignore`) — database
dibuat lokal dari `data/schema.sql` + `demo/menu.json` saat runtime.
