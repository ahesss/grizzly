import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import sqlite3
import json
import os
import threading
import time

# =============================================
# KONFIGURASI
# =============================================
TOKEN = os.environ.get("BOT_TOKEN", "8753406517:AAFMSxxBE9W11Pn6VudzNCV3mdLYlLyALVA")
bot = telebot.TeleBot(TOKEN)

API_BASE = "https://api.grizzlysms.com/stubs/handler_api.php"
DB_PATH = os.environ.get("DB_PATH", "database.db")

# ADMIN — hanya admin yang bisa add/remove user
ADMIN_ID = 940475417

MAX_ORDER = 20         # Maksimal order sekaligus
OTP_TIMEOUT = 1200     # Timeout 20 menit (1200 detik)
CHECK_INTERVAL = 5     # Cek OTP setiap 5 detik
CANCEL_DELAY = 120     # Baru bisa cancel setelah 2 menit (120 detik)
SERVICE = "wa"         # WhatsApp service

# =============================================
# KONFIGURASI NEGARA
# =============================================
COUNTRIES = {
    "vietnam": {
        "name": "Vietnam",
        "flag": "🇻🇳",
        "country_id": "10",
        "country_code": "84",
    },
    "colombia": {
        "name": "Colombia",
        "flag": "🇨🇴",
        "country_id": "33",
        "country_code": "57",
    },
}

# Menyimpan data order aktif per chat_id agar callback bisa akses
# Format: { chat_id: { message_id: [orders_list] } }
active_orders = {}

# =============================================
# DATABASE
# =============================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        api_key TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS whitelist (
        user_id INTEGER PRIMARY KEY,
        added_by INTEGER,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_info (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        last_seen TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        detail TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # Pastikan admin selalu ada di whitelist
    c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (ADMIN_ID, ADMIN_ID))
    conn.commit()
    conn.close()

# =============================================
# WHITELIST / ACCESS CONTROL
# =============================================
def is_whitelisted(user_id):
    """Cek apakah user ada di whitelist"""
    if user_id == ADMIN_ID:
        return True
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM whitelist WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def add_to_whitelist(user_id, added_by):
    """Tambahkan user ke whitelist"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (user_id, added_by))
    conn.commit()
    conn.close()

def remove_from_whitelist(user_id):
    """Hapus user dari whitelist"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_all_whitelisted():
    """Dapatkan semua user yang ada di whitelist"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, added_at FROM whitelist")
    res = c.fetchall()
    conn.close()
    return res

# =============================================
# USER INFO & ACTIVITY LOGGING
# =============================================
def update_user_info(user):
    """Simpan/update info user (nama, username)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO user_info (user_id, first_name, last_name, username, last_seen)
                 VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
              (user.id, user.first_name, user.last_name or '', user.username or ''))
    conn.commit()
    conn.close()

def get_user_info(user_id):
    """Dapatkan info user dari DB"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT first_name, last_name, username, last_seen FROM user_info WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res

def log_activity(user_id, action, detail=""):
    """Catat aktivitas user"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO activity_log (user_id, action, detail) VALUES (?, ?, ?)",
              (user_id, action, detail))
    conn.commit()
    conn.close()

def get_active_users():
    """Dapatkan user yang terakhir aktif beserta info-nya"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT a.user_id, u.first_name, u.last_name, u.username, 
                        a.action, a.detail, a.timestamp
                 FROM activity_log a
                 LEFT JOIN user_info u ON a.user_id = u.user_id
                 WHERE a.id IN (
                     SELECT MAX(id) FROM activity_log GROUP BY user_id
                 )
                 ORDER BY a.timestamp DESC
                 LIMIT 20""")
    res = c.fetchall()
    conn.close()
    return res

def get_user_stats():
    """Dapatkan statistik penggunaan per user"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT a.user_id, u.first_name, u.last_name, u.username,
                        COUNT(*) as total_actions,
                        SUM(CASE WHEN a.action = 'order' THEN 1 ELSE 0 END) as total_orders,
                        SUM(CASE WHEN a.action = 'balance' THEN 1 ELSE 0 END) as total_balance,
                        MAX(a.timestamp) as last_active
                 FROM activity_log a
                 LEFT JOIN user_info u ON a.user_id = u.user_id
                 GROUP BY a.user_id
                 ORDER BY last_active DESC""")
    res = c.fetchall()
    conn.close()
    return res

def format_user_label(user_id, first_name, last_name, username):
    """Format label user dengan nama dan username"""
    name = first_name or "Unknown"
    if last_name:
        name += f" {last_name}"
    if username:
        name += f" (@{username})"
    return name

