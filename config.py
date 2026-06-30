import datetime
import telebot
from telebot import types
from flask import Flask, request
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError

from config import (
    BOT_TOKEN, MONGO_URI, MONGO_DB_NAME, WEBHOOK_HOST, PORT,
    ADMINS, ADMIN_USERNAME, CHANNELS,
)

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

# Admin "kino qo'shish" jarayoni uchun vaqtinchalik xotira (state machine)
user_states = {}

# ============================== MONGODB ==============================
client = MongoClient(MONGO_URI)
db = client[MONGO_DB_NAME]
movies_col = db["movies"]
users_col = db["users"]

movies_col.create_index([("movie_id", ASCENDING)], unique=True)


# ============================== DATABASE FUNKSIYALARI ==============================
def add_user(user_id):
    users_col.update_one(
        {"_id": user_id},
        {"$setOnInsert": {
            "joined_date": datetime.datetime.utcnow(),
            "is_premium": False,
            "premium_until": None,
        }},
        upsert=True,
    )


def get_all_users():
    return [u["_id"] for u in users_col.find({}, {"_id": 1})]


def add_movie(movie_id, title, file_id, is_premium=False):
    try:
        movies_col.insert_one({
            "movie_id": movie_id,
            "title": title,
            "file_id": file_id,
            "is_premium": is_premium,
            "added_date": datetime.datetime.utcnow(),
        })
        return True
    except DuplicateKeyError:
        return False


def delete_movie(movie_id):
    result = movies_col.delete_one({"movie_id": movie_id})
    return result.deleted_count > 0


def get_movie_by_id(movie_id):
    return movies_col.find_one({"movie_id": movie_id})


def search_movies_by_title(text):
    return list(movies_col.find({"title": {"$regex": text, "$options": "i"}}).limit(20))


def get_movies_count():
    return movies_col.count_documents({})


def get_users_count():
    return users_col.count_documents({})


def get_premium_count():
    return users_col.count_documents({"is_premium": True})


def set_premium(user_id, days):
    until = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    users_col.update_one(
        {"_id": user_id},
        {"$set": {"is_premium": True, "premium_until": until}},
        upsert=True,
    )
    return until


def remove_premium(user_id):
    users_col.update_one({"_id": user_id}, {"$set": {"is_premium": False, "premium_until": None}})


def is_premium_user(user_id):
    if is_admin(user_id):
        return True
    user = users_col.find_one({"_id": user_id})
    if not user or not user.get("is_premium"):
        return False
    until = user.get("premium_until")
    if until and until > datetime.datetime.utcnow():
        return True
    # muddati tugagan bo'lsa, avtomatik o'chiramiz
    remove_premium(user_id)
    return False


def get_premium_until(user_id):
    user = users_col.find_one({"_id": user_id})
    return user.get("premium_until") if user else None


# ============================== YORDAMCHI ==============================
def is_admin(user_id):
    return user_id in ADMINS


def check_subscription(user_id):
    """Premium foydalanuvchi va adminlar majburiy obunadan ozod"""
    if is_premium_user(user_id):
        return True
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
        "Obuna bo'lgach, pastdagi \"✅ Obuna bo'ldim\" tugmasini bosing.\n\n"
        "💎 <i>Premium foydalanuvchilar majburiy obunasiz foydalanishi mumkin.</i>",
        parse_mode="HTML",
        reply_markup=subscription_keyboard(),
    )


