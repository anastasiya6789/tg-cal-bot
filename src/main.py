import os
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
import secrets
from db import init_db
from oauth import get_auth_url, handle_callback
from schedule_parser import parse_schedule
from gcal import create_events

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_BASE_URL")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("👋 Привет! Пришли расписание текстом.\nФормат: `Пн 10:00-11:30 Математика`")

@dp.message(Command("connect"))
async def cmd_connect(message: types.Message):
    state = f"{message.from_user.id}_{secrets.token_hex(8)}"
    url = get_auth_url(state)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Подключить Google", url=url)]])
    await message.answer("🔐 Нажми кнопку, чтобы авторизовать доступ к календарю:", reply_markup=kb)

@dp.message(F.text)
async def handle_schedule(message: types.Message):
    events = parse_schedule(message.text, TIMEZONE)
    if not events:
        await message.answer("⚠️ Не понял формат. Пример:\n`Пн 10:00-11:30 Математика`\n`Вт 14:00-15:00 Физика`")
        return
    ok, msg = await create_events(message.from_user.id, events, TIMEZONE)
    await message.answer(msg)

async def gcal_callback(request):
    code = request.query.get("code")
    state = request.query.get("state")
    if not code or not state:
        return web.Response(text="Ошибка авторизации", status=400)
    user_id = int(state.split("_")[0])
    await handle_callback(code, state, user_id)
    await bot.send_message(user_id, "✅ Google подключен! Теперь присылай расписание.")
    return web.HTTPFound(f"https://t.me/{bot.me.username}")

async def on_startup(app):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")

def main():
    import asyncio
    asyncio.run(init_db())

    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.router.add_get("/gcal/callback", gcal_callback)

    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))


print("hello")

if __name__ == "__main__":
    main()