def get_user_api(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT api_key FROM users WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None

def set_user_api(user_id, api_key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, api_key) VALUES (?, ?)", (user_id, api_key))
    conn.commit()
    conn.close()

# =============================================
# API HELPER
# =============================================
def req_api(api_key, action, **kwargs):
    params = {'api_key': api_key, 'action': action}
    params.update(kwargs)
    try:
        r = requests.get(API_BASE, params=params, timeout=15)
        return r.text.strip()
    except Exception as e:
        return f"ERROR: {str(e)}"

def strip_country_code(number, country_code="84"):
    """Hapus country code dari nomor, sisakan nomor lokal saja"""
    number = number.strip()
    if number.startswith("+"):
        number = number[1:]
    if number.startswith(country_code):
        number = number[len(country_code):]
    return number

def get_country_label(country_key):
    """Dapatkan label negara dengan flag"""
    c = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    return f"{c['name']} {c['flag']}"

# =============================================
# FORMAT PESAN ORDER
# =============================================
def format_order_message(orders, title="", country_key="vietnam"):
    """Format pesan daftar order dengan status OTP"""
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    lines = []
    if title:
        lines.append(title)
        lines.append("")

    done_count = 0
    total = len(orders)
    now = time.time()

    for i, order in enumerate(orders, 1):
        number_local = strip_country_code(order['number'], country['country_code'])
        status = order.get('status', 'waiting')
        # Format harga: [💰 0.203 USD]
        price_str = f" [💰 {order['price']} USD]" if order.get('price') else ""

        if status == 'waiting':
            elapsed = now - order.get('order_time', now)
            remaining = max(0, OTP_TIMEOUT - elapsed)
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            lines.append(f"{i}. `{number_local}`{price_str} — ⏳ Menunggu OTP... ({mins}m {secs}s)")
        elif status == 'got_otp':
            code = order.get('code', '???')
            lines.append(f"{i}. `{number_local}`{price_str} — ✅ OTP: `{code}`")
            done_count += 1
        elif status == 'cancelled':
            lines.append(f"{i}. `{number_local}`{price_str} — 🚫 Dibatalkan (Refund)")
            done_count += 1
        elif status == 'timeout':
            lines.append(f"{i}. `{number_local}`{price_str} — ⏰ Timeout (20 menit)")
            done_count += 1
        elif status == 'error':
            lines.append(f"{i}. `{number_local}`{price_str} — ❌ Error")
            done_count += 1

    lines.append("")
    lines.append(f"📊 Progress: {done_count}/{total}")

    if done_count >= total:
        lines.append("\n✅ *Semua order selesai!*")

    return "\n".join(lines)

def safe_edit_message(text, chat_id, message_id, markup=None):
    """Edit pesan dengan handling rate limit dan error"""
    try:
        if markup:
            bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=markup)
        else:
            bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown")
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "retry after" in err_str or "too many requests" in err_str:
            time.sleep(5)
        elif "message is not modified" in err_str:
            pass
        else:
            print(f"Edit message error: {e}")
        return False

