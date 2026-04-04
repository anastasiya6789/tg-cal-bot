import pytz
import os
import logging
import secrets
import traceback
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from db import init_db
from oauth import get_auth_url, handle_callback
from gcal import create_event, get_schedule

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_BASE_URL")
TZ_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
tz = pytz.timezone(TZ_NAME)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.errors()
async def errors_handler(update: types.Update, exception: Exception):
    logger.error(f"❌ Ошибка: {exception}\n{traceback.format_exc()}")
    return True

class ScheduleFSM(StatesGroup):
    waiting_custom_date = State()

class EventCreation(StatesGroup):
    choosing_type = State()
    setting_deadline = State()
    setting_start = State()
    setting_end = State()
    setting_title = State()
    setting_location = State()
    setting_description = State()
    setting_color = State()
    confirming = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать событие", callback_data="create")],
        [InlineKeyboardButton(text="📅 Моё расписание", callback_data="schedule")],
        [InlineKeyboardButton(text="🔗 Подключить Google", callback_data="connect")]
    ])
    await message.answer("👋 Привет! Я твой помощник по расписанию.\n\n🔧 Команды:\n/start — Меню\n/create — Создать\n/schedule — Расписание", reply_markup=kb)

@dp.callback_query(F.data == "start")
async def back_to_menu(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать событие", callback_data="create")],
        [InlineKeyboardButton(text="📅 Моё расписание", callback_data="schedule")],
        [InlineKeyboardButton(text="🔗 Подключить Google", callback_data="connect")]
    ])
    await callback.message.edit_text("👋 Главное меню:", reply_markup=kb)
    await callback.answer()

@dp.message(Command("connect"))
@dp.callback_query(F.data == "connect")
async def cmd_connect(message_or_cb: types.Message | types.CallbackQuery):
    user_id = message_or_cb.from_user.id
    state = f"{user_id}_{secrets.token_hex(8)}"
    url = get_auth_url(state)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔐 Авторизовать Google", url=url)]])
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("🔐 Нажми кнопку, чтобы разрешить доступ к календарю:", reply_markup=kb)
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()

@dp.message(Command("create"))
@dp.callback_query(F.data == "create")
async def start_creation(message_or_cb: types.Message | types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Встреча", callback_data="type_meeting"),
         InlineKeyboardButton(text="✅ Задача", callback_data="type_task"),
         InlineKeyboardButton(text="🎯 Мероприятие", callback_data="type_event")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("Выберите тип события:", reply_markup=kb)
    await state.set_state(EventCreation.choosing_type)
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()

@dp.callback_query(F.data.startswith("type_"))
async def choose_type(callback: types.CallbackQuery, state: FSMContext):
    event_type = callback.data.split("_")[1]
    await state.update_data(type=event_type)
    
    if event_type == "task":
        await callback.message.edit_text("✅ Введите ДЕДЛАЙН задачи: `ДД.ММ ЧЧ:ММ`\n(или `/now` для сейчас)")
        await state.set_state(EventCreation.setting_deadline)
    else:
        await callback.message.edit_text("📅 Введите дату и время НАЧАЛА: `ДД.ММ ЧЧ:ММ`\n(или `/now` для сейчас)")
        await state.set_state(EventCreation.setting_start)
    await callback.answer()

# ================= ЗАДАЧИ: только дедлайн =================
@dp.message(EventCreation.setting_deadline)
async def set_deadline(message: types.Message, state: FSMContext):
    if message.text.lower() == "/now":
        dt = datetime.now(tz)  # ✅ Timezone-aware datetime
    else:
        try:
            dt = datetime.strptime(message.text, "%d.%m %H:%M")
            dt = dt.replace(year=datetime.now(tz).year)
            dt = tz.localize(dt)  # ✅ Добавляем часовой пояс
        except ValueError:
            await message.answer("❌ Неверный формат. Пример: `10.04 14:00`")
            return
    await state.update_data(start=dt.isoformat(), end=dt.isoformat(), deadline=dt.strftime("%d.%m %H:%M"))
    await message.answer("📝 Введите название задачи:")
    await state.set_state(EventCreation.setting_title)

# ================= ВСТРЕЧИ/МЕРОПРИЯТИЯ: начало + конец =================
@dp.message(EventCreation.setting_start)
async def set_start(message: types.Message, state: FSMContext):
    if message.text.lower() == "/now":
        dt = datetime.now(tz)
    else:
        try:
            dt = datetime.strptime(message.text, "%d.%m %H:%M")
            dt = dt.replace(year=datetime.now(tz).year)
            dt = tz.localize(dt)
        except ValueError:
            await message.answer("❌ Неверный формат. Пример: `10.04 14:00`")
            return
    await state.update_data(start=dt.isoformat())
    await message.answer("📅 Введите дату и время ОКОНЧАНИЯ: `ДД.ММ ЧЧ:ММ`")
    await state.set_state(EventCreation.setting_end)

@dp.message(EventCreation.setting_end)
async def set_end(message: types.Message, state: FSMContext):
    try:
        dt = datetime.strptime(message.text, "%d.%m %H:%M")
        dt = dt.replace(year=datetime.now(tz).year)
        dt = tz.localize(dt)
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: `10.04 15:30`")
        return
    await state.update_data(end=dt.isoformat())
    await message.answer("📝 Введите название события:")
    await state.set_state(EventCreation.setting_title)

# ================= ОБЩИЕ ШАГИ =================
@dp.message(EventCreation.setting_title)
async def set_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("📍 Введите локацию (или `/skip`):")
    await state.set_state(EventCreation.setting_location)

@dp.message(F.text.lower() == "/skip")
async def skip_field(message: types.Message, state: FSMContext):
    current = await state.get_state()
    if current == EventCreation.setting_location:
        await state.update_data(location=None)
        await message.answer("📄 Введите описание (или `/skip`):")
        await state.set_state(EventCreation.setting_description)
    elif current == EventCreation.setting_description:
        await state.update_data(description=None)
        await show_color_selection(message, state)
    else:
        await message.answer("❌ Пропуск доступен только для локации или описания")
    await message.delete()

@dp.message(EventCreation.setting_location)
async def set_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await message.answer("📄 Введите описание (или `/skip`):")
    await state.set_state(EventCreation.setting_description)

@dp.message(EventCreation.setting_description)
async def set_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await show_color_selection(message, state)

async def show_color_selection(message, state):
    data = await state.get_data()
    colors = {
        "meeting": [("🔵 Синий", "9"), ("🟢 Зелёный", "10"), ("🟣 Фиолетовый", "11")],
        "task": [("🔴 Красный", "4"), ("🟠 Оранжевый", "5"), ("🟡 Жёлтый", "3")],
        "event": [("🌸 Розовый", "1"), ("⚫ Чёрный", "8"), ("⚪ Серый", "7")]
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"color_{code}")]
        for name, code in colors.get(data.get("type", "event"), colors["event"])
    ] + [[InlineKeyboardButton(text="⏭ Стандартный", callback_data="color_skip")]])
    await message.answer("🎨 Выберите цвет:", reply_markup=kb)
    await state.set_state(EventCreation.setting_color)

