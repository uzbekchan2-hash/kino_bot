import datetime
import functools
import logging
import time

import telebot
from telebot import types
from flask import Flask, request, abort
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, ConnectionFailure, ServerSelectionTimeoutError

from config import (
    BOT_TOKEN, MONGO_URI, MONGO_DB_NAME, WEBHOOK_HOST, PORT, WEBHOOK_SECRET,
    ADMINS, ADMIN_USERNAME, CHANNELS,
)

# ============================== LOGGING ==============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("kino_bot")

# ============================== BOT & FLASK ==============================
bot = telebot.TeleBot(BOT_TOKEN, threaded=False, parse_mode="HTML")
app = Flask(__name__)

# Har bir foydalanuvchi uchun vaqtinchalik holat (admin bosqichlari uchun)
user_states = {}


# ============================== MONGODB (xato bo'lsa qayta urinadi) ==============================
def connect_mongo(retries=5, delay=3):
    for attempt in range(1, retries + 1):
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            client.admin.command("ping")
            log.info("MongoDB ulanish muvaffaqiyatli (urinish %d).", attempt)
            return client
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            log.error("MongoDB ulanishda xato (urinish %d/%d): %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(delay)
    log.critical("MongoDB ga ulanib bo'lmadi! MONGO_URI va Network Access (0.0.0.0/0) sozlamasini tekshiring.")
    raise SystemExit(1)


mongo_client = connect_mongo()
db = mongo_client[MONGO_DB_NAME]
movies_col = db["movies"]
users_col = db["users"]

try:
    movies_col.create_index([("movie_id", ASCENDING)], unique=True)
except Exception as e:
    log.warning("Index yaratishda ogohlantirish: %s", e)


# ============================== XATOLARDAN HIMOYA QILUVCHI DEKORATOR ==============================
def safe_handler(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            log.exception("Handler '%s' ichida xato: %s", func.__name__, e)
            try:
                target = args[0]
                chat_id = None
                if hasattr(target, "chat"):
                    chat_id = target.chat.id
                elif hasattr(target, "message"):
                    chat_id = target.message.chat.id
                if chat_id:
                    bot.send_message(chat_id, "⚠️ Kutilmagan xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
            except Exception:
                pass
    return wrapper


# ============================== DATABASE FUNKSIYALARI ==============================
def add_user(user_id):
    try:
        users_col.update_one(
            {"_id": user_id},
            {"$setOnInsert": {
                "joined_date": datetime.datetime.utcnow(),
                "is_premium": False,
                "premium_until": None,
            }},
            upsert=True,
        )
    except Exception as e:
        log.error("add_user xato: %s", e)


def get_all_users():
    try:
        return [u["_id"] for u in users_col.find({}, {"_id": 1})]
    except Exception as e:
        log.error("get_all_users xato: %s", e)
        return []


def add_movie(movie_id, title, file_id, is_premium=False):
    try:
        movies_col.insert_one({
            "movie_id": movie_id,
            "title": title,
            "file_id": file_id,
            "is_premium": is_premium,
            "views": 0,
            "added_date": datetime.datetime.utcnow(),
        })
        return True
    except DuplicateKeyError:
        return False
    except Exception as e:
        log.error("add_movie xato: %s", e)
        return False


def delete_movie(movie_id):
    try:
        result = movies_col.delete_one({"movie_id": movie_id})
        return result.deleted_count > 0
    except Exception as e:
        log.error("delete_movie xato: %s", e)
        return False


def get_movie_by_id(movie_id):
    try:
        return movies_col.find_one({"movie_id": movie_id})
    except Exception as e:
        log.error("get_movie_by_id xato: %s", e)
        return None


def increment_views(movie_id):
    try:
        movies_col.update_one({"movie_id": movie_id}, {"$inc": {"views": 1}})
    except Exception as e:
        log.error("increment_views xato: %s", e)


def search_movies_by_title(text):
    try:
        return list(movies_col.find({"title": {"$regex": text, "$options": "i"}}).limit(20))
    except Exception as e:
        log.error("search_movies_by_title xato: %s", e)
        return []


def get_latest_movies(limit=10):
    try:
        return list(movies_col.find().sort("added_date", DESCENDING).limit(limit))
    except Exception as e:
        log.error("get_latest_movies xato: %s", e)
        return []


def get_top_movies(limit=10):
    try:
        return list(movies_col.find().sort("views", DESCENDING).limit(limit))
    except Exception as e:
        log.error("get_top_movies xato: %s", e)
        return []


def get_movies_count():
    try:
        return movies_col.count_documents({})
    except Exception:
        return 0


def get_users_count():
    try:
        return users_col.count_documents({})
    except Exception:
        return 0


def get_premium_count():
    try:
        return users_col.count_documents({"is_premium": True})
    except Exception:
        return 0


def set_premium(user_id, days):
    until = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"is_premium": True, "premium_until": until}},
            upsert=True,
        )
    except Exception as e:
        log.error("set_premium xato: %s", e)
    return until


