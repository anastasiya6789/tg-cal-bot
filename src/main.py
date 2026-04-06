# main.py
import logging
import os
import traceback
import asyncio
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import BOT_TOKEN, WEBHOOK_URL, BOT_USERNAME, tz, logger, TZ_NAME
from db import init_db, get_pending_reminders, mark_reminder_sent
from oauth import handle_callback, get_credentials
from handlers import start, create, schedule, manage, reminders # ✅

# Импортируем новую функцию форматирования для уведомлений
from gcal import fmt_evt, parse_dt
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.errors()
async def errors_handler(update: types.Update, exception: Exception):
    logger.error(f"❌ Ошибка: {exception}\n{traceback.format_exc()}")
    return True

# Регистрируем роутеры
dp.include_router(start.router)
dp.include_router(create.router)
dp.include_router(schedule.router)
dp.include_router(manage.router)
dp.include_router(reminders.router) # ✅

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
    return web.HTTPFound(f"https://t.me/{BOT_USERNAME}")

# ✅ Health check
async def health_check(request):
    try:
        me = await bot.get_me()
        return web.json_response({"status": "ok", "bot": me.username, "timestamp": datetime.now().isoformat()})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

# ==================== ФОНОВАЯ ЗАДАЧА НАПОМИНАНИЙ ====================

async def check_reminders_task():
    """Запускается раз в 2 минуты и проверяет события"""
    logger.info("🔔 Фоновая задача напоминаний запущена")
    import pytz
    local_tz = pytz.timezone(TZ_NAME)
    
    while True:
        try:
            await asyncio.sleep(120) # Проверка каждые 2 минуты
            
            reminders = await get_pending_reminders()
            if not reminders:
                continue
                
            now = datetime.now(local_tz)
            processed_count = 0
            
            # Группируем по юзерам, чтобы не создавать соединение с Google 100 раз
            # Но для простоты и надежности (т.к. токены индивидуальные) пройдемся циклом
            
            for user_id, event_id, minutes_before in reminders:
                try:
                    creds = await get_credentials(user_id)
                    if not creds:
                        # Если токены протухли совсем и не рефрешнулись - пропускаем, чтобы не спамить
                        continue
                        
                    svc = build('calendar', 'v3', credentials=creds)
                    event = svc.events().get(calendarId='primary', eventId=event_id).execute()
                    
                    start_str = event.get('start', {}).get('dateTime')
                    if not start_str:
                        # Если событие на весь день, пока пропускаем (или можно реализовать отдельно)
                        continue
                        
                    event_dt = parse_dt(start_str)
                    if not event_dt:
                        continue
                        
                    if event_dt.tzinfo is None:
                        event_dt = local_tz.localize(event_dt)
                    else:
                        event_dt = event_dt.astimezone(local_tz)
                        
                    remind_time = event_dt - timedelta(minutes=minutes_before)
                    
                    # Если время пришло (или уже прошло, но недавно)
                    if now >= remind_time and (now - remind_time).total_seconds() < 3600: # Окно 1 час
                        message = f"🔔 <b>Напоминание:</b>\n\n{fmt_evt(event)}"
                        await bot.send_message(user_id, message, parse_mode="HTML")
                        
                        await mark_reminder_sent(user_id, event_id)
                        logger.info(f"✅ Напоминание отправлено юзеру {user_id} про {event.get('summary')}")
                        processed_count += 1
                        
                except Exception as e:
                    # Ошибка конкретного юзера не должна ломать цикл
                    if "404" in str(e):
                         # Событие удалено в гугле, чистим у себя
                         from db import delete_reminder
                         await delete_reminder(user_id, event_id)
                    continue
                    
            if processed_count > 0:
                logger.info(f"📢 Всего отправлено напоминаний: {processed_count}")

        except Exception as e:
            logger.error(f"❌ Ошибка в цикле напоминаний: {e}")

# ================================================================

async def on_startup(app):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}/webhook")
    # Запускаем фоновую задачу
    asyncio.create_task(check_reminders_task())

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
    app.router.add_get("/health", health_check)
    
    logger.info(f"🚀 Запуск бота на порту {os.getenv('PORT', 8080)}")
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    main()