@dp.callback_query(F.data.startswith("color_"))
async def choose_color(callback: types.CallbackQuery, state: FSMContext):
    color = None if callback.data == "color_skip" else callback.data.split("_")[1]
    await state.update_data(color=color)
    await confirm_event(callback.message, state)
    await callback.answer()

async def confirm_event(message: types.Message, state: FSMContext):
    data = await state.get_data()
    type_map = {"meeting": "Встреча", "task": "Задача", "event": "Мероприятие"}
    
    if data.get("type") == "task":
        deadline_str = data.get('deadline', data.get('start', '')[:16].replace('T', ' '))
        preview = (
            f"✅ <b>Предпросмотр задачи</b>\n\n"
            f"🔖 Название: {data.get('title')}\n"
            f"⏰ Дедлайн: {deadline_str}\n"
            f"📍 Локация: {data.get('location') or '—'}\n"
            f"📝 Описание: {data.get('description') or '—'}\n"
        )
    else:
        start_str = data['start'][:16].replace('T', ' ')
        end_str = data['end'][:16].replace('T', ' ')
        preview = (
            f"📋 <b>Предпросмотр</b>\n\n"
            f"📌 Тип: {type_map.get(data.get('type'), 'Событие')}\n"
            f"🔖 Название: {data.get('title')}\n"
            f"🕒 Время: {start_str} – {end_str}\n"
            f"📍 Локация: {data.get('location') or '—'}\n"
            f"📝 Описание: {data.get('description') or '—'}\n"
        )
    
    preview += "\nОтправить?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="confirm_create"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    await message.answer(preview, reply_markup=kb, parse_mode="HTML")
    await state.set_state(EventCreation.confirming)

@dp.callback_query(F.data == "confirm_create")
async def finalize_event(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    type_map = {"meeting": "Встреча", "task": "Задача", "event": "Мероприятие"}
    title = f"{data.get('title')} ({type_map.get(data.get('type'), 'Событие')})"

    event_data = {
        "title": title,
        "start": data.get('start'),
        "end": data.get('end'),
        "type": data.get("type"),
        "color": data.get('color'),
        "location": data.get('location'),
        "description": data.get('description'),
        "deadline": data.get('deadline')
    }

    success, msg = await create_event(callback.from_user.id, event_data)
    await callback.message.edit_text(msg)
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "cancel")
async def cancel_creation(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Создание отменено. Введите `/create` чтобы начать заново.")
    await callback.answer()

# ================= SCHEDULE VIEW =================
async def show_schedule_view(message_or_cb, user_id, period, date_str, offset=0):
    try:
        success, text, has_more, _ = await get_schedule(user_id, period, date_str, offset)
        if not success:
            if isinstance(message_or_cb, types.Message):
                await message_or_cb.answer(text)
            else:
                await message_or_cb.message.edit_text(text)
                await message_or_cb.answer()
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sched|{period}|{date_str}|{offset}|prev"),
             InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"sched|{period}|{date_str}|{offset}|next")],
            [InlineKeyboardButton(text="📅 Другая дата", callback_data="sched_custom"),
             InlineKeyboardButton(text="🔄 Обновить", callback_data=f"sched|{period}|{date_str}|0|refresh")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="start")]
        ])
        if has_more:
            kb.inline_keyboard.append([InlineKeyboardButton(text="📄 Ещё события", callback_data=f"sched|{period}|{date_str}|{offset+8}|more")])

        if isinstance(message_or_cb, types.Message):
            await message_or_cb.answer(text, reply_markup=kb)
        else:
            try:
                await message_or_cb.message.edit_text(text, reply_markup=kb)
            except Exception as edit_err:
                if "message is not modified" in str(edit_err).lower():
                    await message_or_cb.answer("✅ Обновлено")
                else:
                    logger.error(f"Edit error: {edit_err}")
                    await message_or_cb.answer("⚠️ Ошибка обновления")
            await message_or_cb.answer()
    except Exception as e:
        logger.error(f"Ошибка в show_schedule_view: {e}\n{traceback.format_exc()}")
        err_text = "❌ Ошибка загрузки расписания"
        if isinstance(message_or_cb, types.Message):
            await message_or_cb.answer(err_text)
        else:
            await message_or_cb.message.edit_text(err_text)
            await message_or_cb.answer()