def remove_premium(user_id):
    try:
        users_col.update_one({"_id": user_id}, {"$set": {"is_premium": False, "premium_until": None}})
    except Exception as e:
        log.error("remove_premium xato: %s", e)


def is_premium_user(user_id):
    if is_admin(user_id):
        return True
    try:
        user = users_col.find_one({"_id": user_id})
    except Exception as e:
        log.error("is_premium_user xato: %s", e)
        return False
    if not user or not user.get("is_premium"):
        return False
    until = user.get("premium_until")
    if until and until > datetime.datetime.utcnow():
        return True
    remove_premium(user_id)
    return False


def get_premium_until(user_id):
    try:
        user = users_col.find_one({"_id": user_id})
        return user.get("premium_until") if user else None
    except Exception:
        return None


# ============================== YORDAMCHI ==============================
def is_admin(user_id):
    return user_id in ADMINS


def check_subscription(user_id):
    if is_premium_user(user_id):
        return True
    if not CHANNELS:
        return True
    for channel in CHANNELS:
        try:
            member = bot.get_chat_member(channel["id"], user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception as e:
            log.warning("Obunani tekshirishda xato (%s): %s — bot kanalda ADMIN ekanini tekshiring!", channel["id"], e)
            return False
    return True


def subscription_keyboard():
    markup = types.InlineKeyboardMarkup()
    for channel in CHANNELS:
        markup.add(types.InlineKeyboardButton(text=f"📢 {channel['name']}", url=channel["url"]))
    markup.add(types.InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_sub"))
    return markup


def send_subscription_message(chat_id):
    try:
        bot.send_message(
            chat_id,
            "🔒 <b>Botdan foydalanish uchun quyidagi kanal(lar)ga obuna bo'ling!</b>\n\n"
            "Obuna bo'lgach, pastdagi \"✅ Obuna bo'ldim\" tugmasini bosing.\n\n"
            "💎 <i>Premium foydalanuvchilar majburiy obunasiz foydalanishi mumkin.</i>",
            reply_markup=subscription_keyboard(),
        )
    except Exception as e:
        log.error("send_subscription_message xato: %s", e)


def send_movie(chat_id, user_id, movie):
    try:
        if movie.get("is_premium") and not is_premium_user(user_id):
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(text="💎 Premium olish", url=f"https://t.me/{ADMIN_USERNAME}"))
            bot.send_message(
                chat_id,
                "💎 <b>Bu kino faqat Premium foydalanuvchilar uchun!</b>\n\n"
                f"Premium olish uchun @{ADMIN_USERNAME} ga murojaat qiling.",
                reply_markup=markup,
            )
            return

        badge = "💎 PREMIUM" if movie.get("is_premium") else "🎬"
        views = movie.get("views", 0) + 1
        caption = (
            f"{badge} <b>{movie['title']}</b>\n"
            f"🆔 Kino kodi: <code>{movie['movie_id']}</code>\n"
            f"👁 Ko'rishlar: {views}"
        )
        bot.send_video(chat_id, movie["file_id"], caption=caption)
        increment_views(movie["movie_id"])
    except Exception as e:
        log.error("send_movie xato: %s", e)
        try:
            bot.send_message(chat_id, "⚠️ Kinoni yuborishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        except Exception:
            pass


# ============================== FOYDALANUVCHI BUYRUQLARI ==============================
@bot.message_handler(commands=["start"])
@safe_handler
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
        "🆕 So'nggi kinolar: /oxirgi\n"
        "🔥 Top kinolar: /top\n"
        "💎 Premium haqida: /premium",
    )


@bot.message_handler(commands=["premium"])
@safe_handler
def cmd_premium_status(message):
    user_id = message.from_user.id
    if is_premium_user(user_id):
        until = get_premium_until(user_id)
        until_str = until.strftime("%Y-%m-%d") if until else "♾ cheksiz"
        bot.send_message(
            message.chat.id,
            f"💎 <b>Siz Premium foydalanuvchisiz!</b>\n📅 Muddati: <code>{until_str}</code>\n\n"
            "✅ Majburiy obunasiz foydalanish\n✅ VIP kinolarga kirish",
        )
    else:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="💎 Premium sotib olish", url=f"https://t.me/{ADMIN_USERNAME}"))
        bot.send_message(
            message.chat.id,
            "💎 <b>Premium imkoniyatlari:</b>\n\n"
            "✅ Majburiy obunasiz foydalanish\n"
            "✅ VIP kinolarga maxsus kirish\n"
            "✅ Tezroq va qulayroq xizmat\n\n"
            f"Sotib olish uchun @{ADMIN_USERNAME} ga yozing.",
            reply_markup=markup,
        )


