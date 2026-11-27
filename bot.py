"""
Telegram bot: foydalanuvchi o'z akkauntini ulaydi, matn saqlaydi va
yuborilgan username'larga o'z akkaunti nomidan shu matnni jo'natadi
(har biri orasida 2 soniya dam).
"""
import asyncio
import logging
import os
import re
import sqlite3

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
DELAY_SECONDS = float(os.getenv("DELAY_SECONDS", "2"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

# ---------------------------------------------------------------- DB
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS users ("
    " user_id INTEGER PRIMARY KEY, session TEXT, text TEXT)"
)
for _col, _typ in [("name", "TEXT"), ("username", "TEXT"),
                   ("blocked", "INTEGER DEFAULT 0"), ("created", "TEXT")]:
    try:
        db.execute(f"ALTER TABLE users ADD COLUMN {_col} {_typ}")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon bor
db.execute(
    "CREATE TABLE IF NOT EXISTS sends ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, target TEXT,"
    " ok INTEGER, error TEXT, ts TEXT DEFAULT (datetime('now','localtime')))"
)
db.commit()


def log_send(user_id: int, target: str, error: str | None) -> None:
    db.execute(
        "INSERT INTO sends(user_id, target, ok, error) VALUES (?,?,?,?)",
        (user_id, target, 0 if error else 1, error),
    )
    db.commit()


def db_get(user_id: int) -> tuple[str | None, str | None]:
    row = db.execute(
        "SELECT session, text FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def db_set(user_id: int, **fields) -> None:
    db.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))
    for key, value in fields.items():
        db.execute(f"UPDATE users SET {key}=? WHERE user_id=?", (value, user_id))
    db.commit()


# ---------------------------------------------------------------- Telethon klientlar
clients: dict[int, TelegramClient] = {}       # ulangan akkauntlar
pending_login: dict[int, dict] = {}            # login jarayonidagi klientlar
send_locks: dict[int, asyncio.Lock] = {}       # har bir user uchun navbat


client_locks: dict[int, asyncio.Lock] = {}    # bir user uchun bitta ulanish


async def get_client(user_id: int) -> TelegramClient | None:
    async with client_locks.setdefault(user_id, asyncio.Lock()):
        return await _get_client(user_id)


async def _get_client(user_id: int) -> TelegramClient | None:
    client = clients.get(user_id)
    if client and client.is_connected():
        return client
    session, _ = db_get(user_id)
    if not session:
        return None
    client = TelegramClient(StringSession(session), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        db_set(user_id, session=None)
        return None
    clients[user_id] = client
    return client


# ---------------------------------------------------------------- Klaviaturalar
BTN_ADD = "➕ Matn qo'shish"
BTN_DEL = "🗑 Matnni o'chirish"
BTN_SHOW = "📄 Mening matnim"
BTN_LOGOUT = "🔌 Akkauntni uzish"
BTN_CANCEL = "❌ Bekor qilish"

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_DEL)],
        [KeyboardButton(text=BTN_SHOW), KeyboardButton(text=BTN_LOGOUT)],
    ],
    resize_keyboard=True,
)
PHONE_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)
CANCEL_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True
)


class Login(StatesGroup):
    phone = State()
    code = State()
    password = State()


class AddText(StatesGroup):
    waiting = State()


router = Router()


class UserMiddleware(BaseMiddleware):
    """Foydalanuvchi ismini saqlaydi va bloklanganlarni to'xtatadi."""

    async def __call__(self, handler, event: Message, data):
        u = event.from_user
        if u:
            db.execute(
                "INSERT OR IGNORE INTO users(user_id, created)"
                " VALUES (?, datetime('now','localtime'))", (u.id,),
            )
            db.execute(
                "UPDATE users SET name=?, username=? WHERE user_id=?",
                (u.full_name, u.username, u.id),
            )
            db.commit()
            row = db.execute("SELECT blocked FROM users WHERE user_id=?", (u.id,)).fetchone()
            if row and row[0]:
                await event.answer("⛔ Siz admin tomonidan bloklangansiz.")
                return None
        return await handler(event, data)