# =============================================
# AUTO-CHECK OTP (BACKGROUND THREAD)
# =============================================
def auto_check_otp(chat_id, message_id, orders, api_key, country_key="vietnam"):
    """Background thread yang otomatis cek OTP untuk semua order"""
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    country_label = get_country_label(country_key)
    start_time = time.time()
    last_edit_time = 0
    EDIT_COOLDOWN = 3
    TIMER_UPDATE = 15
    last_timer_update = 0

    try:
        while True:
            waiting_orders = [o for o in orders if o['status'] == 'waiting']
            if not waiting_orders:
                text = format_order_message(orders, f"🛒 *Order WA {country_label} — Selesai*", country_key)
                safe_edit_message(text, chat_id, message_id)
                break

            elapsed = time.time() - start_time
            if elapsed > OTP_TIMEOUT:
                for o in orders:
                    if o['status'] == 'waiting':
                        o['status'] = 'timeout'
                        try:
                            req_api(api_key, 'setStatus', status='8', id=o['id'])
                        except:
                            pass
                        time.sleep(0.5)
                text = format_order_message(orders, f"🛒 *Order WA {country_label} — Timeout*", country_key)
                safe_edit_message(text, chat_id, message_id)
                break

            changed = False
            for o in orders:
                if o['status'] != 'waiting':
                    continue
                try:
                    res = req_api(api_key, 'getStatus', id=o['id'])
                    if res.startswith('STATUS_OK'):
                        code = res.split(':')[1] if ':' in res else '???'
                        o['status'] = 'got_otp'
                        o['code'] = code
                        changed = True
                        try:
                            req_api(api_key, 'setStatus', status='6', id=o['id'])
                        except:
                            pass
                    elif res == 'STATUS_CANCEL':
                        o['status'] = 'cancelled'
                        changed = True
                except:
                    pass
                time.sleep(0.3)

            now = time.time()
            should_update = changed or (now - last_timer_update >= TIMER_UPDATE)

            if should_update and (now - last_edit_time >= EDIT_COOLDOWN):
                remaining = [o for o in orders if o['status'] == 'waiting']
                text = format_order_message(orders, f"🛒 *Order WA {country_label}*", country_key)

                if remaining:
                    markup = InlineKeyboardMarkup()
                    oldest_order_time = min(o.get('order_time', now) for o in remaining)
                    can_cancel = (now - oldest_order_time) >= CANCEL_DELAY

                    if can_cancel:
                        ids_str = ",".join([o['id'] for o in remaining])
                        markup.row(InlineKeyboardButton(
                            f"🚫 Batalkan Sisa ({len(remaining)})",
                            callback_data=f"cancelall_{ids_str}"
                        ))
                    else:
                        wait_mins = int((CANCEL_DELAY - (now - oldest_order_time)) / 60) + 1
                        markup.row(InlineKeyboardButton(
                            f"⏳ Cancel tersedia ~{wait_mins} menit lagi",
                            callback_data="cancel_wait"
                        ))

                    if safe_edit_message(text, chat_id, message_id, markup):
                        last_edit_time = now
                        last_timer_update = now
                else:
                    if safe_edit_message(text, chat_id, message_id):
                        last_edit_time = now
                        last_timer_update = now

            time.sleep(CHECK_INTERVAL)

    except Exception as e:
        print(f"Auto-check OTP thread error: {e}")
        try:
            country_label = get_country_label(country_key)
            text = format_order_message(orders, f"🛒 *Order WA {country_label} — Error*", country_key)
            text += f"\n\n⚠️ Bot error: cek ulang dengan /start"
            safe_edit_message(text, chat_id, message_id)
        except:
            pass
    finally:
        try:
            if chat_id in active_orders and message_id in active_orders[chat_id]:
                del active_orders[chat_id][message_id]
        except:
            pass

# =============================================
# COMMAND HANDLERS
# =============================================

# --- ADMIN COMMANDS (whitelist management) ---
@bot.message_handler(commands=['adduser'])
def adduser_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Format: `/adduser USER_ID`\n\nContoh: `/adduser 123456789`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "❌ User ID harus berupa angka.")
        return
    add_to_whitelist(target_id, message.from_user.id)
    bot.reply_to(message, f"✅ User `{target_id}` berhasil ditambahkan ke whitelist.", parse_mode="Markdown")

@bot.message_handler(commands=['removeuser'])
def removeuser_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Format: `/removeuser USER_ID`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "❌ User ID harus berupa angka.")
        return
    if target_id == ADMIN_ID:
        bot.reply_to(message, "⚠️ Tidak bisa menghapus admin dari whitelist.")
        return
    remove_from_whitelist(target_id)
    bot.reply_to(message, f"✅ User `{target_id}` dihapus dari whitelist.", parse_mode="Markdown")

@bot.message_handler(commands=['listusers'])
def listusers_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    users = get_all_whitelisted()
    if not users:
        bot.reply_to(message, "📋 Whitelist kosong.")
        return
    lines = ["📋 *Daftar Whitelist:*\n"]
    for uid, added_at in users:
        info = get_user_info(uid)
        if info:
            name = format_user_label(uid, info[0], info[1], info[2])
        else:
            name = str(uid)
        role = "👑 ADMIN" if uid == ADMIN_ID else "👤 User"
        lines.append(f"{role}: {name}\n   ID: `{uid}` | Ditambahkan: {added_at}")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=['activeusers'])
def activeusers_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    active = get_active_users()
    if not active:
        bot.reply_to(message, "📊 Belum ada aktivitas user.")
        return
    lines = ["📊 *User Aktif Terakhir:*\n"]
    for i, (uid, fname, lname, uname, action, detail, ts) in enumerate(active, 1):
        name = format_user_label(uid, fname, lname, uname)
        action_text = action
        if detail:
            action_text += f" ({detail})"
        lines.append(f"{i}. {name}\n   🔹 `{action_text}` — {ts}")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=['stats'])
