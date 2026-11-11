import asyncio
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
APP_URL = os.getenv("PUBLIC_APP_URL", "").strip()
WELCOME_IMAGE_URL = os.getenv(
    "WELCOME_IMAGE_URL",
    "https://nft-gift-scanner.pages.dev/welcome_640x360.jpg",
).strip()
API_BASE = os.getenv(
    "API_BASE_URL",
    "https://nft-gift-scanner-production.up.railway.app",
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
        first_name = message.from_user.first_name if message.from_user else "друг"
        text = (
            f"✨ <b>Добро пожаловать в GIFT RADAR, {first_name}!</b>\n\n"
            "Твой центр Telegram-подарков:\n"
            "🎁 <b>Маркет</b> — каталог подарков Portals и поиск по коллекциям.\n"
            "📊 <b>Обзор</b> — активность, заявки и демонстрационный объём.\n"
            "🧾 <b>Ордера</b> — тестовые предложения покупки и продажи.\n"
            "⭐ <b>Избранное</b> — сохраняй интересные экземпляры.\n"
            "👤 <b>Профиль</b> — настройки и история.\n\n"
            "В интерфейсе доступны обозначения TON и Telegram Stars. "
            "Все заявки и объёмы демонстрационные: реальные платежи и операции с кошельками не выполняются.\n\n"
            "<b>Нажми кнопку ниже, чтобы открыть маркетплейс.</b> 🚀"
        )
        buttons = []
        if APP_URL.startswith("https://"):
            buttons.append([
                InlineKeyboardButton(
                    text="🚀 Открыть GIFT RADAR",
                    web_app=WebAppInfo(url=APP_URL),
                )
            ])
        buttons.append([InlineKeyboardButton(text="📢 Telegram-канал", url=CHANNEL_URL)])
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

    @dp.message(Command("admin"))
    async def admin_panel(message: Message):
        if not is_admin(message):
            await message.answer("Доступ запрещён.")
            return
        portals = "настроен" if os.getenv("PORTALS_API_KEY", "").strip() else "не настроен"
        app_ready = "настроен" if APP_URL.startswith("https://") else "не настроен"
        await message.answer(
            "🔐 <b>GIFT RADAR — админ-панель</b>\n\n"
            f"Mini App: {app_ready}\n"
            f"Portals: {portals}\n"
            "<b>Команды:</b>\n"
            "/admin_status — проверить backend и источники\n"
            "/admin — открыть эту панель\n\n"
            "Панель управляет мониторингом. Все заявки остаются демонстрационными."
        )

    @dp.message(Command("admin_status"))
    async def admin_status(message: Message):
        if not is_admin(message):
            await message.answer("Доступ запрещён.")
            return
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                health_response = await client.get(f"{API_BASE}/health")
                status_response = await client.get(f"{API_BASE}/api/status")
                marketplace_response = await client.get(f"{API_BASE}/api/marketplace/stats")
            if not health_response.is_success or not status_response.is_success or not marketplace_response.is_success:
                raise RuntimeError("Backend returned an error")
            status_data = status_response.json()
            sources = status_data.get("sources", {})
            portals = sources.get("portals", "unknown")
            stats = marketplace_response.json()
            await message.answer(
                "📊 <b>Статус GIFT RADAR</b>\n\n"
                "Backend: 🟢 онлайн\n"
                f"Portals: <code>{portals}</code>\n"
                f"Демо-ордера: <code>{stats.get('total_orders', 0)}</code>\n"
                "Режим: <code>shared_demo</code>"
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