async def force_logout(user_id: int) -> None:
    """Admin paneldan akkauntni uzish."""
    client = clients.pop(user_id, None)
    if client is None:
        session, _ = db_get(user_id)
        if session:
            client = TelegramClient(StringSession(session), API_ID, API_HASH)
            await client.connect()
    if client:
        try:
            await client.log_out()
        except Exception:
            pass
    db_set(user_id, session=None)


# ---------------------------------------------------------------- Ro'yxatdan o'tish / ulanish
@router.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()
    if await get_client(msg.from_user.id):
        await msg.answer(
            "✅ Akkauntingiz ulangan.\n\n"
            "Username'larni yuboring (bir yoki bir nechta) — saqlangan matn "
            "sizning akkauntingizdan jo'natiladi.",
            reply_markup=MAIN_KB,
        )
        return
    await msg.answer(
        "👋 Xush kelibsiz!\n\nBotdan foydalanish uchun Telegram akkauntingizni "
        "ulashingiz kerak. Telefon raqamingizni yuboring (tugma orqali yoki "
        "+998901234567 ko'rinishida).",
        reply_markup=PHONE_KB,
    )
    await state.set_state(Login.phone)


@router.message(Login.phone)
async def login_phone(msg: Message, state: FSMContext):
    if msg.contact:
        phone = msg.contact.phone_number
    else:
        phone = (msg.text or "").strip()
    phone = "+" + re.sub(r"\D", "", phone)
    if len(phone) < 9:
        await msg.answer("❗ Raqam noto'g'ri. Qaytadan yuboring.")
        return

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except errors.PhoneNumberInvalidError:
        await client.disconnect()
        await msg.answer("❗ Bunday raqam topilmadi. Qaytadan yuboring.")
        return
    except errors.FloodWaitError as e:
        await client.disconnect()
        await msg.answer(f"⏳ Juda ko'p urinish. {e.seconds} soniyadan keyin qayta urining.")
        return

    pending_login[msg.from_user.id] = {
        "client": client, "phone": phone, "hash": sent.phone_code_hash,
    }
    await state.set_state(Login.code)
    await msg.answer(
        "📩 Telegram'ga kod yuborildi.\n\n"
        "⚠️ Kodni <b>bo'shliq bilan</b> yozing, masalan: <code>1 2 3 4 5</code>\n"
        "(aks holda Telegram kodni bloklab qo'yadi).",
        reply_markup=ReplyKeyboardRemove(),
    )


async def finish_login(msg: Message, state: FSMContext, client: TelegramClient):
    user_id = msg.from_user.id
    db_set(user_id, session=client.session.save())
    clients[user_id] = client
    pending_login.pop(user_id, None)
    await state.clear()
    me = await client.get_me()
    await msg.answer(
        f"✅ Akkaunt ulandi: <b>{me.first_name}</b>"
        + (f" (@{me.username})" if me.username else "")
        + "\n\nEndi «➕ Matn qo'shish» orqali matn saqlang, so'ng "
        "username'larni yuboring.",
        reply_markup=MAIN_KB,
    )


@router.message(Login.code)
async def login_code(msg: Message, state: FSMContext):
    data = pending_login.get(msg.from_user.id)
    if not data:
        await state.clear()
        await msg.answer("Jarayon eskirdi. /start ni bosing.")
        return
    code = re.sub(r"\D", "", msg.text or "")
    client: TelegramClient = data["client"]
    try:
        await client.sign_in(data["phone"], code, phone_code_hash=data["hash"])
    except errors.SessionPasswordNeededError:
        await state.set_state(Login.password)
        await msg.answer("🔐 Akkauntda 2 bosqichli parol bor. Parolni yuboring:")
        return
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
        await msg.answer("❗ Kod noto'g'ri. Qaytadan yuboring (bo'shliq bilan).")
        return
    except errors.PhoneCodeExpiredError:
        await client.disconnect()
        pending_login.pop(msg.from_user.id, None)
        await state.clear()
        await msg.answer("⌛ Kod eskirdi. /start ni bosib qaytadan urining.")
        return
    await finish_login(msg, state, client)