def stats_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    stats = get_user_stats()
    if not stats:
        bot.reply_to(message, "📈 Belum ada statistik.")
        return
    lines = ["📈 *Statistik Penggunaan Bot:*\n"]
    for uid, fname, lname, uname, total, orders, balance, last_active in stats:
        name = format_user_label(uid, fname, lname, uname)
        lines.append(
            f"👤 {name}\n"
            f"   ID: `{uid}`\n"
            f"   📦 Order: {orders}x | 💰 Cek saldo: {balance}x | 📊 Total: {total}x\n"
            f"   ⏰ Terakhir aktif: {last_active}\n"
        )
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

# --- USER COMMANDS (with whitelist check) ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_id = message.from_user.id

    # Cek whitelist
    if not is_whitelisted(user_id):
        bot.send_message(message.chat.id,
            "🔒 *Maaf, Anda tidak bisa mengakses bot ini.*\n\n"
            "Hub orang ganteng: @hesssxb",
            parse_mode="Markdown")
        return

    update_user_info(message.from_user)
    log_activity(user_id, "start")
    api_key = get_user_api(user_id)

    text = (
        "🐻 *Bot OTP WhatsApp (GrizzlySMS)* \n\n"
        "Bot ini untuk order nomor WhatsApp dengan OTP otomatis.\n"
        "Pilih negara, lalu pilih jumlah nomor yang ingin di-order.\n\n"
        "🌍 *Negara tersedia:*\n"
        "🇻🇳 Vietnam (Country ID: 10)\n"
        "🇨🇴 Colombia (Country ID: 33)\n\n"
        "📋 *Perintah:*\n"
        "`/setapi API_KEY` — Daftarkan API Key GrizzlySMS\n"
        "`/order N` — Order N nomor (pilih negara dulu)\n"
        "`/balance` — Cek saldo\n"
        "`/autobuy` — Auto buy WA Vietnam sampai saldo habis\n"
        "`/stopauto` — Hentikan auto buy\n"
        "`/help` — Bantuan\n\n"
    )

    if api_key:
        bal_res = req_api(api_key, 'getBalance')
        if 'ACCESS_BALANCE' in bal_res:
            bal = bal_res.split(':')[1]
            text += f"✅ API Key: Terdaftar\n💰 Saldo: *{bal} USD*"
        else:
            text += "⚠️ API Key terdaftar tapi tidak valid.\nGunakan `/setapi API_KEY` untuk mengganti."
    else:
        text += "❌ Belum ada API Key.\nGunakan `/setapi API_KEY` untuk mendaftar."

    markup = InlineKeyboardMarkup()
    if api_key:
        # Baris 1: Negara
        markup.row(
            InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"),
            InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia")
        )
        # Baris 2: Order & Cek Saldo
        markup.row(
            InlineKeyboardButton("🛒 Order Baru", callback_data="nav_order"),
            InlineKeyboardButton("💰 Cek Saldo", callback_data="nav_balance")
        )
        # Baris 3: Fitur Auto
        markup.row(
            InlineKeyboardButton("🔥 Auto Buy (VN)", callback_data="nav_autobuy"),
            InlineKeyboardButton("🛑 Stop Auto", callback_data="nav_stopauto")
        )
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['help'])
def help_cmd(message):
    if not is_whitelisted(message.from_user.id):
        bot.reply_to(message, "🔒 Maaf, Anda tidak bisa mengakses bot ini.\nHub orang ganteng: @hesssxb")
        return
    text = (
        "📖 *Panduan Penggunaan*\n\n"
        "1️⃣ Daftarkan API Key dari akun GrizzlySMS Anda:\n"
        "   `/setapi API_KEY_ANDA`\n\n"
        "   Dapatkan API Key di: https://grizzlysms.com/docs\n\n"
        "2️⃣ Ketik `/start` lalu pilih negara:\n"
        "   🇻🇳 Vietnam — Country ID 10\n"
        "   🇨🇴 Colombia — Country ID 33\n\n"
        "3️⃣ Pilih jumlah nomor yang ingin di-order (1-5)\n\n"
        "4️⃣ Bot akan otomatis cek OTP setiap 5 detik.\n"
        "   Ketika OTP masuk, akan langsung muncul di bawah nomor.\n\n"
        "⏱ Timeout: 20 menit per order\n"
        "🚫 Cancel: tersedia setelah 2 menit\n"
        "📱 Maks order: 20 nomor sekaligus\n\n"
        "💰 Cek saldo: `/balance`"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['setapi'])
