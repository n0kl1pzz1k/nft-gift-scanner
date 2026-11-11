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
TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("PUBLIC_APP_URL", "").strip()
WELCOME_IMAGE_URL = os.getenv(
    "WELCOME_IMAGE_URL",
    "https://nft-gift-scanner.pages.dev/welcome_640x360.jpg",
).strip()
API_BASE = os.getenv("API_BASE_URL", f"http://127.0.0.1:{os.getenv('PORT', '8080')}").rstrip("/")
ADMIN_TELEGRAM_IDS = {part.strip() for part in os.getenv("ADMIN_TELEGRAM_IDS", os.getenv("ADMIN_TELEGRAM_ID", "")).split(",") if part.strip().isdigit()}
CHANNEL_URL = "https://t.me/nopzw"


def is_admin(message: Message) -> bool:
    user = message.from_user
    return bool(user and str(user.id) in ADMIN_TELEGRAM_IDS)


async def main():
    if not TOKEN:
        raise RuntimeError("Set BOT_TOKEN in Railway Variables; never commit it.")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
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
        text = (
            "👋 <b>Ты попал на GIFT RADAR!</b>\n\n"
            "Здесь собраны Telegram NFT-подарки из каталога Portals. "
            "Изучай коллекции, сохраняй интересные экземпляры и знакомься с интерфейсом GIFT RADAR Marketplace.\n\n"
            "Твой радар подарков уже готов — запускай! 🚀"
        )

        buttons = [[
            InlineKeyboardButton(text="📢 Мой Telegram", url=CHANNEL_URL),
        ]]
        if APP_URL.startswith("https://"):
            buttons.append([
                InlineKeyboardButton(
                    text="🚀 Открыть GIFT RADAR",
                    web_app=WebAppInfo(url=APP_URL),
                )
            ])

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
                # Keep the welcome flow available if the image URL is not deployed yet.
                pass

        await message.answer(text, reply_markup=keyboard)

    @dp.message(Command("admin"))
    async def admin_panel(message: Message):
        if not is_admin(message):
            await message.answer("Доступ запрещён.")
            return
        portals = "настроен" if os.getenv("PORTALS_API_KEY", "").strip() else "не настроен"
        app = "настроен" if APP_URL.startswith("https://") else "не настроен"
        await message.answer(
            "🔐 <b>GIFT RADAR — админ-панель</b>\n\n"
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
                "📊 <b>Статус GIFT RADAR</b>\n\n"
                "Backend: 🟢 онлайн\n"
                f"Portals: <code>{portals}</code>\n"
                f"Tonnel: <code>{tonnel}</code>\n"
                f"MRKT: <code>{mrkt}</code>\n"
                "Режим: <code>read_only</code>"
            )
        except Exception:
            await message.answer(
                "🔴 Backend недоступен или не ответил вовремя. "
                "Проверь Railway Deployments и Logs."
            )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
