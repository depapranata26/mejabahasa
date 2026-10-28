<!--
  Template README untuk repo Hugging Face Space MejaBahasa.
  CARA PAKAI: copy file ini menjadi README.md di repo Space
  (huggingface.co/spaces/<user>/mejabahasa), BUKAN di repo GitHub utama.
  Frontmatter YAML di bawah adalah konfigurasi Space: SDK Docker dan
  port aplikasi 7860 (sesuai Dockerfile).
-->
---
title: MejaBahasa
emoji: 🍽️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# MejaBahasa — Multilingual Restaurant Agent

Pesan dari meja dengan bahasa tamu sendiri (Mandarin, Korea, Jepang,
Indonesia). QR meja → chat web di HP tamu → agen memvalidasi menu dan
stok → tamu konfirmasi → staf ACC → tiket Indonesia ke dapur.

Demo untuk Meta Global AI Developer Hackathon 2026 (Indie Developer).
Model: Meta open-weight `meta-models/muse-spark-open-1026` via Baseten
(kunci API diisi sebagai Secret di Settings Space, tidak pernah
di-commit ke repo).