@bot.message_handler(commands=["oxirgi"])
@safe_handler
def cmd_latest(message):
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    movies = get_latest_movies(10)
    if not movies:
        bot.send_message(message.chat.id, "😔 Hozircha kinolar mavjud emas.")
        return
    markup = types.InlineKeyboardMarkup()
    for m in movies:
        label = ("💎 " if m.get("is_premium") else "🎬 ") + m["title"]
        markup.add(types.InlineKeyboardButton(text=label[:64], callback_data=f"get_movie:{m['movie_id']}"))
    bot.send_message(message.chat.id, "🆕 <b>So'nggi qo'shilgan kinolar:</b>", reply_markup=markup)


@bot.message_handler(commands=["top"])
@safe_handler
def cmd_top(message):
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    movies = get_top_movies(10)
    if not movies:
        bot.send_message(message.chat.id, "😔 Hozircha kinolar mavjud emas.")
        return
    markup = types.InlineKeyboardMarkup()
    for m in movies:
        label = f"👁{m.get('views', 0)} — " + ("💎 " if m.get("is_premium") else "🎬 ") + m["title"]
        markup.add(types.InlineKeyboardButton(text=label[:64], callback_data=f"get_movie:{m['movie_id']}"))
    bot.send_message(message.chat.id, "🔥 <b>Eng ko'p ko'rilgan kinolar:</b>", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
@safe_handler
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
@safe_handler
def callback_get_movie(call):
    movie_id = call.data.split(":", 1)[1]
    movie = get_movie_by_id(movie_id)
    if movie:
        send_movie(call.message.chat.id, call.from_user.id, movie)
    else:
        bot.answer_callback_query(call.id, "❌ Kino topilmadi, ehtimol o'chirilgan.", show_alert=True)
        return
    bot.answer_callback_query(call.id)


# ============================== TUGMALI ADMIN PANEL ==============================
def admin_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ Kino qo'shish", callback_data="adm:add"),
        types.InlineKeyboardButton("🗑 Kino o'chirish", callback_data="adm:delete"),
    )
    markup.add(
        types.InlineKeyboardButton("📃 Kinolar ro'yxati", callback_data="adm:list"),
        types.InlineKeyboardButton("📊 Statistika", callback_data="adm:stats"),
    )
    markup.add(
        types.InlineKeyboardButton("💎 Premium berish", callback_data="adm:addprem"),
        types.InlineKeyboardButton("❌ Premium o'chirish", callback_data="adm:delprem"),
    )
    markup.add(
        types.InlineKeyboardButton("📢 Xabar yuborish", callback_data="adm:broadcast"),
    )
    return markup


def back_to_menu_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="adm:menu"))
    return markup


