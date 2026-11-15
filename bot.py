import asyncio
import html
import os

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    WebAppInfo,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
APP_URL = os.getenv(
    "PUBLIC_APP_URL",
    "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
).strip()
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "").strip()
API_BASE = os.getenv(
    "API_BASE_URL",
    "https://hassle-nft-market.up.railway.app",
).rstrip("/")
ADMIN_TELEGRAM_IDS = {
    part.strip()
    for part in os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", "")).split(",")
    if part.strip().isdigit()
}
CHANNEL_URL = "https://t.me/nopzw"


def is_admin(message: Message) -> bool:
    user = message.from_user
    return bool(user and str(user.id) in ADMIN_TELEGRAM_IDS)


def create_bot() -> Bot:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")
    return Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    @dp.message(Command("myid"))
    async def my_id(message: Message):
        if message.from_user:
            await message.answer(
                f"Твой Telegram ID: <code>{message.from_user.id}</code>\n"
                "Это твой собственный ID. Не отправляй токены и пароли."
            )

    @dp.message(CommandStart())
    async def start(message: Message):
        raw_name = message.from_user.first_name if message.from_user else "друг"
        first_name = html.escape(raw_name)
        text = (
            f"✨ <b>HASSLE NFT</b> — привет, {first_name}!\n\n"
            "🎁 <b>Маркетплейс NFT-подарков Telegram от n0kl1pzz1k.</b>\n"
            "Покупай, продавай и размещай Telegram NFT-подарки.\n\n"
            "<b>Жми кнопку ниже, чтобы открыть HASSLE NFT Market.</b>"
        )
        buttons = []
        if APP_URL.startswith("https://"):
            buttons.append([
                InlineKeyboardButton(
                    text="🚀 Открыть маркет",
                    web_app=WebAppInfo(url=APP_URL),
                )
            ])
        buttons.append([InlineKeyboardButton(text="📢 Новости и обновления", url=CHANNEL_URL)])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        if WELCOME_IMAGE_URL.startswith("https://"):
            try:
                await message.answer_photo(
                    photo=WELCOME_IMAGE_URL,
                    caption=text,
                    reply_markup=keyboard,
                )
                return
            except Exception:
                pass
        await message.answer(text, reply_markup=keyboard)

    @dp.message(Command("help"))
    async def help_command(message: Message):
        await message.answer(
            "🤖 <b>HASSLE NFT — помощь</b>\n\n"
            "/start — приветствие и запуск Mini App\n"
            "/help — эта справка\n"
            "/myid — показать твой Telegram ID\n\n"
            "Если Mini App не открывается, обнови Telegram и попробуй /start ещё раз."
        )

    @dp.message(Command("admin"))
    async def admin_panel(message: Message):
        if not is_admin(message):
            await message.answer("Доступ запрещён.")
            return
        app_ready = "настроен" if APP_URL.startswith("https://") else "не настроен"
        await message.answer(
            "🔐 <b>HASSLE NFT · админ-панель</b>\n\n"
            f"Mini App: {app_ready}\n"
            "<b>Команды:</b>\n"
            "/admin_status · состояние backend и маркетплейса\n"
            "/admin · эта панель"
        )

    @dp.message(Command("admin_status"))
    async def admin_status(message: Message):
        if not is_admin(message):
            await message.answer("Доступ запрещён.")
            return
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                health_response = await client.get(f"{API_BASE}/health")
                marketplace_response = await client.get(f"{API_BASE}/api/marketplace/stats")
            if not health_response.is_success or not marketplace_response.is_success:
                raise RuntimeError("Backend returned an error")
            stats = marketplace_response.json()
            await message.answer(
                "📊 <b>HASSLE NFT · статус</b>\n\n"
                "Backend: 🟢 онлайн\n"
                f"Объявлений: <code>{stats.get('total_orders', 0)}</code>\n"
                f"Активных продаж: <code>{stats.get('active_listings', 0)}</code>\n"
                f"Заявок на покупку: <code>{stats.get('active_buy_requests', 0)}</code>"
            )
        except Exception:
            await message.answer(
                "🔴 Backend недоступен или не ответил вовремя. "
                "Проверь Railway Deployments и Logs."
            )

    return dp


async def main():
    """Optional local polling runner. Railway production uses the FastAPI webhook."""
    if os.getenv("BOT_RUN_POLLING", "0") != "1":
        return
    bot = create_bot()
    dp = create_dispatcher()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