def setapi_cmd(message):
    if not is_whitelisted(message.from_user.id):
        bot.reply_to(message, "🔒 Maaf, Anda tidak bisa mengakses bot ini.\nHub orang ganteng: @hesssxb")
        return
    update_user_info(message.from_user)
    log_activity(message.from_user.id, "setapi")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Format: `/setapi API_KEY_KAMU`\n\nDapatkan API Key di https://grizzlysms.com/docs", parse_mode="Markdown")
        return

    api_key = parts[1].strip()
    bot.reply_to(message, "⏳ Mengecek API Key...")

    bal_res = req_api(api_key, 'getBalance')
    if 'ACCESS_BALANCE' in bal_res:
        bal = bal_res.split(':')[1]
        set_user_api(message.from_user.id, api_key)
        bot.send_message(message.chat.id, f"✅ API Key valid & tersimpan!\n💰 Saldo: *{bal} USD*\n\nKetik `/start` untuk pilih negara dan mulai order.", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ API Key tidak valid atau server gangguan.")

@bot.message_handler(commands=['balance'])
def balance_cmd(message):
    if not is_whitelisted(message.from_user.id):
        bot.reply_to(message, "🔒 Maaf, Anda tidak bisa mengakses bot ini.\nHub orang ganteng: @hesssxb")
        return
    update_user_info(message.from_user)
    log_activity(message.from_user.id, "balance")
    api_key = get_user_api(message.from_user.id)
    if not api_key:
        bot.reply_to(message, "❌ Belum ada API Key. Gunakan `/setapi API_KEY`", parse_mode="Markdown")
        return

    bal_res = req_api(api_key, 'getBalance')
    if 'ACCESS_BALANCE' in bal_res:
        bal = bal_res.split(':')[1]
        bot.reply_to(message, f"💰 Saldo Anda: *{bal} USD*", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"❌ Gagal cek saldo: {bal_res}")

@bot.message_handler(commands=['order'])
def order_cmd(message):
    if not is_whitelisted(message.from_user.id):
        bot.reply_to(message, "🔒 Maaf, Anda tidak bisa mengakses bot ini.\nHub orang ganteng: @hesssxb")
        return
    update_user_info(message.from_user)
    log_activity(message.from_user.id, "order")
    api_key = get_user_api(message.from_user.id)
    if not api_key:
        bot.reply_to(message, "❌ Belum ada API Key. Gunakan `/setapi API_KEY`", parse_mode="Markdown")
        return

    # Tampilkan pilihan negara dulu
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"),
        InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia")
    )
    bot.send_message(message.chat.id, "🌍 *Pilih negara untuk order:*", parse_mode="Markdown", reply_markup=markup)

