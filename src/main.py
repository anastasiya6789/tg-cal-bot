import pytz
import os
import logging
import secrets
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_BASE_URL")
TZ_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
tz = pytz.timezone(TZ_NAME)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= FSM STATES =================
class ScheduleFSM(StatesGroup):
    waiting_custom_date = State()

class EventCreation(StatesGroup):
    choosing_type = State()
    setting_start = State()
    setting_end = State()
    setting_title = State()
    setting_location = State()
    setting_description = State()
    setting_color = State()
    setting_deadline = State()
    confirming = State()

# ================= MAIN MENU =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать событие", callback_data="create")],
        [InlineKeyboardButton(text="📅 Моё расписание", callback_data="schedule")],
        [InlineKeyboardButton(text="🔗 Подключить Google", callback_data="connect")]
    ])
    await message.answer("👋 Привет! Я твой помощник по расписанию.\n\n🔧 Команды:\n/start — Меню\n/create — Создать\n/schedule — Расписание\n/connect — Google аккаунт", reply_markup=kb)

# ================= CONNECT GOOGLE =================
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

# ================= CREATE EVENT FSM =================
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
    await state.update_data(type=callback.data.split("_")[1])
    await callback.message.edit_text("📅 Введите дату и время НАЧАЛА в формате: `ДД.ММ ЧЧ:ММ`\n(или `/now` для текущего времени)")
    await state.set_state(EventCreation.setting_start)
    await callback.answer()

@dp.message(EventCreation.setting_start)
async def set_start(message: types.Message, state: FSMContext):
    if message.text.lower() == "/now":
        dt = datetime.now()
    else:
        try:
            dt = datetime.strptime(message.text, "%d.%m %H:%M").replace(year=datetime.now().year)
        except ValueError:
            await message.answer("❌ Неверный формат. Пример: `10.04 14:00`")
            return
    await state.update_data(start=dt.isoformat())
    await message.answer("📅 Введите дату и время ОКОНЧАНИЯ: `ДД.ММ ЧЧ:ММ`")
    await state.set_state(EventCreation.setting_end)

@dp.message(EventCreation.setting_end)
async def set_end(message: types.Message, state: FSMContext):
    try:
        dt = datetime.strptime(message.text, "%d.%m %H:%M").replace(year=datetime.now().year)
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: `10.04 15:30`")
        return
    await state.update_data(end=dt.isoformat())
    await message.answer("📝 Введите название события:")
    await state.set_state(EventCreation.setting_title)

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
    elif current == EventCreation.setting_deadline:
        await state.update_data(deadline=None)
        await confirm_event(message, state)
    else:
        await message.answer("❌ Пропуск доступен только для локации, описания или дедлайна")
    await message.delete()

@dp.message(EventCreation.setting_location)
async def set_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await message.answer("📄 Введите описание (или `/skip`):")
    await state.set_state(EventCreation.setting_description)

@dp.message(EventCreation.setting_description)
async def set_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    data = await state.get_data()
    if data.get("type") == "task":
        await message.answer("⏰ Введите ДЕДЛАЙН: `ДД.ММ ЧЧ:ММ` (или `/skip`):")
        await state.set_state(EventCreation.setting_deadline)
    else:
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

@dp.message(EventCreation.setting_deadline)
async def set_deadline(message: types.Message, state: FSMContext):
    if message.text.lower() == "/skip":
        await state.update_data(deadline=None)
    else:
        try:
            dt = datetime.strptime(message.text, "%d.%m %H:%M").replace(year=datetime.now().year)
            await state.update_data(deadline=dt.strftime("%d.%m %H:%M"))
        except ValueError:
            await message.answer("❌ Неверный формат. Пример: `15.04 18:30`")
            return
    await confirm_event(message, state)

