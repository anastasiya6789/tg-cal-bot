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

# ==================== ФОНОВАЯ ЗАДАЧА НАПОМИНАНИЙ (ОБНОВЛЁННАЯ) ====================

async def check_reminders_task():
    """Проверяет ВСЕ события из календаря и шлёт напоминания"""
    logger.info("🔔 Фоновая задача напоминаний запущена (режим: все события)")
    import pytz
    from datetime import timedelta
    from googleapiclient.discovery import build
    from gcal import fmt_evt, parse_dt
    from oauth import get_credentials
    from db import get_token, save_reminder, mark_reminder_sent
    
    local_tz = pytz.timezone(TZ_NAME)
    
    while True:
        try:
            await asyncio.sleep(120)  # Проверка каждые 2 минуты
            now = datetime.now(local_tz)
            
            # Получаем всех пользователей с токенами
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute('SELECT user_id FROM user_tokens') as cur:
                    users = [row[0] for row in await cur.fetchall()]
            
            for user_id in users:
                try:
                    creds = await get_credentials(user_id)
                    if not creds:
                        continue
                    
                    svc = build('calendar', 'v3', credentials=creds)
                    
                    # ✅ Забираем ВСЕ события на ближайшие 2 часа
                    time_min = now.isoformat()
                    time_max = (now + timedelta(hours=2)).isoformat()
                    
                    events_result = svc.events().list(
                        calendarId='primary',
                        timeMin=time_min,
                        timeMax=time_max,
                        singleEvents=True,
                        orderBy='startTime',
                        maxResults=50
                    ).execute()
                    
                    events = events_result.get('items', [])
                    
                    for event in events:
                        event_id = event.get('id')
                        summary = event.get('summary', 'Без названия')
                        
                        # Пропускаем задачи из Tasks API (у них нет точного времени)
                        if event.get('visibility') == 'private' and not event.get('start', {}).get('dateTime'):
                            continue
                            
                        start_str = event.get('start', {}).get('dateTime')
                        if not start_str or 'T' not in start_str:
                            continue  # Пропускаем события на весь день
                            
                        event_dt = parse_dt(start_str)
                        if not event_dt:
                            continue
                            
                        if event_dt.tzinfo is None:
                            event_dt = local_tz.localize(event_dt)
                        else:
                            event_dt = event_dt.astimezone(local_tz)
                        
                        # Проверяем: нужно ли напомнить за 15 минут?
                        remind_time = event_dt - timedelta(minutes=10)
                        time_diff = (now - remind_time).total_seconds()
                        
                        # Если время напоминания пришло (окно ±5 минут)
                        if -300 <= time_diff <= 300:  # ±5 минут
                            # Проверяем, не отправляли ли уже
                            async with aiosqlite.connect(DB_PATH) as db:
                                async with db.execute(
                                    'SELECT is_sent FROM reminders WHERE user_id = ? AND event_id = ?',
                                    (user_id, event_id)
                                ) as cur:
                                    row = await cur.fetchone()
                                
                                if row and row[0]:  # Уже отправлено
                                    continue
                                
                                # Отправляем уведомление
                                message = f"🔔 <b>Напоминание:</b>\n\n{fmt_evt(event)}"
                                await bot.send_message(user_id, message, parse_mode="HTML")
                                
                                # ✅ Создаём или обновляем запись о напоминании
                                await db.execute('''INSERT OR REPLACE INTO reminders 
                                                   (user_id, event_id, remind_minutes, is_sent) 
                                                   VALUES (?, ?, 15, 1)''', 
                                                (user_id, event_id))
                                await db.commit()
                                
                                logger.info(f"✅ Reminder sent to {user_id} for {summary}")
                                
                except Exception as e:
                    logger.error(f"❌ Error checking reminders for user {user_id}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Error in reminder task loop: {e}")
            await asyncio.sleep(60)

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