def process_bulk_order(chat_id, api_key, count, country_key="vietnam"):
    """Proses order banyak nomor sekaligus"""
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    country_label = get_country_label(country_key)
    country_id_str = str(country['country_id'])

    # Cek Harga dengan cara aman (timeout ketat agar tidak ganggu order)
    price_val = None
    try:
        # Panggil API dengan timeout lebih pendek khusus harga
        params = {'api_key': api_key, 'action': 'getPrices', 'service': SERVICE, 'country': country_id_str}
        r_p = requests.get(API_BASE, params=params, timeout=5)
        res_price = r_p.text.strip()
        
        if res_price and res_price.startswith("{"):
            try:
                data = json.loads(res_price)
                # Coba cari harga dengan berbagai format JSON (Country/Service atau Service/Country)
                inner = None
                if country_id_str in data and SERVICE in data[country_id_str]:
                    inner = data[country_id_str][SERVICE]
                elif SERVICE in data and country_id_str in data[SERVICE]:
                    inner = data[SERVICE][country_id_str]
                
                if inner and isinstance(inner, dict):
                    if "cost" in inner:
                        price_val = inner["cost"]
                    else:
                        # Jika formatnya {"0.18": 10, "0.20": 5}, ambil yang termurah
                        numeric_keys = [float(k) for k in inner.keys() if k.replace('.', '', 1).isdigit()]
                        if numeric_keys:
                            price_val = min(numeric_keys)
            except:
                pass
    except:
        pass

    # Beri jeda sangat singkat agar API tidak overload sebelum order
    if price_val:
        time.sleep(0.5)

    msg = bot.send_message(chat_id, f"⏳ Sedang memesan {count} nomor WA {country_label}...", parse_mode="Markdown")

    orders = []
    failed = 0

    for i in range(count):
        res = req_api(api_key, 'getNumber', service=SERVICE, country=country['country_id'])

        if 'ACCESS_NUMBER' in res:
            parts = res.split(':')
            if len(parts) >= 3:
                t_id = parts[1]
                number = parts[2]
                orders.append({
                    'id': t_id,
                    'number': number,
                    'status': 'waiting',
                    'code': None,
                    'order_time': time.time(),
                    'country_key': country_key,
                    'price': price_val
                })
        elif res == 'NO_BALANCE':
            bot.edit_message_text(
                f"❌ *Saldo tidak cukup!*\n\nBerhasil order {len(orders)} dari {count} nomor.",
                chat_id, msg.message_id, parse_mode="Markdown"
            )
            if not orders:
                return
            break
        elif res == 'NO_NUMBERS':
            failed += 1
            if failed >= 3 and not orders:
                bot.edit_message_text(f"❌ Nomor WA {country_label} sedang tidak tersedia.", chat_id, msg.message_id, parse_mode="Markdown")
                return
        else:
            failed += 1

        if i < count - 1:
            time.sleep(0.3)

    if not orders:
        bot.edit_message_text("❌ Gagal memesan nomor. Coba lagi nanti.", chat_id, msg.message_id, parse_mode="Markdown")
        return

    text = format_order_message(orders, f"🛒 *Order WA {country_label}*", country_key)

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton(f"⏳ Cancel tersedia ~2 menit lagi", callback_data="cancel_wait"))

    bot.edit_message_text(text, chat_id, msg.message_id, parse_mode="Markdown", reply_markup=markup)

    if chat_id not in active_orders:
        active_orders[chat_id] = {}
    active_orders[chat_id][msg.message_id] = orders

    thread = threading.Thread(
        target=auto_check_otp,
        args=(chat_id, msg.message_id, orders, api_key, country_key),
        daemon=True
    )
    thread.start()

