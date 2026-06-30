import sqlite3
import telebot
from telebot import types
from config import BOT_TOKEN, ADMINS, CHANNELS, DB_NAME

bot = telebot.TeleBot(BOT_TOKEN)

# Admin "kino qo'shish" jarayoni uchun vaqtinchalik xotira (state machine)
user_states = {}


# ============================== DATABASE ==============================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movie_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            added_date TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            joined_date TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def add_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def add_movie(movie_id, title, file_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO movies (movie_id, title, file_id) VALUES (?, ?, ?)",
            (movie_id, title, file_id),
        )
        conn.commit()
        ok = True
    except sqlite3.IntegrityError:
        ok = False
    conn.close()
    return ok


def delete_movie(movie_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM movies WHERE movie_id = ?", (movie_id,))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed > 0


def get_movie_by_id(movie_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT movie_id, title, file_id FROM movies WHERE movie_id = ?", (movie_id,))
    row = cur.fetchone()
    conn.close()
    return row


def search_movies_by_title(text):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT movie_id, title, file_id FROM movies WHERE title LIKE ? LIMIT 20",
        (f"%{text}%",),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_movies_count():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM movies")
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_users_count():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    return count


# ============================== YORDAMCHI ==============================
def is_admin(user_id):
    return user_id in ADMINS


def check_subscription(user_id):
    """Foydalanuvchi BARCHA majburiy kanallarga obuna bo'lganmi tekshiradi"""
    for channel in CHANNELS:
        try:
            member = bot.get_chat_member(channel["id"], user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception:
            return False
    return True


def subscription_keyboard():
    markup = types.InlineKeyboardMarkup()
    for channel in CHANNELS:
        markup.add(types.InlineKeyboardButton(text=f"📢 {channel['name']}", url=channel["url"]))
    markup.add(types.InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_sub"))
    return markup


def send_subscription_message(chat_id):
    bot.send_message(
        chat_id,
        "🔒 <b>Botdan foydalanish uchun quyidagi kanal(lar)ga obuna bo'ling!</b>\n\n"
        "Obuna bo'lgach, pastdagi \"✅ Obuna bo'ldim\" tugmasini bosing.",
        parse_mode="HTML",
        reply_markup=subscription_keyboard(),
    )


def send_movie(chat_id, movie):
    movie_id, title, file_id = movie
    caption = f"🎬 <b>{title}</b>\n🆔 Kino kodi: <code>{movie_id}</code>"
    bot.send_video(chat_id, file_id, caption=caption, parse_mode="HTML")


# ============================== FOYDALANUVCHI BUYRUQLARI ==============================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    add_user(message.from_user.id)

    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    bot.send_message(
        message.chat.id,
        "🎥 <b>Kino botga xush kelibsiz!</b>\n\n"
        "Kino topish uchun kino <b>kodini</b> yoki <b>nomini</b> yuboring.\n\n"
        "Masalan: <code>1</code> yoki <i>Avengers</i>",
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
def callback_check_sub(call):
    if check_subscription(call.from_user.id):
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.send_message(call.message.chat.id, "✅ Obuna tasdiqlandi!\n\nEndi kino kodini yoki nomini yuboring.")
    else:
        bot.answer_callback_query(call.id, "❌ Siz hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data.startswith("get_movie:"))
def callback_get_movie(call):
    movie_id = call.data.split(":", 1)[1]
    movie = get_movie_by_id(movie_id)
    if movie:
        send_movie(call.message.chat.id, movie)
    bot.answer_callback_query(call.id)


# ============================== ADMIN BUYRUQLARI ==============================
@bot.message_handler(commands=["add"])
def cmd_add(message):
    if not is_admin(message.from_user.id):
        return
    user_states[message.from_user.id] = {"step": "waiting_video", "data": {}}
    bot.send_message(message.chat.id, "🎬 Kino faylini (video) yuboring:")


@bot.message_handler(commands=["delete"])
def cmd_delete(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❗ Foydalanish: /delete <kino_kodi>")
        return
    movie_id = parts[1].strip()
    if delete_movie(movie_id):
        bot.send_message(message.chat.id, f"🗑 <code>{movie_id}</code> kodli kino o'chirildi.", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "❌ Bunday kodli kino topilmadi.")


@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(
        message.chat.id,
        f"📊 <b>Statistika</b>\n\n🎬 Kinolar soni: {get_movies_count()}\n👥 Foydalanuvchilar soni: {get_users_count()}",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["xabar"])
def cmd_broadcast(message):
    if not is_admin(message.from_user.id):
        return
    text = message.text.replace("/xabar", "", 1).strip()
    if not text:
        bot.send_message(message.chat.id, "❗ Foydalanish: /xabar <matn>")
        return
    success, fail = 0, 0
    for uid in get_all_users():
        try:
            bot.send_message(uid, text)
            success += 1
        except Exception:
            fail += 1
    bot.send_message(message.chat.id, f"✅ Yuborildi: {success}\n❌ Yuborilmadi: {fail}")


# ============================== ASOSIY MESSAGE HANDLER ==============================
@bot.message_handler(content_types=["text", "video", "document"])
def handle_message(message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    # ---- ADMIN: VIDEO KUTILYAPTI ----
    if state and state["step"] == "waiting_video" and is_admin(user_id):
        if message.content_type == "video":
            file_id = message.video.file_id
        elif message.content_type == "document":
            file_id = message.document.file_id
        else:
            bot.send_message(message.chat.id, "❗ Iltimos video fayl yuboring.")
            return
        state["data"]["file_id"] = file_id
        state["step"] = "waiting_id"
        bot.send_message(message.chat.id, "🆔 Endi kino uchun ID (kod) kiriting (masalan: 1):")
        return

    if message.content_type != "text":
        return  # video/document holatdan tashqari e'tiborsiz qoldiriladi

    text = message.text.strip()
    if text.startswith("/"):
        return  # buyruqlar yuqorida alohida ushlanadi

    # ---- ADMIN: ID KIRITISH BOSQICHI ----
    if state and state["step"] == "waiting_id" and is_admin(user_id):
        if get_movie_by_id(text):
            bot.send_message(message.chat.id, "❗ Bu kod band. Boshqa kod kiriting:")
            return
        state["data"]["movie_id"] = text
        state["step"] = "waiting_title"
        bot.send_message(message.chat.id, "📝 Endi kino nomini kiriting:")
        return

    # ---- ADMIN: NOM KIRITISH BOSQICHI ----
    if state and state["step"] == "waiting_title" and is_admin(user_id):
        data = state["data"]
        add_movie(data["movie_id"], text, data["file_id"])
        bot.send_message(
            message.chat.id,
            f"✅ Kino qo'shildi!\n\n🆔 Kod: <code>{data['movie_id']}</code>\n📝 Nomi: {text}",
            parse_mode="HTML",
        )
        user_states.pop(user_id, None)
        return

    # ---- ODDIY FOYDALANUVCHI: QIDIRUV ----
    add_user(user_id)

    if not check_subscription(user_id):
        send_subscription_message(message.chat.id)
        return

    # avval ID (kod) bo'yicha qidiramiz
    movie = get_movie_by_id(text)
    if movie:
        send_movie(message.chat.id, movie)
        return

    # topilmasa, nomi bo'yicha qidiramiz
    results = search_movies_by_title(text)
    if not results:
        bot.send_message(message.chat.id, "😔 Hech narsa topilmadi. Kodni yoki nomni tekshirib qayta yuboring.")
        return

    if len(results) == 1:
        send_movie(message.chat.id, results[0])
        return

    markup = types.InlineKeyboardMarkup()
    for movie_id, title, _ in results:
        markup.add(types.InlineKeyboardButton(text=f"🎬 {title}", callback_data=f"get_movie:{movie_id}"))
    bot.send_message(message.chat.id, "🔍 Quyidagi natijalar topildi:", reply_markup=markup)


# ============================== ISHGA TUSHIRISH ==============================
if __name__ == "__main__":
    init_db()
    print("Bot ishga tushdi...")
    bot.infinity_polling(skip_pending=True)
