import asyncio
import html
import logging
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
from marketplace import upsert_telegram_business_connection
from marketplace_stars import confirm_stars_payment, validate_stars_pre_checkout

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

    @dp.business_connection()
    async def business_connection_updated(connection):
        # Telegram sends this update on connect, permission changes, and disconnect.
        # Store only the owner ID and the exact rights granted by the account owner.
        upsert_telegram_business_connection(
            connection.model_dump(mode="json", exclude_none=True)
        )


    @dp.pre_checkout_query()
    async def stars_pre_checkout(query):
        user = query.from_user
        if user is None:
            await query.answer(ok=False, error_message="Telegram user identity is missing.")
            return
        try:
            ok, error_message = await asyncio.to_thread(
                validate_stars_pre_checkout,
                invoice_payload=query.invoice_payload,
                telegram_user_id=user.id,
                currency=query.currency,
                total_amount=query.total_amount,
            )
        except Exception:
            logging.getLogger("hassle_nft.payments").exception("Stars pre-checkout validation failed")
            ok, error_message = False, "Payment verification is temporarily unavailable."
        await query.answer(
            ok=bool(ok),
            error_message=(error_message or "This invoice is no longer valid.") if not ok else None,
        )

    @dp.message(lambda message: message.successful_payment is not None)
    async def stars_successful_payment(message: Message):
        user = message.from_user
        payment = message.successful_payment
        if user is None or payment is None:
            return
        try:
            result = await asyncio.to_thread(
                confirm_stars_payment,
                invoice_payload=payment.invoice_payload,
                telegram_user_id=user.id,
                currency=payment.currency,
                total_amount=payment.total_amount,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                provider_payment_charge_id=payment.provider_payment_charge_id,
            )
        except Exception:
            logging.getLogger("hassle_nft.payments").exception(
                "Stars successful_payment requires reconciliation"
            )
            await message.answer(
                "Платёж получен Telegram, но подтверждение сделки требует проверки. "
                "Не оплачивай повторно. Напиши /paysupport и укажи ID сделки."
            )
            return
        await message.answer(
            "⭐ <b>Оплата подтверждена.</b> Средства учтены в защищённом балансе сделки.\n"
            f"Сделка: <code>{html.escape(result['deal_id'])}</code>\n"
            "Следующий этап: передача NFT продавцом и проверка нового владельца."
        )

    @dp.message(Command("paysupport"))
    async def payment_support(message: Message):
        user = message.from_user
        if user is None:
            return
        details = (message.text or "").partition(" ")[2].strip()
        if not details:
            await message.answer(
                "🆘 Для обращения отправь одной командой ID сделки и описание проблемы:\n"
                "<code>/paysupport DEAL-ID описание проблемы</code>"
            )
            return
        if not ADMIN_TELEGRAM_IDS:
            await message.answer("Платёжная поддержка временно недоступна. Повтори попытку позже.")
            return
        username = f"@{html.escape(user.username)}" if user.username else "без username"
        report = (
            "🆘 <b>Платёжное обращение HASSLE NFT</b>\n"
            f"Пользователь: <code>{user.id}</code> ({username})\n"
            f"Обращение: {html.escape(details[:1500])}"
        )
        delivered = 0
        for admin_id in ADMIN_TELEGRAM_IDS:
            try:
                await message.bot.send_message(int(admin_id), report)
                delivered += 1
            except Exception:
                logging.getLogger("hassle_nft.payments").exception(
                    "Could not forward payment support request to admin"
                )
        if delivered:
            await message.answer("Обращение передано платёжной поддержке. Сохрани ID сделки.")
        else:
            await message.answer("Не удалось передать обращение. Попробуй позже.")

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
            "/myid — показать твой Telegram ID\n"
            "/paysupport — помощь по платежам\n\n"
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
