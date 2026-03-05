import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import sqlite3
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

MAX_ORDER = 20         # Maksimal order sekaligus
OTP_TIMEOUT = 1200     # Timeout 20 menit (1200 detik)
CHECK_INTERVAL = 5     # Cek OTP setiap 5 detik
CANCEL_DELAY = 120     # Baru bisa cancel setelah 2 menit (120 detik)
COUNTRY_CODE = "84"    # Vietnam country code
COUNTRY_ID = "10"      # Vietnam country ID di GrizzlySMS
SERVICE = "wa"         # WhatsApp service

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
    conn.commit()
    conn.close()

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

def strip_country_code(number):
    """Hapus country code +84 dari nomor Vietnam, sisakan nomor lokal saja"""
    number = number.strip()
    if number.startswith("+"):
        number = number[1:]
    if number.startswith(COUNTRY_CODE):
        number = number[len(COUNTRY_CODE):]
    return number

# =============================================
# FORMAT PESAN ORDER
# =============================================
def format_order_message(orders, title=""):
    """Format pesan daftar order dengan status OTP"""
    lines = []
    if title:
        lines.append(title)
        lines.append("")

    done_count = 0
    total = len(orders)
    now = time.time()

    for i, order in enumerate(orders, 1):
        number_local = strip_country_code(order['number'])
        status = order.get('status', 'waiting')

        if status == 'waiting':
            # Hitung sisa waktu
            elapsed = now - order.get('order_time', now)
            remaining = max(0, OTP_TIMEOUT - elapsed)
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            lines.append(f"{i}. `{number_local}` — ⏳ Menunggu OTP... ({mins}m {secs}s)")
        elif status == 'got_otp':
            code = order.get('code', '???')
            lines.append(f"{i}. `{number_local}` — ✅ OTP: `{code}`")
            done_count += 1
        elif status == 'cancelled':
            lines.append(f"{i}. `{number_local}` — 🚫 Dibatalkan (Refund)")
            done_count += 1
        elif status == 'timeout':
            lines.append(f"{i}. `{number_local}` — ⏰ Timeout (20 menit)")
            done_count += 1
        elif status == 'error':
            lines.append(f"{i}. `{number_local}` — ❌ Error")
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
def auto_check_otp(chat_id, message_id, orders, api_key):
    """Background thread yang otomatis cek OTP untuk semua order"""
    start_time = time.time()
    last_edit_time = 0
    EDIT_COOLDOWN = 3
    TIMER_UPDATE = 15
    last_timer_update = 0

    try:
        while True:
            waiting_orders = [o for o in orders if o['status'] == 'waiting']
            if not waiting_orders:
                text = format_order_message(orders, "🛒 *Order WA Vietnam — Selesai*")
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
                text = format_order_message(orders, "🛒 *Order WA Vietnam — Timeout*")
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
                text = format_order_message(orders, "🛒 *Order WA Vietnam*")

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
            text = format_order_message(orders, "🛒 *Order WA Vietnam — Error*")
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
@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_id = message.from_user.id
    api_key = get_user_api(user_id)

    text = (
        "🐻 *Bot OTP WhatsApp Vietnam (GrizzlySMS)* 🇻🇳\n\n"
        "Bot ini khusus untuk order nomor WhatsApp Vietnam.\n"
        "OTP akan otomatis muncul di bawah nomor masing-masing.\n\n"
        "📋 *Perintah:*\n"
        "`/setapi API_KEY` — Daftarkan API Key GrizzlySMS\n"
        "`/order N` — Order N nomor sekaligus (maks 20)\n"
        "`/balance` — Cek saldo\n"
        "`/help` — Bantuan\n\n"
    )

    if api_key:
        bal_res = req_api(api_key, 'getBalance')
        if 'ACCESS_BALANCE' in bal_res:
            bal = bal_res.split(':')[1]
            text += f"✅ API Key: Terdaftar\n💰 Saldo: *{bal} RUB*"
        else:
            text += "⚠️ API Key terdaftar tapi tidak valid.\nGunakan `/setapi API_KEY` untuk mengganti."
    else:
        text += "❌ Belum ada API Key.\nGunakan `/setapi API_KEY` untuk mendaftar."

    markup = InlineKeyboardMarkup()
    if api_key:
        markup.row(InlineKeyboardButton("🛒 Order 1 Nomor", callback_data="quick_1"))
        markup.row(
            InlineKeyboardButton("🛒 Order 5", callback_data="quick_5"),
            InlineKeyboardButton("🛒 Order 10", callback_data="quick_10")
        )
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['help'])
def help_cmd(message):
    text = (
        "📖 *Panduan Penggunaan*\n\n"
        "1️⃣ Daftarkan API Key dari akun GrizzlySMS Anda:\n"
        "   `/setapi API_KEY_ANDA`\n\n"
        "   Dapatkan API Key di: https://grizzlysms.com/docs\n\n"
        "2️⃣ Order nomor WA Vietnam (contoh 5 nomor):\n"
        "   `/order 5`\n\n"
        "3️⃣ Bot akan otomatis cek OTP setiap 5 detik.\n"
        "   Ketika OTP masuk, akan langsung muncul di bawah nomor.\n\n"
        "4️⃣ Salin nomor (tanpa +84) langsung dari chat.\n\n"
        "⏱ Timeout: 20 menit per order\n"
        "🚫 Cancel: tersedia setelah 2 menit\n"
        "📱 Maks order: 20 nomor sekaligus\n\n"
        "💰 Cek saldo: `/balance`"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['setapi'])
