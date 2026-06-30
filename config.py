# ============================================================
#  SOZLAMALAR FAYLI
#  Pastdagi qiymatlarni o'zingizga moslab to'ldiring
# ============================================================

# @BotFather dan olingan bot tokeningiz
BOT_TOKEN = "123456789:AAExampleTokenBuYergaQoying"

# Admin(lar) Telegram ID raqami (@userinfobot orqali bilib olasiz)
# Bir nechta admin bo'lsa, vergul bilan qo'shing: [111111, 222222]
ADMINS = [
    123456789,
]

# Majburiy obuna kanallari (xohlagancha qo'shishingiz mumkin)
# "id"  -> kanalning username'i (@kanal_nomi) — bot get_chat_member uchun ishlatadi
# "url" -> kanal havolasi — foydalanuvchi bosib o'tadi
# "name"-> tugmada ko'rinadigan nom
CHANNELS = [
    {"id": "@kanal_username1", "url": "https://t.me/kanal_username1", "name": "1-kanal"},
    {"id": "@kanal_username2", "url": "https://t.me/kanal_username2", "name": "2-kanal"},
]

# Ma'lumotlar bazasi fayli nomi (o'zgartirish shart emas)
DB_NAME = "movies.db"