# =============================================
# CALLBACK HANDLERS
# =============================================
@bot.callback_query_handler(func=lambda call: True)
def callback_q(call):
    user_id = call.from_user.id

    # Cek whitelist untuk callback juga
    if not is_whitelisted(user_id):
        bot.answer_callback_query(call.id, "🔒 Maaf, Anda tidak bisa mengakses bot ini. Hub orang ganteng: @hesssxb", show_alert=True)
        return

    api_key = get_user_api(user_id)
    data = call.data

    if not api_key:
        bot.answer_callback_query(call.id, "❌ Belum ada API Key. Gunakan /setapi", show_alert=True)
        return

    # Pilih negara → tampilkan submenu jumlah order
    if data.startswith("country_"):
        country_key = data.replace("country_", "")
        if country_key not in COUNTRIES:
            bot.answer_callback_query(call.id, "❌ Negara tidak valid.", show_alert=True)
            return

        country_label = get_country_label(country_key)
        bot.answer_callback_query(call.id, f"Negara: {country_label}")

        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("1️⃣", callback_data=f"quick_{country_key}_1"),
            InlineKeyboardButton("2️⃣", callback_data=f"quick_{country_key}_2"),
            InlineKeyboardButton("3️⃣", callback_data=f"quick_{country_key}_3"),
            InlineKeyboardButton("4️⃣", callback_data=f"quick_{country_key}_4"),
            InlineKeyboardButton("5️⃣", callback_data=f"quick_{country_key}_5")
        )
        markup.row(InlineKeyboardButton("⬅️ Kembali", callback_data="back_to_country"))

        text = f"🌍 *Negara: {country_label}*\n\nPilih jumlah nomor WA yang ingin di-order:"

        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        except:
            bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

    # Kembali ke pilihan negara
    elif data == "back_to_country" or data == "nav_order":
        bot.answer_callback_query(call.id)
        markup = InlineKeyboardMarkup()
        # Baris 1: Negara
        markup.row(
            InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"),
            InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia")
        )
        # Baris 2: Order & Cek Saldo
        markup.row(
            InlineKeyboardButton("🛒 Order Baru", callback_data="nav_order"),
            InlineKeyboardButton("💰 Cek Saldo", callback_data="nav_balance")
        )
        # Baris 3: Fitur Auto
        markup.row(
            InlineKeyboardButton("🔥 Auto Buy (VN)", callback_data="nav_autobuy"),
            InlineKeyboardButton("🛑 Stop Auto", callback_data="nav_stopauto")
        )
        
        text = "🌍 *Pilih negara untuk order:*"
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        except:
            bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

    # Quick order dengan negara
    elif data.startswith("quick_"):
        parts = data.split("_")
        # Format: quick_{country_key}_{count}
        if len(parts) == 3:
            country_key = parts[1]
            count = int(parts[2])
        else:
            # Legacy format: quick_{count} (default vietnam)
            country_key = "vietnam"
            count = int(parts[1])

        country_label = get_country_label(country_key)
        bot.answer_callback_query(call.id, f"Memesan {count} nomor {country_label}...")
        process_bulk_order(call.message.chat.id, api_key, count, country_key)

    # Cek saldo callback
    elif data == "nav_balance":
        bot.answer_callback_query(call.id)
        bal_res = req_api(api_key, 'getBalance')
        if 'ACCESS_BALANCE' in bal_res:
            bal = bal_res.split(':')[1]
            bot.send_message(call.message.chat.id, f"💰 Saldo Anda: *{bal} USD*", parse_mode="Markdown")
        else:
            bot.send_message(call.message.chat.id, f"❌ Gagal cek saldo: {bal_res}")

    elif data == "cancel_wait":
        bot.answer_callback_query(call.id, "⏳ Belum bisa cancel. Harus tunggu minimal 2 menit sejak order.", show_alert=True)
        
    elif data == "nav_autobuy":
        bot.answer_callback_query(call.id, "🔥 Mengaktifkan Auto Buy...")
        # Simulate message to reuse logic
        message = call.message
        message.from_user = call.from_user
        autobuy_cmd(message)
        
    elif data == "nav_stopauto":
        bot.answer_callback_query(call.id, "🛑 Menghentikan Auto Buy...")
        # Simulate message to reuse logic
        message = call.message
        message.from_user = call.from_user
        stopauto_cmd(message)

    elif data.startswith("cancelall_"):
        ids_str = data.split("_", 1)[1]
        ids_list = ids_str.split(",")
        cancelled = 0
        failed_cancel = 0

        chat_id = call.message.chat.id
        msg_id = call.message.message_id
        orders_ref = None
        if chat_id in active_orders and msg_id in active_orders[chat_id]:
            orders_ref = active_orders[chat_id][msg_id]

        # Tentukan country_key dari orders
        country_key = "vietnam"
        if orders_ref and orders_ref[0].get('country_key'):
            country_key = orders_ref[0]['country_key']

        for t_id in ids_list:
            try:
                res = req_api(api_key, 'setStatus', status='8', id=t_id)
                if 'ACCESS_CANCEL' in res:
                    cancelled += 1
                    if orders_ref:
                        for o in orders_ref:
                            if o['id'] == t_id and o['status'] == 'waiting':
                                o['status'] = 'cancelled'
                else:
                    failed_cancel += 1
            except:
                failed_cancel += 1

        bot.answer_callback_query(call.id, f"🚫 {cancelled} dibatalkan, {failed_cancel} gagal.", show_alert=True)

        try:
            country_label = get_country_label(country_key)
            if orders_ref:
                text = format_order_message(orders_ref, f"🛒 *Order WA {country_label} — Selesai*", country_key)
                bot.edit_message_text(text, chat_id, msg_id, parse_mode="Markdown")
            else:
                result_text = f"🚫 *{cancelled} order dibatalkan.*\nSaldo dikembalikan."
                if failed_cancel > 0:
                    result_text += f"\n⚠️ {failed_cancel} gagal dibatalkan."
                bot.edit_message_text(result_text, chat_id, msg_id, parse_mode="Markdown")
        except:
            pass

# =============================================
# AUTO-BUY (BRUTAL MODE)
# =============================================
autobuy_active = {}

