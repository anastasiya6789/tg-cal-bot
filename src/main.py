import os
import logging
import secrets
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class EventCreation(StatesGroup):
    choosing_type = State()
    setting_color = State()
    setting_title = State()
    setting_datetime = State()
    setting_location = State()
    setting_description = State()
    setting_deadline = State()
    confirming = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать событие", callback_data="create")],
        [InlineKeyboardButton(text="📅 Моё расписание", callback_data="schedule")],
        [InlineKeyboardButton(text="🔗 Подключить Google", callback_data="connect")]
    ])
    await message.answer("👋 Привет! Я твой помощник по расписанию.\n\n🔧 Команды:\n/start — Меню\n/create — Создать\n/schedule — Расписание\n/connect — Google аккаунт", reply_markup=kb)

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
    colors = {
        "meeting": [("🔵 Синий", "9"), ("🟢 Зелёный", "10"), ("🟣 Фиолетовый", "11")],
        "task": [("🔴 Красный", "4"), ("🟠 Оранжевый", "5"), ("🟡 Жёлтый", "3")],
        "event": [("🌸 Розовый", "1"), ("⚫ Чёрный", "8"), ("⚪ Серый", "7")]
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"color_{code}")]
        for name, code in colors.get(event_type, colors["event"])
    ] + [[InlineKeyboardButton(text="⏭ Стандартный", callback_data="color_skip")]])
    await callback.message.edit_text(f"Выберите цвет для {event_type}:", reply_markup=kb)
    await state.set_state(EventCreation.setting_color)
    await callback.answer()

@dp.callback_query(F.data.startswith("color_"))
async def choose_color(callback: types.CallbackQuery, state: FSMContext):
    color = None if callback.data == "color_skip" else callback.data.split("_")[1]
    await state.update_data(color=color)
    await callback.message.edit_text("📝 Введите название события:")
    await state.set_state(EventCreation.setting_title)
    await callback.answer()

@dp.message(EventCreation.setting_title)
async def set_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("📅 Введите дату и время начала в формате: ДД.ММ ЧЧ:ММ\n(например: 10.04 14:00 или /skip для сейчас)")
    await state.set_state(EventCreation.setting_datetime)

@dp.message(EventCreation.setting_datetime)
async def set_datetime(message: types.Message, state: FSMContext):
    if message.text.lower() in ['/skip', 'сейчас']:
        start_dt = datetime.now().isoformat()
        end_dt = (datetime.now() + timedelta(hours=1)).isoformat()
    else:
        try:
            dt = datetime.strptime(message.text, "%d.%m %H:%M")
            dt = dt.replace(year=datetime.now().year)
            start_dt = dt.isoformat()
            end_dt = (dt + timedelta(hours=1)).isoformat()
        except ValueError:
            await message.answer("❌ Неверный формат. Пример: 10.04 14:00")
            return
    await state.update_data(start=start_dt, end=end_dt)
    await message.answer("📍 Введите локацию (или /skip):")
    await state.set_state(EventCreation.setting_location)

@dp.message(F.text.lower() == "/skip")
async def skip_field(message: types.Message, state: FSMContext):
    current = await state.get_state()
    if current == EventCreation.setting_location:
        await state.update_data(location=None)
        await message.answer("📄 Введите описание (или /skip):")
        await state.set_state(EventCreation.setting_description)
    elif current == EventCreation.setting_description:
        await state.update_data(description=None)
        await confirm_event(message, state)
    elif current == EventCreation.setting_deadline:
        await state.update_data(deadline=None)
        await confirm_event(message, state)
    else:
        await message.answer("❌ Пропуск доступен только для локации, описания или дедлайна")
    await message.delete()

@dp.message(EventCreation.setting_location)
async def set_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await message.answer("📄 Введите описание (или /skip):")
    await state.set_state(EventCreation.setting_description)

@dp.message(EventCreation.setting_description)
async def set_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    data = await state.get_data()
    if data.get("type") == "task":
        await message.answer("⏰ Введите дедлайн в формате: ДД.ММ ЧЧ:ММ (или /skip):")
        await state.set_state(EventCreation.setting_deadline)
    else:
        await confirm_event(message, state)

@dp.message(EventCreation.setting_deadline)
async def set_deadline(message: types.Message, state: FSMContext):
    if message.text.lower() == "/skip":
        await state.update_data(deadline=None)
    else:
        try:
            dt = datetime.strptime(message.text, "%d.%m %H:%M")
            dt = dt.replace(year=datetime.now().year)
            await state.update_data(deadline=dt.strftime("%d.%m %H:%M"))
        except ValueError:
            await message.answer("❌ Неверный формат. Пример: 15.04 18:30")
            return
    await confirm_event(message, state)

async def confirm_event(message: types.Message, state: FSMContext):
    data = await state.get_data()
    preview = (
        f"📋 <b>Предпросмотр</b>\n\n"
        f"📌 Тип: {data.get('type')}\n"
        f"🔖 Название: {data.get('title')}\n"
        f"📍 Локация: {data.get('location') or '—'}\n"
        f"📝 Описание: {data.get('description') or '—'}\n"
        f"🕒 Начало: {data['start'][:16].replace('T', ' ')}\n"
    )
    if data.get("deadline"): preview += f"⏰ Дедлайн: {data['deadline']}\n"
    preview += "\nОтправить в календарь?"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="confirm_create"),
         InlineKeyboardButton(text="✏️ Изменить", callback_data="edit_event")],
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
    await callback.message.edit_text("❌ Создание отменено. Введите /create чтобы начать заново.")
    await callback.answer()

@dp.message(Command("schedule"))
@dp.callback_query(F.data == "schedule")
async def cmd_schedule(message_or_cb: types.Message | types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📆 Сегодня", callback_data="view_today"),
         InlineKeyboardButton(text="🗓 Неделя", callback_data="view_week")],
        [InlineKeyboardButton(text="📅 Месяц", callback_data="view_month")]
    ])
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("Выберите период для просмотра:", reply_markup=kb)
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()

@dp.callback_query(F.data.startswith("view_"))
async def view_schedule(callback: types.CallbackQuery):
    period = callback.data.split("_")[1]
    success, text = await get_schedule(callback.from_user.id, period)
    if not success:
        await callback.message.edit_text(text)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="schedule")],
            [InlineKeyboardButton(text="➕ Создать", callback_data="create")]
        ])
        await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

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