def send_movie(chat_id, user_id, movie):
    if movie.get("is_premium") and not is_premium_user(user_id):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="💎 Premium olish", url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"))
        bot.send_message(
            chat_id,
            "💎 <b>Bu kino faqat Premium foydalanuvchilar uchun!</b>\n\n"
            f"Premium olish uchun {ADMIN_USERNAME} ga murojaat qiling.",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    badge = "💎 PREMIUM" if movie.get("is_premium") else "🎬"
    caption = f"{badge} <b>{movie['title']}</b>\n🆔 Kino kodi: <code>{movie['movie_id']}</code>"
    bot.send_video(chat_id, movie["file_id"], caption=caption, parse_mode="HTML")


# ============================== FOYDALANUVCHI BUYRUQLARI ==============================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    add_user(message.from_user.id)

    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    status = "💎 <b>Siz Premium foydalanuvchisiz!</b>\n\n" if is_premium_user(message.from_user.id) else ""
    bot.send_message(
        message.chat.id,
        f"{status}🎥 <b>Kino botga xush kelibsiz!</b>\n\n"
        "Kino topish uchun kino <b>kodini</b> yoki <b>nomini</b> yuboring.\n\n"
        "Masalan: <code>1</code> yoki <i>Avengers</i>\n\n"
        "💎 Premium haqida: /premium",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["premium"])
def cmd_premium_status(message):
    user_id = message.from_user.id
    if is_premium_user(user_id):
        until = get_premium_until(user_id)
        until_str = until.strftime("%Y-%m-%d") if until else "♾ cheksiz"
        bot.send_message(
            message.chat.id,
            f"💎 <b>Siz Premium foydalanuvchisiz!</b>\n📅 Muddati: <code>{until_str}</code>\n\n"
            "✅ Majburiy obunasiz foydalanish\n✅ VIP kinolarga kirish",
            parse_mode="HTML",
        )
    else:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="💎 Premium sotib olish", url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"))
        bot.send_message(
            message.chat.id,
            "💎 <b>Premium imkoniyatlari:</b>\n\n"
            "✅ Majburiy obunasiz foydalanish\n"
            "✅ VIP kinolarga maxsus kirish\n"
            "✅ Tezroq va qulayroq xizmat\n\n"
            f"Sotib olish uchun {ADMIN_USERNAME} ga yozing.",
            parse_mode="HTML",
            reply_markup=markup,
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
        send_movie(call.message.chat.id, call.from_user.id, movie)
    bot.answer_callback_query(call.id)


# ============================== ADMIN: KINO BOSHQARUVI ==============================
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
        f"📊 <b>Statistika</b>\n\n"
        f"🎬 Kinolar soni: {get_movies_count()}\n"
        f"👥 Foydalanuvchilar soni: {get_users_count()}\n"
        f"💎 Premium foydalanuvchilar: {get_premium_count()}",
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


# ============================== ADMIN: PREMIUM BOSHQARUVI ==============================
@bot.message_handler(commands=["addpremium"])
def cmd_add_premium(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        bot.send_message(message.chat.id, "❗ Foydalanish: /addpremium <user_id> <kun_soni>")
        return
    user_id, days = int(parts[1]), int(parts[2])
    until = set_premium(user_id, days)
    bot.send_message(message.chat.id, f"💎 {user_id} ga {days} kunlik Premium berildi (tugash sanasi: {until.strftime('%Y-%m-%d')})")
    try:
        bot.send_message(user_id, f"🎉 Sizga {days} kunlik 💎 Premium status berildi!\n\nKo'rish: /premium")
    except Exception:
        pass


@bot.message_handler(commands=["delpremium"])
def cmd_del_premium(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.send_message(message.chat.id, "❗ Foydalanish: /delpremium <user_id>")
        return
    remove_premium(int(parts[1]))
    bot.send_message(message.chat.id, f"❌ {parts[1]} uchun Premium bekor qilindi.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("movietype:"))
def callback_movie_type(call):
    user_id = call.from_user.id
    state = user_states.get(user_id)
    if not state or state["step"] != "waiting_type" or not is_admin(user_id):
        return

    is_premium = call.data.split(":", 1)[1] == "premium"
    data = state["data"]
    add_movie(data["movie_id"], data["title"], data["file_id"], is_premium=is_premium)

    type_text = "💎 Premium (VIP)" if is_premium else "🎬 Oddiy"
    bot.edit_message_text(
        f"✅ Kino qo'shildi!\n\n🆔 Kod: {data['movie_id']}\n📝 Nomi: {data['title']}\n🏷 Turi: {type_text}",
        call.message.chat.id, call.message.message_id,
    )
    user_states.pop(user_id, None)
    bot.answer_callback_query(call.id)


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
        return

    text = message.text.strip()
    if text.startswith("/"):
        return

    # ---- ADMIN: ID KIRITISH ----
    if state and state["step"] == "waiting_id" and is_admin(user_id):
        if get_movie_by_id(text):
            bot.send_message(message.chat.id, "❗ Bu kod band. Boshqa kod kiriting:")
            return
        state["data"]["movie_id"] = text
        state["step"] = "waiting_title"
        bot.send_message(message.chat.id, "📝 Endi kino nomini kiriting:")
        return

    # ---- ADMIN: NOM KIRITISH ----
    if state and state["step"] == "waiting_title" and is_admin(user_id):
        state["data"]["title"] = text
        state["step"] = "waiting_type"
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(text="🎬 Oddiy", callback_data="movietype:normal"),
            types.InlineKeyboardButton(text="💎 Premium (VIP)", callback_data="movietype:premium"),
        )
        bot.send_message(message.chat.id, "🏷 Kino turini tanlang:", reply_markup=markup)
        return

    # ---- ODDIY FOYDALANUVCHI: QIDIRUV ----
    add_user(user_id)

    if not check_subscription(user_id):
        send_subscription_message(message.chat.id)
        return

    movie = get_movie_by_id(text)
    if movie:
        send_movie(message.chat.id, user_id, movie)
        return

    results = search_movies_by_title(text)
    if not results:
        bot.send_message(message.chat.id, "😔 Hech narsa topilmadi. Kodni yoki nomni tekshirib qayta yuboring.")
        return

    if len(results) == 1:
        send_movie(message.chat.id, user_id, results[0])
        return

    markup = types.InlineKeyboardMarkup()
    for m in results:
        label = ("💎 " if m.get("is_premium") else "🎬 ") + m["title"]
        markup.add(types.InlineKeyboardButton(text=label, callback_data=f"get_movie:{m['movie_id']}"))
    bot.send_message(message.chat.id, "🔍 Quyidagi natijalar topildi:", reply_markup=markup)


# ============================== FLASK WEBHOOK (RENDER UCHUN) ==============================
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200


@app.route("/")
def index():
    return "🎬 Kino bot ishlayapti ✅", 200


def setup_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=f"{WEBHOOK_HOST}/{BOT_TOKEN}")


# Modul import qilinganda ham (gunicorn orqali) webhook o'rnatiladi
setup_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