@bot.message_handler(commands=["admin"])
@safe_handler
def cmd_admin(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Sizda admin huquqi yo'q.")
        return
    user_states.pop(message.from_user.id, None)
    bot.send_message(message.chat.id, "🛠 <b>Admin panel</b>\n\nKerakli bo'limni tanlang:", reply_markup=admin_menu_keyboard())


@bot.message_handler(commands=["cancel"])
@safe_handler
def cmd_cancel(message):
    if user_states.pop(message.from_user.id, None) is not None:
        bot.send_message(message.chat.id, "🚫 Amal bekor qilindi.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("adm:"))
@safe_handler
def callback_admin_menu(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "⛔ Sizda admin huquqi yo'q.", show_alert=True)
        return

    action = call.data.split(":", 1)[1]
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    if action == "menu":
        user_states.pop(user_id, None)
        bot.edit_message_text("🛠 <b>Admin panel</b>\n\nKerakli bo'limni tanlang:", chat_id, msg_id,
                               reply_markup=admin_menu_keyboard())

    elif action == "add":
        user_states[user_id] = {"step": "waiting_video", "data": {}}
        bot.edit_message_text("🎬 Kino faylini (video) yuboring:\n\n/cancel — bekor qilish", chat_id, msg_id,
                               reply_markup=back_to_menu_keyboard())

    elif action == "delete":
        user_states[user_id] = {"step": "waiting_delete_id", "data": {}}
        bot.edit_message_text("🗑 O'chirmoqchi bo'lgan kino kodini kiriting:\n\n/cancel — bekor qilish", chat_id, msg_id,
                               reply_markup=back_to_menu_keyboard())

    elif action == "list":
        movies = get_latest_movies(30)
        if not movies:
            text = "📃 Hozircha kinolar mavjud emas."
        else:
            lines = ["📃 <b>Oxirgi 30 ta kino:</b>\n"]
            for m in movies:
                badge = "💎" if m.get("is_premium") else "🎬"
                lines.append(f"{badge} <code>{m['movie_id']}</code> — {m['title']} (👁{m.get('views', 0)})")
            text = "\n".join(lines)
        bot.edit_message_text(text[:4000], chat_id, msg_id, reply_markup=back_to_menu_keyboard())

    elif action == "stats":
        text = (
            f"📊 <b>Statistika</b>\n\n"
            f"🎬 Kinolar soni: {get_movies_count()}\n"
            f"👥 Foydalanuvchilar soni: {get_users_count()}\n"
            f"💎 Premium foydalanuvchilar: {get_premium_count()}"
        )
        bot.edit_message_text(text, chat_id, msg_id, reply_markup=back_to_menu_keyboard())

    elif action == "addprem":
        user_states[user_id] = {"step": "waiting_addprem_id", "data": {}}
        bot.edit_message_text("💎 Premium beriladigan foydalanuvchi ID sini kiriting:\n\n/cancel — bekor qilish",
                               chat_id, msg_id, reply_markup=back_to_menu_keyboard())

    elif action == "delprem":
        user_states[user_id] = {"step": "waiting_delprem_id", "data": {}}
        bot.edit_message_text("❌ Premium bekor qilinadigan foydalanuvchi ID sini kiriting:\n\n/cancel — bekor qilish",
                               chat_id, msg_id, reply_markup=back_to_menu_keyboard())

    elif action == "broadcast":
        user_states[user_id] = {"step": "waiting_broadcast_text", "data": {}}
        bot.edit_message_text("📢 Barcha foydalanuvchilarga yuboriladigan xabar matnini kiriting:\n\n/cancel — bekor qilish",
                               chat_id, msg_id, reply_markup=back_to_menu_keyboard())

    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("movietype:"))
@safe_handler
def callback_movie_type(call):
    user_id = call.from_user.id
    state = user_states.get(user_id)
    if not state or state.get("step") != "waiting_type" or not is_admin(user_id):
        bot.answer_callback_query(call.id, "⚠️ Bu amal muddati o'tgan.", show_alert=True)
        return

    is_premium = call.data.split(":", 1)[1] == "premium"
    data = state["data"]
    added = add_movie(data["movie_id"], data["title"], data["file_id"], is_premium=is_premium)

    if added:
        type_text = "💎 Premium (VIP)" if is_premium else "🎬 Oddiy"
        bot.edit_message_text(
            f"✅ Kino qo'shildi!\n\n🆔 Kod: {data['movie_id']}\n📝 Nomi: {data['title']}\n🏷 Turi: {type_text}",
            call.message.chat.id, call.message.message_id, reply_markup=back_to_menu_keyboard(),
        )
    else:
        bot.edit_message_text("❌ Xatolik: bu kod band yoki bazaga yozishda muammo bo'ldi.",
                               call.message.chat.id, call.message.message_id, reply_markup=back_to_menu_keyboard())

    user_states.pop(user_id, None)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("bcast:"))
