import asyncio
import os

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("PUBLIC_APP_URL", "")
API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "").strip()


def is_admin(message: Message) -> bool:
    user = message.from_user
    return bool(user and ADMIN_TELEGRAM_ID.isdigit() and str(user.id) == ADMIN_TELEGRAM_ID)


async def main():
    if not TOKEN:
        raise RuntimeError("Set BOT_TOKEN in Railway Variables; never commit it.")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    @dp.message(Command("myid"))
    async def my_id(message: Message):
        if message.from_user:
            await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>\nЭто твой собственный ID. Не отправляй токены и пароли.")
    
    @dp.message(CommandStart())
    async def start(message: Message):
        if APP_URL.startswith("https://"):
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Open Gift Scanner", web_app=WebAppInfo(url=APP_URL))]],
                resize_keyboard=True
            )
            await message.answer(
                "NFT Gift Scanner is ready. The scanner shows public marketplace information.",
                reply_markup=keyboard
            )
        else:
            await message.answer("Bot is running. Configure PUBLIC_APP_URL to enable the Mini App.")

    @dp.message(Command("admin"))
    async def admin_panel(message: Message):
        if not is_admin(message):
            await message.answer("Доступ запрещён.")
            return
        portals = "настроен" if os.getenv("PORTALS_API_KEY", "").strip() else "не настроен"
        app = "настроен" if APP_URL.startswith("https://") else "не настроен"
        await message.answer(
            "🔐 <b>NFT Gift Scanner — админ-панель</b>\n\n"
            f"Mini App: {app}\n"
            f"Portals: {portals}\n"
            "Tonnel: официальный read-only API не подтверждён\n"
            "MRKT: официальный read-only API не подтверждён\n\n"
            "<b>Команды:</b>\n"
            "/admin_status — проверить backend и источники\n"
            "/admin — открыть эту панель\n\n"
            "Панель управляет только мониторингом. Торговых и кошельковых операций нет."
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
            if not health_response.is_success or not status_response.is_success:
                raise RuntimeError("Backend returned an error")
            status_data = status_response.json()
            sources = status_data.get("sources", {})
            portals = sources.get("portals", "unknown")
            tonnel = sources.get("tonnel", "unknown")
            mrkt = sources.get("mrkt", "unknown")
            await message.answer(
                "📊 <b>Статус NFT Gift Scanner</b>\n\n"
                "Backend: 🟢 онлайн\n"
                f"Portals: <code>{portals}</code>\n"
                f"Tonnel: <code>{tonnel}</code>\n"
                f"MRKT: <code>{mrkt}</code>\n"
                "Режим: <code>read_only</code>"
            )
        except Exception:
            await message.answer("🔴 Backend недоступен или не ответил вовремя. Проверь Railway Deployments и Logs.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