def setapi_cmd(message):
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
        bot.send_message(message.chat.id, f"✅ API Key valid & tersimpan!\n💰 Saldo: *{bal} RUB*\n\nKetik `/order 5` untuk mulai order.", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ API Key tidak valid atau server gangguan.")

@bot.message_handler(commands=['balance'])
def balance_cmd(message):
    api_key = get_user_api(message.from_user.id)
    if not api_key:
        bot.reply_to(message, "❌ Belum ada API Key. Gunakan `/setapi API_KEY`", parse_mode="Markdown")
        return

    bal_res = req_api(api_key, 'getBalance')
    if 'ACCESS_BALANCE' in bal_res:
        bal = bal_res.split(':')[1]
        bot.reply_to(message, f"💰 Saldo Anda: *{bal} RUB*", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"❌ Gagal cek saldo: {bal_res}")

@bot.message_handler(commands=['order'])
def order_cmd(message):
    api_key = get_user_api(message.from_user.id)
    if not api_key:
        bot.reply_to(message, "❌ Belum ada API Key. Gunakan `/setapi API_KEY`", parse_mode="Markdown")
        return

    parts = message.text.split()
    count = 1
    if len(parts) >= 2:
        try:
            count = int(parts[1])
        except ValueError:
            bot.reply_to(message, "❌ Format: `/order 5` (angka 1-20)", parse_mode="Markdown")
            return

    if count < 1 or count > MAX_ORDER:
        bot.reply_to(message, f"❌ Jumlah harus antara 1 dan {MAX_ORDER}.", parse_mode="Markdown")
        return

    process_bulk_order(message.chat.id, api_key, count)

def process_bulk_order(chat_id, api_key, count):
    """Proses order banyak nomor sekaligus"""
    msg = bot.send_message(chat_id, f"⏳ Sedang memesan {count} nomor WA Vietnam...", parse_mode="Markdown")

    orders = []
    failed = 0

    for i in range(count):
        res = req_api(api_key, 'getNumber', service=SERVICE, country=COUNTRY_ID)

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
                    'order_time': time.time()
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
                bot.edit_message_text("❌ Nomor WA Vietnam sedang tidak tersedia.", chat_id, msg.message_id, parse_mode="Markdown")
                return
        else:
            failed += 1

        if i < count - 1:
            time.sleep(0.3)

    if not orders:
        bot.edit_message_text("❌ Gagal memesan nomor. Coba lagi nanti.", chat_id, msg.message_id, parse_mode="Markdown")
        return

    text = format_order_message(orders, "🛒 *Order WA Vietnam*")

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton(f"⏳ Cancel tersedia ~2 menit lagi", callback_data="cancel_wait"))

    bot.edit_message_text(text, chat_id, msg.message_id, parse_mode="Markdown", reply_markup=markup)

    if chat_id not in active_orders:
        active_orders[chat_id] = {}
    active_orders[chat_id][msg.message_id] = orders

    thread = threading.Thread(
        target=auto_check_otp,
        args=(chat_id, msg.message_id, orders, api_key),
        daemon=True
    )
    thread.start()

# =============================================
# CALLBACK HANDLERS
# =============================================
@bot.callback_query_handler(func=lambda call: True)
def callback_q(call):
    user_id = call.from_user.id
    api_key = get_user_api(user_id)
    data = call.data

    if not api_key:
        bot.answer_callback_query(call.id, "❌ Belum ada API Key. Gunakan /setapi", show_alert=True)
        return

    if data.startswith("quick_"):
        count = int(data.split("_")[1])
        bot.answer_callback_query(call.id, f"Memesan {count} nomor...")
        process_bulk_order(call.message.chat.id, api_key, count)

    elif data == "cancel_wait":
        bot.answer_callback_query(call.id, "⏳ Belum bisa cancel. Harus tunggu minimal 2 menit sejak order.", show_alert=True)

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
            if orders_ref:
                text = format_order_message(orders_ref, "🛒 *Order WA Vietnam — Selesai*")
                bot.edit_message_text(text, chat_id, msg_id, parse_mode="Markdown")
            else:
                result_text = f"🚫 *{cancelled} order dibatalkan.*\nSaldo dikembalikan."
                if failed_cancel > 0:
                    result_text += f"\n⚠️ {failed_cancel} gagal dibatalkan."
                bot.edit_message_text(result_text, chat_id, msg_id, parse_mode="Markdown")
        except:
            pass

# =============================================
# MAIN
# =============================================
if __name__ == '__main__':
    init_db()
    print("GrizzlySMS Bot is running...")
    bot.infinity_polling()
