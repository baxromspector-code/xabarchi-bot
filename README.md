# Username'larga xabar yuboruvchi bot

## O'rnatish
1. `pip install -r requirements.txt`
2. `.env.example` ni `.env` deb nusxalang va to'ldiring:
   - `BOT_TOKEN` — @BotFather dan
   - `API_ID`, `API_HASH` — https://my.telegram.org → API development tools
3. `python bot.py`

## Ishlatish
- /start → telefon raqam → Telegram yuborgan kodni **bo'shliq bilan** (`1 2 3 4 5`) yozing → (bo'lsa) 2FA parol.
- Menyu: ➕ Matn qo'shish, 🗑 Matnni o'chirish, 📄 Mening matnim, 🔌 Akkauntni uzish.
- Username'larni yuboring: `@user1 @user2 t.me/user3` (bo'shliq, vergul yoki yangi qator bilan).
  Matn sizning akkauntingizdan har biriga 2 soniya oraliqda bittadan yuboriladi.
  Bir nechta ro'yxat ketma-ket yuborilsa — navbat bilan bajariladi.

## Muhim
- `bot.db` faylida akkaunt sessiyalari saqlanadi — uni hech kimga bermang.
- Notanish odamlarga ko'p xabar yuborsangiz Telegram akkauntni spam-cheklovga tushiradi.
