# main.py
import logging
import os
import traceback
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import BOT_TOKEN, WEBHOOK_URL, BOT_USERNAME, tz, logger
from db import init_db
from oauth import handle_callback
from handlers import start, create, schedule, manage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Регистрируем мидлварь ошибок
dp.errors.middleware(ErrorsMiddleware())

# Регистрируем роутеры
dp.include_router(start.router)
dp.include_router(create.router)
dp.include_router(schedule.router)
dp.include_router(manage.router)

# OAuth callback
async def gcal_callback(request):
    code = request.query.get("code")
    state = request.query.get("state")
    if not code or not state:
        return web.Response(text="Ошибка авторизации", status=400)
    user_id = int(state.split("_")[0])
    try:
        await handle_callback(code, state, user_id)
        await bot.send_message(user_id, "✅ Google аккаунт подключён!")
    except Exception as e:
        logger.error(f"OAuth error: {e}\n{traceback.format_exc()}")
        await bot.send_message(user_id, f"❌ Ошибка: {e}")
    # Используем BOT_USERNAME из config вместо bot.me.username
    return web.HTTPFound(f"https://t.me/{BOT_USERNAME}")

async def on_startup(app):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}/webhook")

def main():
    import asyncio
    asyncio.run(init_db())
    logger.info("✅ База данных инициализирована")
    
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    
    app.on_startup.append(on_startup)
    app.router.add_get("/gcal/callback", gcal_callback)
    
    logger.info(f"🚀 Запуск бота на порту {os.getenv('PORT', 8080)}")
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    main()