async def confirm_event(message: types.Message, state: FSMContext):
    data = await state.get_data()
    start_str = data['start'][:16].replace('T', ' ')
    end_str = data['end'][:16].replace('T', ' ')
    preview = (
        f"📋 <b>Предпросмотр</b>\n\n"
        f"📌 Тип: {data.get('type')}\n"
        f"🔖 Название: {data.get('title')}\n"
        f"🕒 Начало: {start_str}\n"
        f"🏁 Окончание: {end_str}\n"
        f"📍 Локация: {data.get('location') or '—'}\n"
        f"📝 Описание: {data.get('description') or '—'}\n"
    )
    if data.get("deadline"): preview += f"⏰ Дедлайн: {data['deadline']}\n"
    preview += "\nОтправить в календарь?"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="confirm_create"),
         InlineKeyboardButton(text="✏️ Изменить", callback_data="cancel")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
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
        "color": data.get("color"),
        "location": data.get("location"),
        "description": data.get("description"),
        "deadline": data.get("deadline")
    }

    success, msg = await create_event(callback.from_user.id, event_data)
    await callback.message.edit_text(msg)
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data.in_({"edit_event", "cancel"}))
async def cancel_creation(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Создание отменено. Введите `/create` чтобы начать заново.")
    await callback.answer()

# ================= SCHEDULE VIEW =================
async def show_schedule_view(message_or_cb, user_id, period, date_str, offset=0):
    # ✅ Исправлено: распаковываем 4 значения (добавлен период данных)
    success, text, has_more, _ = await get_schedule(user_id, period, date_str, offset)
    if not success:
        await (message_or_cb.message.edit_text(text) if hasattr(message_or_cb, 'message') else message_or_cb.answer(text))
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sched_{period}_{date_str}_{offset}_prev"),
         InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"sched_{period}_{date_str}_{offset}_next")],
        [InlineKeyboardButton(text="📅 Другая дата", callback_data="sched_custom"),
         InlineKeyboardButton(text="🔄 Обновить", callback_data=f"sched_{period}_{date_str}_0")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="start")]
    ])
    if has_more:
        kb.inline_keyboard.append([InlineKeyboardButton(text="📄 Ещё события", callback_data=f"sched_{period}_{date_str}_{offset+8}")])
    
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.edit_text(text, reply_markup=kb)
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()

@dp.message(Command("schedule"))
@dp.callback_query(F.data == "schedule")
async def cmd_schedule(message_or_cb: types.Message | types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📆 День", callback_data="sched_day"),
         InlineKeyboardButton(text="🗓 Неделя", callback_data="sched_week"),
         InlineKeyboardButton(text="📅 Месяц", callback_data="sched_month")]
    ])
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("Выберите период:", reply_markup=kb)
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()

@dp.callback_query(F.data.in_({"sched_day", "sched_week", "sched_month"}))
async def init_schedule(callback: types.CallbackQuery):
    period = callback.data.split("_")[1]
    today = datetime.now().strftime("%Y-%m-%d")
    await show_schedule_view(callback, callback.from_user.id, period, today, 0)

@dp.callback_query(F.data.startswith("sched_") & ~F.data.in_({"sched_day", "sched_week", "sched_month", "sched_custom"}))
async def navigate_schedule(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    period = parts[1]
    date_str = parts[2]
    offset = int(parts[3])
    direction = parts[4] if len(parts) > 4 else None

    base_dt = datetime.strptime(date_str, "%Y-%m-%d")
    if direction == "next":
        if period == "day": base_dt += timedelta(days=1)
        elif period == "week": base_dt += timedelta(days=7)
        elif period == "month":
            m = base_dt.month + 1
            y = base_dt.year
            if m > 12: m, y = 1, y + 1
            base_dt = base_dt.replace(year=y, month=m, day=1)
    elif direction == "prev":
        if period == "day": base_dt -= timedelta(days=1)
        elif period == "week": base_dt -= timedelta(days=7)
        elif period == "month":
            m = base_dt.month - 1
            y = base_dt.year
            if m < 1: m, y = 12, y - 1
            base_dt = base_dt.replace(year=y, month=m, day=1)

    await show_schedule_view(callback, callback.from_user.id, period, base_dt.strftime("%Y-%m-%d"), 0)  # ✅ Сброс оффсета при навигации

# ================= CUSTOM DATE FSM =================
@dp.callback_query(F.data == "sched_custom")
async def ask_custom_date(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ScheduleFSM.waiting_custom_date)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="schedule")]])
    await callback.message.edit_text("📅 Введите дату в формате `ДД.ММ.ГГГГ` (например: `15.04.2026`):", reply_markup=kb)
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
    await message.answer("❌ Неверный формат. Введите дату как `15.04.2026` или нажмите /skip для отмены")

# ✅ Исправлено: добавлен StateFilter для корректной работы с состоянием
@dp.callback_query(F.data == "schedule", StateFilter(ScheduleFSM.waiting_custom_date))
@dp.message(F.text.lower() == "/skip", StateFilter(ScheduleFSM.waiting_custom_date))
async def cancel_custom_date(message_or_cb: types.Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("❌ Ввод даты отменён. Выберите период:")
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()

# ================= WEBHOOK & STARTUP =================
async def gcal_callback(request):
    code = request.query.get("code")
    state = request.query.get("state")
    if not code or not state:
        return web.Response(text="Ошибка авторизации", status=400)
    user_id = int(state.split("_")[0])
    try:
        await handle_callback(code, state, user_id)
        await bot.send_message(user_id, "✅ Google аккаунт успешно подключён!")
    except Exception as e:
        await bot.send_message(user_id, f"❌ Ошибка подключения: {e}")
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

if __name__ == "__main__":
    main()