@router.message(Login.password)
async def login_password(msg: Message, state: FSMContext):
    data = pending_login.get(msg.from_user.id)
    if not data:
        await state.clear()
        await msg.answer("Jarayon eskirdi. /start ni bosing.")
        return
    password = msg.text or ""
    try:
        await msg.delete()  # parol chatda qolmasin
    except Exception:
        pass
    try:
        await data["client"].sign_in(password=password)
    except errors.PasswordHashInvalidError:
        await msg.answer("❗ Parol noto'g'ri. Qaytadan yuboring:")
        return
    await finish_login(msg, state, data["client"])


# ---------------------------------------------------------------- Ulanish talab qilinadi
async def require_client(msg: Message, state: FSMContext) -> TelegramClient | None:
    client = await get_client(msg.from_user.id)
    if not client:
        await msg.answer("❗ Avval akkauntingizni ulang.")
        await start(msg, state)
    return client


# ---------------------------------------------------------------- Menyu
@router.message(F.text == BTN_CANCEL)
async def cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Bekor qilindi.", reply_markup=MAIN_KB)


@router.message(StateFilter(None), F.text == BTN_ADD)
async def add_text(msg: Message, state: FSMContext):
    if not await require_client(msg, state):
        return
    await state.set_state(AddText.waiting)
    await msg.answer("✍️ Yuboriladigan matnni yozing:", reply_markup=CANCEL_KB)


@router.message(AddText.waiting, F.text)
async def save_text(msg: Message, state: FSMContext):
    db_set(msg.from_user.id, text=msg.text)
    await state.clear()
    await msg.answer("✅ Matn saqlandi.", reply_markup=MAIN_KB)


@router.message(StateFilter(None), F.text == BTN_DEL)
async def del_text(msg: Message, state: FSMContext):
    _, text = db_get(msg.from_user.id)
    if not text:
        await msg.answer("Saqlangan matn yo'q.", reply_markup=MAIN_KB)
        return
    db_set(msg.from_user.id, text=None)
    await msg.answer("🗑 Matn o'chirildi.", reply_markup=MAIN_KB)


@router.message(StateFilter(None), F.text == BTN_SHOW)
async def show_text(msg: Message, state: FSMContext):
    _, text = db_get(msg.from_user.id)
    if not text:
        await msg.answer("Saqlangan matn yo'q.", reply_markup=MAIN_KB)
    else:
        await msg.answer(f"📄 Saqlangan matn:\n\n{text}", reply_markup=MAIN_KB, parse_mode=None)


@router.message(StateFilter(None), F.text == BTN_LOGOUT)
async def logout(msg: Message, state: FSMContext):
    user_id = msg.from_user.id
    client = await get_client(user_id)
    if client:
        try:
            await client.log_out()
        except Exception:
            pass
    clients.pop(user_id, None)
    db_set(user_id, session=None)
    await msg.answer("🔌 Akkaunt uzildi. Qayta ulash uchun /start.", reply_markup=ReplyKeyboardRemove())


# ---------------------------------------------------------------- Username'larga yuborish
USERNAME_RE = re.compile(r"(?:@|t\.me/|telegram\.me/)([A-Za-z][A-Za-z0-9_]{3,31})")


def parse_usernames(text: str) -> list[str]:
    result, seen = [], set()
    for token in re.split(r"[\s,;]+", text):
        m = USERNAME_RE.fullmatch(token.strip().removeprefix("https://").removeprefix("http://"))
        if m and m.group(1).lower() not in seen:
            seen.add(m.group(1).lower())
            result.append(m.group(1))
    return result