@safe_handler
def callback_broadcast_confirm(call):
    user_id = call.from_user.id
    state = user_states.get(user_id)
    if not is_admin(user_id) or not state or state.get("step") != "waiting_broadcast_confirm":
        bot.answer_callback_query(call.id, "⚠️ Bu amal muddati o'tgan.", show_alert=True)
        return

    decision = call.data.split(":", 1)[1]
    if decision == "no":
        user_states.pop(user_id, None)
        bot.edit_message_text("🚫 Xabar yuborish bekor qilindi.", call.message.chat.id, call.message.message_id,
                               reply_markup=back_to_menu_keyboard())
        bot.answer_callback_query(call.id)
        return

    text = state["data"]["text"]
    bot.edit_message_text("⏳ Xabar yuborilmoqda...", call.message.chat.id, call.message.message_id)
    success, fail = 0, 0
    for uid in get_all_users():
        try:
            bot.send_message(uid, text)
            success += 1
        except Exception:
            fail += 1
    bot.edit_message_text(f"✅ Yuborildi: {success}\n❌ Yuborilmadi: {fail}",
                           call.message.chat.id, call.message.message_id, reply_markup=back_to_menu_keyboard())
    user_states.pop(user_id, None)
    bot.answer_callback_query(call.id)


# ============================== ASOSIY MESSAGE HANDLER (matn/video bosqichlari) ==============================
@bot.message_handler(content_types=["text", "video", "document"])
@safe_handler
def handle_message(message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    step = state.get("step") if state else None

    # ---- ADMIN: VIDEO KUTILYAPTI ----
    if step == "waiting_video" and is_admin(user_id):
        if message.content_type == "video":
            file_id = message.video.file_id
        elif message.content_type == "document":
            file_id = message.document.file_id
        else:
            bot.send_message(message.chat.id, "❗ Iltimos video fayl yuboring (yoki /cancel bosing).")
            return
        state["data"]["file_id"] = file_id
        state["step"] = "waiting_id"
        bot.send_message(message.chat.id, "🆔 Endi kino uchun ID (kod) kiriting (masalan: 1):")
        return

    if message.content_type != "text":
        return

    text = message.text.strip()
    if not text:
        return

    if text.startswith("/"):
        return  # buyruqlar alohida ushlanadi

    # ---- ADMIN: KINO ID KIRITISH ----
    if step == "waiting_id" and is_admin(user_id):
        if get_movie_by_id(text):
            bot.send_message(message.chat.id, "❗ Bu kod band. Boshqa kod kiriting:")
            return
        state["data"]["movie_id"] = text
        state["step"] = "waiting_title"
        bot.send_message(message.chat.id, "📝 Endi kino nomini kiriting:")
        return

    # ---- ADMIN: KINO NOMI KIRITISH ----
    if step == "waiting_title" and is_admin(user_id):
        state["data"]["title"] = text
        state["step"] = "waiting_type"
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(text="🎬 Oddiy", callback_data="movietype:normal"),
            types.InlineKeyboardButton(text="💎 Premium (VIP)", callback_data="movietype:premium"),
        )
        bot.send_message(message.chat.id, "🏷 Kino turini tanlang:", reply_markup=markup)
        return

    # ---- ADMIN: KINO O'CHIRISH ----
    if step == "waiting_delete_id" and is_admin(user_id):
        if delete_movie(text):
            bot.send_message(message.chat.id, f"🗑 <code>{text}</code> kodli kino o'chirildi.",
                              reply_markup=back_to_menu_keyboard())
        else:
            bot.send_message(message.chat.id, "❌ Bunday kodli kino topilmadi.", reply_markup=back_to_menu_keyboard())
        user_states.pop(user_id, None)
        return

    # ---- ADMIN: PREMIUM BERISH — ID ----
    if step == "waiting_addprem_id" and is_admin(user_id):
        if not text.isdigit():
            bot.send_message(message.chat.id, "❗ Foydalanuvchi ID raqam bo'lishi kerak. Qayta kiriting:")
            return
        state["data"]["target_id"] = int(text)
        state["step"] = "waiting_addprem_days"
        bot.send_message(message.chat.id, "📅 Necha kunlik Premium berilsin? (masalan: 30)")
        return

    if step == "waiting_addprem_days" and is_admin(user_id):
        if not text.isdigit():
            bot.send_message(message.chat.id, "❗ Kunlar soni raqam bo'lishi kerak. Qayta kiriting:")
            return
        target_id = state["data"]["target_id"]
        days = int(text)
        until = set_premium(target_id, days)
        bot.send_message(
            message.chat.id,
            f"💎 <code>{target_id}</code> ga {days} kunlik Premium berildi.\n📅 Tugash sanasi: {until.strftime('%Y-%m-%d')}",
            reply_markup=back_to_menu_keyboard(),
        )
        try:
            bot.send_message(target_id, f"🎉 Sizga {days} kunlik 💎 Premium status berildi!\n\nKo'rish: /premium")
        except Exception:
            pass
        user_states.pop(user_id, None)
        return

    # ---- ADMIN: PREMIUM O'CHIRISH ----
    if step == "waiting_delprem_id" and is_admin(user_id):
        if not text.isdigit():
            bot.send_message(message.chat.id, "❗ Foydalanuvchi ID raqam bo'lishi kerak. Qayta kiriting:")
            return
        remove_premium(int(text))
        bot.send_message(message.chat.id, f"❌ <code>{text}</code> uchun Premium bekor qilindi.",
                          reply_markup=back_to_menu_keyboard())
        user_states.pop(user_id, None)
        return

    # ---- ADMIN: BROADCAST MATNI ----
    if step == "waiting_broadcast_text" and is_admin(user_id):
        state["data"]["text"] = text
        state["step"] = "waiting_broadcast_confirm"
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("✅ Ha, yuborish", callback_data="bcast:yes"),
            types.InlineKeyboardButton("🚫 Bekor qilish", callback_data="bcast:no"),
        )
        bot.send_message(message.chat.id, f"📢 <b>Ushbu xabar barchaga yuborilsinmi?</b>\n\n{text}", reply_markup=markup)
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
        markup.add(types.InlineKeyboardButton(text=label[:64], callback_data=f"get_movie:{m['movie_id']}"))
    bot.send_message(message.chat.id, "🔍 Quyidagi natijalar topildi:", reply_markup=markup)