def autobuy_worker(chat_id, api_key):
    bot.send_message(chat_id, "🔥 *AUTO BUY VIETNAM AKTIF (BRUTAL MODE)*\n\nMencari nomor nonstop sampai saldo habis...\nKetik /stopauto untuk berhenti.", parse_mode="Markdown")
    
    country_key = "vietnam"
    country = COUNTRIES[country_key]
    
    while autobuy_active.get(chat_id, False):
        res = req_api(api_key, 'getNumber', service=SERVICE, country=country['country_id'])
        
        if 'ACCESS_NUMBER' in res:
            parts = res.split(':')
            if len(parts) >= 3:
                t_id = parts[1]
                number = parts[2]
                
                # Fetch price for display
                price_val = None
                try:
                    params = {'api_key': api_key, 'action': 'getPrices', 'service': SERVICE, 'country': str(country['country_id'])}
                    r_p = requests.get(API_BASE, params=params, timeout=3)
                    p_data = json.loads(r_p.text.strip())
                    inner = None
                    c_id_str = str(country['country_id'])
                    if c_id_str in p_data and SERVICE in p_data[c_id_str]:
                        inner = p_data[c_id_str][SERVICE]
                    elif SERVICE in p_data and c_id_str in p_data[SERVICE]:
                        inner = p_data[SERVICE][c_id_str]
                    
                    if inner and isinstance(inner, dict):
                        if "cost" in inner:
                            price_val = inner["cost"]
                        else:
                            numeric_keys = [float(k) for k in inner.keys() if k.replace('.', '', 1).isdigit()]
                            if numeric_keys: price_val = min(numeric_keys)
                except: pass

                order = {
                    'id': t_id,
                    'number': number,
                    'status': 'waiting',
                    'code': None,
                    'order_time': time.time(),
                    'country_key': country_key,
                    'price': price_val
                }
                
                # Notify and start checking
                text = format_order_message([order], "🎯 *TARGET DIDAPATKAN (AUTO BUY)*", country_key)
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton("⏳ Cancel tersedia ~2 menit lagi", callback_data="cancel_wait"))
                
                try:
                    msg = bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
                    if chat_id not in active_orders:
                        active_orders[chat_id] = {}
                    active_orders[chat_id][msg.message_id] = [order]
                    threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, [order], api_key, country_key), daemon=True).start()
                except Exception as e:
                    print("Error sending autobuy message:", e)
                
                time.sleep(0.5) # small pause after success to prevent telegram rate limits
                
        elif res == 'NO_BALANCE':
            try:
                bot.send_message(chat_id, "❌ *AUTO BUY BERHENTI*\nSaldo Anda habis!", parse_mode="Markdown")
            except: pass
            autobuy_active[chat_id] = False
            break
        elif res == 'NO_NUMBERS':
            # Brutal mode: very short sleep to hammer the API
            time.sleep(0.1)
        elif 'BAD_KEY' in res or 'BANNED' in res:
            try:
                bot.send_message(chat_id, f"⚠️ *AUTO BUY BERHENTI*\nAkun API Anda bermasalah: `{res}`", parse_mode="Markdown")
            except: pass
            autobuy_active[chat_id] = False
            break
        elif res.startswith('ERROR'):
            # This is usually a network read timeout from our req_api wrapper.
            # In brutal war mode, we don't care, we just give it a tiny break and retry.
            time.sleep(0.5)
        else:
            time.sleep(0.5)

@bot.message_handler(commands=['autobuy'])
def autobuy_cmd(message):
    chat_id = message.chat.id
    if not is_whitelisted(message.from_user.id):
        bot.reply_to(message, "🔒 Maaf, Anda tidak bisa mengakses bot ini.\nHub orang ganteng: @hesssxb", parse_mode="Markdown")
        return
        
    api_key = get_user_api(message.from_user.id)
    if not api_key:
        bot.reply_to(message, "❌ Belum ada API Key. Gunakan `/setapi API_KEY`", parse_mode="Markdown")
        return
        
    if autobuy_active.get(chat_id, False):
        bot.reply_to(message, "⚠️ Autobuy sudah berjalan! Ketik /stopauto untuk menghentikan.")
        return
        
    autobuy_active[chat_id] = True
    threading.Thread(target=autobuy_worker, args=(chat_id, api_key), daemon=True).start()

@bot.message_handler(commands=['stopauto'])
def stopauto_cmd(message):
    chat_id = message.chat.id
    if autobuy_active.get(chat_id, False):
        autobuy_active[chat_id] = False
        bot.reply_to(message, "🛑 Autobuy berhasil dihentikan.")
    else:
        bot.reply_to(message, "⚠️ Tidak ada autobuy yang sedang berjalan.")

# =============================================
# CATCH-ALL: pesan dari user tidak dikenal
# =============================================
@bot.message_handler(func=lambda message: True)
def catch_all(message):
    if not is_whitelisted(message.from_user.id):
        bot.reply_to(message,
            "🔒 *Maaf, Anda tidak bisa mengakses bot ini.*\n\n"
            "Hub orang ganteng: @hesssxb",
            parse_mode="Markdown")

# =============================================
# MAIN
# =============================================
if __name__ == '__main__':
    init_db()
    print("GrizzlySMS Bot is running... (LOCKED MODE)")
    print(f"Admin ID: {ADMIN_ID}")
    bot.infinity_polling()