async def send_batch(bot: Bot, chat_id: int, user_id: int, usernames: list[str], text: str):
    lock = send_locks.setdefault(user_id, asyncio.Lock())
    async with lock:  # bir vaqtda kelgan ro'yxatlar navbat bilan
        client = await get_client(user_id)
        if not client:
            await bot.send_message(chat_id, "❗ Akkaunt uzilgan. /start ni bosing.")
            return
        ok, failed = 0, []
        for i, username in enumerate(usernames):
            err = None
            for attempt in range(2):
                try:
                    await client.send_message(username, text)
                    ok += 1
                    err = None
                    log.info("user %s -> @%s yuborildi", user_id, username)
                    break
                except errors.FloodWaitError as e:
                    err = "flood cheklov"
                    if attempt == 0:
                        await bot.send_message(
                            chat_id, f"⏳ Telegram cheklovi: {e.seconds} soniya kutilmoqda..."
                        )
                        await asyncio.sleep(e.seconds + 1)
                        continue
                except (ValueError, errors.UsernameNotOccupiedError, errors.UsernameInvalidError):
                    err = "topilmadi"
                    break
                except errors.PeerFloodError:
                    err = "akkaunt spam-cheklovda"
                    break
                except errors.UserIsBlockedError:
                    err = "sizni bloklagan"
                    break
                except Exception as e:  # noqa: BLE001
                    err = type(e).__name__
                    break
            if err:
                failed.append(f"@{username} — {err}")
            log_send(user_id, username, err)
            if i < len(usernames) - 1:
                await asyncio.sleep(DELAY_SECONDS)

        report = f"📊 Natija: {ok}/{len(usernames)} ta yuborildi."
        if failed:
            report += "\n\n❗ Yuborilmadi:\n" + "\n".join(failed)
        await bot.send_message(chat_id, report, parse_mode=None)
        # navbatdagi ro'yxat ham 2 soniyadan keyin boshlansin
        await asyncio.sleep(DELAY_SECONDS)


@router.message(StateFilter(None), F.text)
async def handle_usernames(msg: Message, state: FSMContext, bot: Bot):
    if not await require_client(msg, state):
        return
    _, text = db_get(msg.from_user.id)
    if not text:
        await msg.answer("❗ Avval «➕ Matn qo'shish» orqali matn saqlang.", reply_markup=MAIN_KB)
        return
    usernames = parse_usernames(msg.text)
    if not usernames:
        await msg.answer("❗ Username topilmadi. Masalan: @username1 @username2")
        return
    busy = send_locks.get(msg.from_user.id)
    queued = " (oldingi ro'yxatdan keyin navbatda)" if busy and busy.locked() else ""
    await msg.answer(
        f"🚀 {len(usernames)} ta username'ga yuborish boshlandi{queued}.\n"
        f"Har biri orasida {DELAY_SECONDS:g} soniya dam olinadi.",
        reply_markup=MAIN_KB,
    )
    asyncio.create_task(send_batch(bot, msg.chat.id, msg.from_user.id, usernames, text))


# ---------------------------------------------------------------- Ishga tushirish
async def main():
    if not BOT_TOKEN or not API_ID or not API_HASH:
        raise SystemExit(".env faylida BOT_TOKEN, API_ID, API_HASH ni to'ldiring.")
    from aiogram.client.default import DefaultBotProperties

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.message.outer_middleware(UserMiddleware())
    dp.include_router(router)

    # ---- admin panel (faqat shu kompyuterdan ochiladi)
    from aiohttp import web
    from admin import create_app

    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if admin_password:
        host = os.getenv("ADMIN_HOST", "127.0.0.1")
        port = int(os.getenv("ADMIN_PORT", "8080"))
        runner = web.AppRunner(create_app(db, admin_password, force_logout))
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        log.info("Admin panel: http://%s:%s", host, port)
    else:
        log.warning("ADMIN_PASSWORD yo'q — admin panel o'chiq")

    # Render tekin tarifi 15 daqiqa so'rovsiz qolsa uxlaydi — o'zimizni ping qilamiz
    public_url = os.getenv("RENDER_EXTERNAL_URL")
    if public_url and admin_password:
        async def keep_alive():
            import aiohttp
            async with aiohttp.ClientSession() as s:
                while True:
                    await asyncio.sleep(600)
                    try:
                        await s.get(f"{public_url}/health", timeout=aiohttp.ClientTimeout(total=20))
                    except Exception as e:  # noqa: BLE001
                        log.warning("keep-alive xato: %s", e)
        keep_alive_task = asyncio.create_task(keep_alive())  # noqa: F841 (havola saqlanadi)
        log.info("Keep-alive yoqildi: %s/health", public_url)

    log.info("Bot ishga tushdi")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
