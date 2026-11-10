import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("PUBLIC_APP_URL", "")

async def main():
    if not TOKEN:
        raise RuntimeError("Set BOT_TOKEN in Railway Variables; never commit it.")
    bot = Bot(TOKEN)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message):
        if APP_URL.startswith("https://"):
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Open Gift Scanner", web_app=WebAppInfo(url=APP_URL))]],
                resize_keyboard=True
            )
            await message.answer(
                "NFT Gift Scanner is ready. The scanner shows public, authorized read-only data.",
                reply_markup=keyboard
            )
        else:
            await message.answer("Bot is running. Configure PUBLIC_APP_URL to enable the Mini App.")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