@dp.message(Command("schedule"))
@dp.callback_query(F.data == "schedule")
async def cmd_schedule(message_or_cb: types.Message | types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📆 День", callback_data="sched_init|day"),
         InlineKeyboardButton(text="🗓 Неделя", callback_data="sched_init|week"),
         InlineKeyboardButton(text="📅 Месяц", callback_data="sched_init|month")]
    ])
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("Выберите период:", reply_markup=kb)
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()

@dp.callback_query(F.data.startswith("sched_init|"))
async def init_schedule(callback: types.CallbackQuery):
    period = callback.data.split("|")[1]
    today = datetime.now().strftime("%Y-%m-%d")
    await show_schedule_view(callback, callback.from_user.id, period, today, 0)
    await callback.answer()

@dp.callback_query(F.data.startswith("sched|"))
async def navigate_schedule(callback: types.CallbackQuery):
    try:
        _, period, date_str, offset_str, action = callback.data.split("|")
        offset = int(offset_str)
        base_dt = datetime.strptime(date_str, "%Y-%m-%d")
        if action in ("next", "prev"):
            if action == "next":
                if period == "day": base_dt += timedelta(days=1)
                elif period == "week": base_dt += timedelta(days=7)
                elif period == "month":
                    m = base_dt.month + 1
                    y = base_dt.year
                    if m > 12: m, y = 1, y + 1
                    base_dt = base_dt.replace(year=y, month=m, day=1)
            else:
                if period == "day": base_dt -= timedelta(days=1)
                elif period == "week": base_dt -= timedelta(days=7)
                elif period == "month":
                    m = base_dt.month - 1
                    y = base_dt.year
                    if m < 1: m, y = 12, y - 1
                    base_dt = base_dt.replace(year=y, month=m, day=1)
            await show_schedule_view(callback, callback.from_user.id, period, base_dt.strftime("%Y-%m-%d"), 0)
        elif action == "more":
            await show_schedule_view(callback, callback.from_user.id, period, date_str, offset)
        elif action == "refresh":
            await show_schedule_view(callback, callback.from_user.id, period, date_str, 0)
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка в navigate_schedule: {e}\n{traceback.format_exc()}")
        await callback.answer("❌ Ошибка навигации", show_alert=True)

@dp.callback_query(F.data == "sched_custom")
async def ask_custom_date(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ScheduleFSM.waiting_custom_date)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="schedule")]])
    await callback.message.edit_text("📅 Введите дату: `ДД.ММ.ГГГГ` (пример: `15.04.2026`):", reply_markup=kb)
    await callback.answer()

@dp.message(ScheduleFSM.waiting_custom_date, F.text.regexp(r"^\d{2}\.\d{2}\.\d{4}$"))
async def handle_custom_date(message: types.Message, state: FSMContext):
    try:
        dt = datetime.strptime(message.text, "%d.%m.%Y")
        dt = tz.localize(dt.replace(hour=12, minute=0, second=0))
        await state.clear()
        await show_schedule_view(message, message.from_user.id, "day", dt.strftime("%Y-%m-%d"), 0)
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: `15.04.2026`")

@dp.message(ScheduleFSM.waiting_custom_date)
async def invalid_custom_date(message: types.Message, state: FSMContext):
    if message.text.lower() not in ["/skip", "отмена", "назад"]:
        await message.answer("❌ Неверный формат. Введите `15.04.2026` или нажмите /skip")

@dp.callback_query(F.data == "schedule", StateFilter(ScheduleFSM.waiting_custom_date))
@dp.message(F.text.lower() == "/skip", StateFilter(ScheduleFSM.waiting_custom_date))
async def cancel_custom_date(message_or_cb: types.Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("❌ Отменено. Выберите период:")
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()

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
    return web.HTTPFound(f"https://t.me/{bot.me.username}")

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