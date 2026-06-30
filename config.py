import os

# ============================================================
#  SOZLAMALAR FAYLI
#  Render'da bularni "Environment Variables" qismida kiritasiz,
#  lokal test uchun esa pastdagi default qiymatlarni o'zgartiring
# ============================================================

# @BotFather dan olingan bot tokeningiz
BOT_TOKEN = os.getenv("BOT_TOKEN", "BOT_TOKEN_BU_YERGA")

# MongoDB Atlas ulanish manzili (mongodb+srv://login:parol@cluster...)
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://USER:PAROL@cluster0.mongodb.net/")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "kino_bot")

# Render avtomatik beradigan tashqi havola (RENDER_EXTERNAL_URL)
# Render Web Service yaratganda bu o'zi to'g'ri keladi, lokal test uchun qo'lda yozing
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "https://sizning-app-nomi.onrender.com")
PORT = int(os.getenv("PORT", 10000))

# Admin(lar) Telegram ID raqami (@userinfobot orqali bilib olasiz)
ADMINS = [
    123456789,
]

# Premium haqida savol/murojaat uchun admin username (@siz)
ADMIN_USERNAME = "@admin_username"

# Majburiy obuna kanallari (Premium foydalanuvchilar bundan ozod bo'ladi)
CHANNELS = [
    {"id": "@kanal_username1", "url": "https://t.me/kanal_username1", "name": "1-kanal"},
    {"id": "@kanal_username2", "url": "https://t.me/kanal_username2", "name": "2-kanal"},
]