# ============================== FLASK WEBHOOK (RENDER UCHUN) ==============================
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"


@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    try:
        if request.headers.get("content-type") != "application/json":
            abort(403)
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        log.exception("Webhook so'rovni qayta ishlashda xato: %s", e)
        return "OK", 200


@app.route("/")
def index():
    return "🎬 Kino bot ishlayapti ✅", 200


@app.route("/health")
def health():
    try:
        mongo_client.admin.command("ping")
        mongo_status = "ok"
    except Exception as e:
        mongo_status = f"xato: {e}"
    return {"status": "running", "mongo": mongo_status, "admins": ADMINS}, 200


@app.errorhandler(404)
def not_found(e):
    return "Not found", 404


@app.errorhandler(500)
def server_error(e):
    log.exception("Flask ichki server xatosi: %s", e)
    return "Internal error", 500


def setup_webhook():
    if not WEBHOOK_HOST:
        log.warning("WEBHOOK_HOST aniqlanmadi (RENDER_EXTERNAL_URL yo'q). Render Web Service sifatida deploy qilinganiga ishonch hosil qiling.")
        return
    full_url = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
    for attempt in range(1, 4):
        try:
            bot.remove_webhook()
            time.sleep(1)
            bot.set_webhook(url=full_url)
            log.info("Webhook muvaffaqiyatli o'rnatildi: %s", full_url)
            return
        except Exception as e:
            log.error("Webhook o'rnatishda xato (urinish %d/3): %s", attempt, e)
            time.sleep(2)
    log.critical("Webhookni 3 marta urinishdan keyin ham o'rnatib bo'lmadi!")


setup_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
