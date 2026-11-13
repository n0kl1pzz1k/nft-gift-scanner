import asyncio
import html
import os

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    WebAppInfo,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    PreCheckoutQuery,
)
from dotenv import load_dotenv
from financial_ledger import approve_telegram_stars_pre_checkout, settle_telegram_stars_payment

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
TERMS_URL = os.getenv("TERMS_URL", "").strip()
PAYMENT_SUPPORT_URL = os.getenv("PAYMENT_SUPPORT_URL", "").strip()
STARS_PAYMENTS_ENABLED = (
    os.getenv("TELEGRAM_STARS_PAYMENTS_ENABLED", "false").strip().lower() == "true"
    and os.getenv("MARKETPLACE_REAL_DEALS_ENABLED", "false").strip().lower() == "true"
    and os.getenv("GIFT_RADAR_STORAGE_BACKEND", "sqlite").strip().lower() == "postgres"
    and os.getenv("DATABASE_URL", "").strip().startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))
    and TERMS_URL.startswith("https://")
    and PAYMENT_SUPPORT_URL.startswith("https://")
)


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
        payment_notice = (
            "Оплата цифровых подарков доступна только в Telegram Stars (XTR). "
            if STARS_PAYMENTS_ENABLED and TERMS_URL.startswith("https://") and PAYMENT_SUPPORT_URL.startswith("https://")
            else "Платежи пока отключены. Заявки и суммы не списываются. "
        )
        text = (
            f"✨ <b>GIFT RADAR</b> — добро пожаловать, {first_name}!\n\n"
            "Твой навигатор по Telegram-подаркам.\n\n"
            "🎁 <b>Маркет</b> — каталог Portals, карточки подарков и поиск.\n"
            "⭐ <b>Избранное</b> — сохраняй интересные экземпляры в своём профиле.\n"
            "🧾 <b>Ордера</b> — лента заявок и сделок.\n"
            "👤 <b>Профиль</b> — Telegram-аккаунт и настройки.\n\n"
            "Каталог получает данные из Portals в режиме просмотра. "
            + payment_notice
            + "Вывод средств и операции с кошельками не выполняются.\n\n"
            "<b>Открывай Mini App кнопкой ниже.</b>"
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
            "🤖 <b>GIFT RADAR — помощь</b>\n\n"
            "/start — приветствие и запуск Mini App\n"
            "/help — эта справка\n"
            "/myid — показать твой Telegram ID\n\n"
            "Если Mini App не открывается, обнови Telegram и попробуй /start ещё раз."
        )

    @dp.message(Command("terms"))
    async def terms_command(message: Message):
        if TERMS_URL.startswith("https://"):
            await message.answer("Условия использования и оплаты: " + html.escape(TERMS_URL))
        else:
            await message.answer("Условия оплаты пока не опубликованы. Покупки временно отключены.")

    @dp.message(Command("paysupport"))
    async def payment_support_command(message: Message):
        if PAYMENT_SUPPORT_URL.startswith("https://"):
            await message.answer("Поддержка по платежам: " + html.escape(PAYMENT_SUPPORT_URL))
        else:
            await message.answer("Платежи сейчас отключены. Обратись в поддержку через официальный канал GIFT RADAR.")

    @dp.pre_checkout_query()
    async def stars_pre_checkout(query: PreCheckoutQuery):
        if not (STARS_PAYMENTS_ENABLED and TERMS_URL.startswith("https://") and PAYMENT_SUPPORT_URL.startswith("https://")):
            await query.answer(ok=False, error_message="Оплата временно недоступна. Попробуйте позже.")
            return
        try:
            approved = approve_telegram_stars_pre_checkout(
                payload=query.invoice_payload,
                buyer_user_id=str(query.from_user.id),
                currency=query.currency,
                amount_stars=query.total_amount,
                query_id=query.id,
            )
        except Exception:
            approved = False
        await query.answer(
            ok=approved,
            error_message=None if approved else "Счёт недействителен, истёк или не соответствует заказу.",
        )

    @dp.message(F.successful_payment)
    async def stars_successful_payment(message: Message):
        payment = message.successful_payment
        if payment is None or message.from_user is None:
            raise RuntimeError("Telegram successful_payment update is incomplete")
        result = settle_telegram_stars_payment(
            payload=payment.invoice_payload,
            buyer_user_id=str(message.from_user.id),
            currency=payment.currency,
            amount_stars=payment.total_amount,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            provider_payment_charge_id=payment.provider_payment_charge_id,
        )
        if result["duplicate"]:
            await message.answer("Эта оплата уже была учтена. Повторного начисления не произошло.")
        else:
            await message.answer("Оплата Stars подтверждена. Сделка переведена в статус «оплачена».")

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
