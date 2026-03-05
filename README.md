# 🐻 Bot Telegram OTP WhatsApp Vietnam (GrizzlySMS)

Bot Telegram untuk order nomor WhatsApp Vietnam via API GrizzlySMS.
Setiap pengguna bisa mendaftarkan API Key mereka sendiri.

## 🚀 Deploy ke Railway

1. Push semua file ke GitHub repo
2. Buka [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Set variable:
   - `BOT_TOKEN` = `8753406517:AAFMSxxBE9W11Pn6VudzNCV3mdLYlLyALVA`
4. Tambah Volume (mount `/data`) dan variable:
   - `DB_PATH` = `/data/database.db`

## 🎮 Cara Pakai

1. `/start` — Menu utama
2. `/setapi API_KEY` — Daftarkan API Key (dari https://grizzlysms.com/docs)
3. `/order N` — Order N nomor sekaligus
4. OTP otomatis muncul di bawah nomor
5. `/balance` — Cek saldo
