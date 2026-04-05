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
from db import init_db, save_event_id, delete_event_id
from oauth import get_auth_url, handle_callback
from gcal import create_event, get_schedule, update_event, delete_event, fmt_evt, detect_type, to_iso, parse_dt

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

class EventManage(StatesGroup):
    selecting_event = State()
    choosing_field = State()
    entering_value = State()
    confirming_action = State()

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
        await callback.message.edit_text("✅ Введите ДЕДЛАЙН задачи: `ДД.ММ.ГГГГ ЧЧ:ММ`\n(или `/now` для сейчас)")
        await state.set_state(EventCreation.setting_deadline)
    else:
        await callback.message.edit_text("📅 Введите дату и время НАЧАЛА: `ДД.ММ.ГГГГ ЧЧ:ММ`\n(или `/now` для сейчас)")
        await state.set_state(EventCreation.setting_start)
    await callback.answer()

@dp.message(EventCreation.setting_deadline)
async def set_deadline(message: types.Message, state: FSMContext):
    if message.text.lower() == "/now":
        dt = datetime.now(tz)
    else:
        try:
            if '.' in message.text and len(message.text.split('.')[2].split()[0]) == 4:
                dt = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            else:
                dt = datetime.strptime(message.text, "%d.%m %H:%M")
                dt = dt.replace(year=datetime.now(tz).year)
            dt = tz.localize(dt)
        except ValueError:
            await message.answer("❌ Неверный формат. Пример: `10.04.2026 14:00` или `10.04 14:00`")
            return
    await state.update_data(start=dt.isoformat(), end=dt.isoformat(), deadline=dt.strftime("%d.%m.%Y %H:%M"))
    await message.answer("📝 Введите название задачи:")
    await state.set_state(EventCreation.setting_title)

@dp.message(EventCreation.setting_start)
async def set_start(message: types.Message, state: FSMContext):
    if message.text.lower() == "/now":
        dt = datetime.now(tz)
    else:
        try:
            if '.' in message.text and len(message.text.split('.')[2].split()[0]) == 4:
                dt = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            else:
                dt = datetime.strptime(message.text, "%d.%m %H:%M")
                dt = dt.replace(year=datetime.now(tz).year)
            dt = tz.localize(dt)
        except ValueError:
            await message.answer("❌ Неверный формат. Пример: `10.04.2026 14:00` или `10.04 14:00`")
            return
    await state.update_data(start=dt.isoformat())
    await message.answer("📅 Введите дату и время ОКОНЧАНИЯ: `ДД.ММ.ГГГГ ЧЧ:ММ`")
    await state.set_state(EventCreation.setting_end)

@dp.message(EventCreation.setting_end)
async def set_end(message: types.Message, state: FSMContext):
    try:
        if '.' in message.text and len(message.text.split('.')[2].split()[0]) == 4:
            dt = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        else:
            dt = datetime.strptime(message.text, "%d.%m %H:%M")
            dt = dt.replace(year=datetime.now(tz).year)
        dt = tz.localize(dt)
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: `10.04.2026 15:30` или `10.04 15:30`")
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

    try:
        success, msg, event_id = await create_event(callback.from_user.id, event_data)
        await callback.message.edit_text(msg)
        if success and event_id:
            await save_event_id(callback.from_user.id, event_id)
    except Exception as e:
        logger.error(f"Create error: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")
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
        result = await get_schedule(user_id, period, date_str, offset)
        if len(result) == 5:
            success, text, has_more, date_range, pag_items = result
        else:
            success, text, has_more, date_range = result
            pag_items = []
        
        if not success:
            if isinstance(message_or_cb, types.Message):
                await message_or_cb.answer(text)
            else:
                await message_or_cb.message.edit_text(text)
                await message_or_cb.answer()
            return

        nav_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sched|{period}|{date_str}|{offset}|prev"),
             InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"sched|{period}|{date_str}|{offset}|next")],
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"sched_edit|{date_str}"),
             InlineKeyboardButton(text="🗑 Удалить", callback_data=f"sched_delete|{date_str}")],
            [InlineKeyboardButton(text="📅 Другая дата", callback_data="sched_custom"),
             InlineKeyboardButton(text="🔄 Обновить", callback_data=f"sched|{period}|{date_str}|0|refresh")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="start")]
        ])
        if has_more:
            nav_kb.inline_keyboard.append([InlineKeyboardButton(text="📄 Ещё события", callback_data=f"sched|{period}|{date_str}|{offset+8}|more")])

        if isinstance(message_or_cb, types.Message):
            await message_or_cb.answer(text, reply_markup=nav_kb)
        else:
            try:
                await message_or_cb.message.edit_text(text, reply_markup=nav_kb)
            except Exception as edit_err:
                if "not modified" in str(edit_err).lower():
                    await message_or_cb.answer("✅ Обновлено")
                else:
                    raise
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
    today = datetime.now(tz).strftime("%Y-%m-%d")
    await show_schedule_view(callback, callback.from_user.id, period, today, 0)
    await callback.answer()

@dp.callback_query(F.data.startswith("sched|"))
async def navigate_schedule(callback: types.CallbackQuery):
    try:
        _, period, date_str, offset_str, action = callback.data.split("|")
        offset = int(offset_str)
        base_dt = datetime.strptime(date_str, "%Y-%m-%d")
        base_dt = tz.localize(base_dt.replace(hour=12, minute=0, second=0))
        
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

# ================= УПРАВЛЕНИЕ: ПОЛУЧЕНИЕ СПИСКА СОБЫТИЙ (Calendar + Tasks API) =================
async def _fetch_manageable_events(user_id, date_str):
    from oauth import get_credentials
    from googleapiclient.discovery import build
    
    cr = await get_credentials(user_id)
    if not cr:
        return []
    
    events = []
    
    try:
        svc_cal = build('calendar','v3',credentials=cr)
        target_dt = datetime.strptime(date_str, "%Y-%m-%d")
        target_dt = tz.localize(target_dt.replace(hour=0, minute=0, second=0))
        time_min = to_iso(target_dt)
        time_max = to_iso(target_dt.replace(hour=23, minute=59, second=59))
        
        for e in svc_cal.events().list(calendarId='primary', timeMin=time_min, timeMax=time_max, singleEvents=True, orderBy='startTime').execute().get('items', []):
            events.append({
                'id': e.get('id'),
                'summary': e.get('summary', 'Без названия'),
                'start': e.get('start', {}),
                'end': e.get('end', {}),
                'description': e.get('description', ''),
                'location': e.get('location', ''),
                '_is_tasks_api': False,
                '_raw': e
            })
    except Exception as ex:
        logger.error(f"Fetch calendar events error: {ex}")
    
    try:
        svc_tasks = build('tasks','v1',credentials=cr)
        tmin = (target_dt - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        tmax = (target_dt + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")
        
        for t in svc_tasks.tasks().list(tasklist='@default', dueMin=tmin, dueMax=tmax, showCompleted=False, showHidden=False).execute().get('items', []):
            due = t.get('due')
            if not due: continue
            due_dt = parse_dt(due)
            if not due_dt or due_dt < target_dt or due_dt > target_dt.replace(hour=23, minute=59, second=59):
                continue
            
            events.append({
                'id': t.get('id'),
                'summary': t.get('title', 'Без названия'),
                'start': {'dateTime': due},
                'end': {'dateTime': due},
                'description': t.get('notes', ''),
                'location': '',
                '_is_tasks_api': True,
                '_raw': t
            })
    except Exception as ex:
        logger.error(f"Fetch tasks error: {ex}")
    
    return events

# ================= УПРАВЛЕНИЕ: ВЫБОР СОБЫТИЯ =================
@dp.callback_query(F.data.startswith("sched_edit|"))
@dp.callback_query(F.data.startswith("sched_delete|"))
async def start_manage(callback: types.CallbackQuery, state: FSMContext):
    try:
        action = "edit" if "sched_edit" in callback.data else "delete"
        date_str = callback.data.split("|")[1]
        await state.update_data(manage_action=action, manage_date=date_str)
        
        events = await _fetch_manageable_events(callback.from_user.id, date_str)
        
        if not events:
            await callback.answer("📭 Нет событий для управления на эту дату", show_alert=True)
            return
        
        event_map = {str(i): ev for i, ev in enumerate(events[:10])}
        await state.update_data(event_map=event_map)
        
        buttons = []
        for idx, ev in event_map.items():
            start_data = ev['start']
            dt_str = start_data.get('dateTime') or start_data.get('date')
            if dt_str and 'T' in dt_str:
                time_str = dt_str[11:16]
            else:
                time_str = "весь день"
            
            title = ev['summary'][:30]
            cb_data = f"select_{action}|{idx}"
            buttons.append([InlineKeyboardButton(text=f"{int(idx)+1}. {time_str} — {title}", callback_data=cb_data)])
        
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_schedule")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        action_text = "✏️ Выберите событие для редактирования:" if action == "edit" else "🗑 Выберите событие для удаления:"
        await callback.message.edit_text(f"{action_text}\n\n(Показаны события на {date_str})", reply_markup=kb)
        await callback.answer()
    except Exception as e:
        logger.error(f"Manage start error: {e}\n{traceback.format_exc()}")
        await callback.answer("❌ Ошибка", show_alert=True)

# ================= ВЫБОР СОБЫТИЯ ПО ИНДЕКСУ =================
@dp.callback_query(F.data.startswith("select_edit|"))
@dp.callback_query(F.data.startswith("select_delete|"))
async def handle_select(callback: types.CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split("|")
        action = parts[0].split("_")[1]
        idx = parts[1]
        
        data = await state.get_data()
        event_map = data.get('event_map', {})
        
        if idx not in event_map:
            await callback.answer("❌ Событие не найдено", show_alert=True)
            return
        
        event = event_map[idx]
        event_id = event['id']
        
        await state.update_data(
            selected_event_id=event_id,
            selected_event_raw=event['_raw'],
            selected_is_tasks_api=event.get('_is_tasks_api', False)
        )
        
        if action == "delete":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete|{idx}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_schedule")]
            ])
            await callback.message.edit_text(f"⚠️ Удалить событие?\n📌 {event['summary']}\nЭто действие нельзя отменить.", reply_markup=kb)
        else:
            from gcal import detect_type
            event_type = detect_type(event['_raw']) if not event.get('_is_tasks_api') else 'task'
            await state.update_data(selected_event_type=event_type)
            
            if event_type == "task" or event.get('_is_tasks_api'):
                fields = [("🔖 Название", "title"), ("📅 Дата", "date"), ("⏰ Время", "time")]
            else:
                fields = [
                    ("🔖 Название", "title"), ("📍 Локация", "location"), ("📝 Описание", "description"),
                    ("📅 Дата начала", "start_date"), ("⏰ Время начала", "start_time"),
                    ("📅 Дата окончания", "end_date"), ("⏰ Время окончания", "end_time")
                ]
            
            buttons = [[InlineKeyboardButton(text=name, callback_data=f"edit_field|{field}")] for name, field in fields]
            buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_schedule")])
            kb = InlineKeyboardMarkup(inline_keyboard=buttons)
            await callback.message.edit_text("✏️ Что изменить?", reply_markup=kb)
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Select error: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)

# ================= ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ =================
@dp.callback_query(F.data.startswith("confirm_delete|"))
async def confirm_delete(callback: types.CallbackQuery, state: FSMContext):
    try:
        idx = callback.data.split("|")[1]
        data = await state.get_data()
        event_map = data.get('event_map', {})
        
        if idx not in event_map:
            await callback.message.edit_text("❌ Событие не найдено")
            await callback.answer()
            return
        
        event = event_map[idx]
        event_id = event['id']
        is_tasks_api = event.get('_is_tasks_api', False)
        
        if is_tasks_api:
            from oauth import get_credentials
            from googleapiclient.discovery import build
            cr = await get_credentials(callback.from_user.id)
            if cr:
                try:
                    svc = build('tasks','v1',credentials=cr)
                    svc.tasks().delete(tasklist='@default', task=event_id).execute()
                    success, msg = True, "✅ Удалено!"
                except Exception as ex:
                    err_str = str(ex).lower()
                    if "404" in err_str or "notfound" in err_str:
                        success, msg = True, "✅ Уже удалено"
                    else:
                        success, msg = False, f"❌ Ошибка: {ex}"
            else:
                success, msg = False, "❌ Сначала подключи Google"
        else:
            success, msg = await delete_event(callback.from_user.id, event_id)
        
        if success or "404" in msg or "notFound" in msg:
            await delete_event_id(callback.from_user.id, event_id)
            await callback.message.edit_text(f"✅ Удалено!\n\nНажмите /schedule, чтобы увидеть обновлённое расписание")
        else:
            await callback.message.edit_text(msg)
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Delete confirm error: {e}")
        await callback.answer("❌ Ошибка при удалении", show_alert=True)

# ================= ВЫБОР ПОЛЯ ДЛЯ РЕДАКТИРОВАНИЯ =================
@dp.callback_query(F.data.startswith("edit_field|"))
async def choose_field(callback: types.CallbackQuery, state: FSMContext):
    field = callback.data.split("|")[1]
    await state.update_data(edit_field=field)
    
    field_prompts = {
        "title": "✏️ Введите новое название:",
        "location": "📍 Введите новую локацию:",
        "description": "📝 Введите новое описание:",
        "date": "📅 Введите новую дату: `ДД.ММ.ГГГГ`",
        "time": "⏰ Введите новое время: `ЧЧ:ММ`",
        "start_date": "📅 Введите новую дату начала: `ДД.ММ.ГГГГ`",
        "start_time": "⏰ Введите новое время начала: `ЧЧ:ММ`",
        "end_date": "📅 Введите новую дату окончания: `ДД.ММ.ГГГГ`",
        "end_time": "⏰ Введите новое время окончания: `ЧЧ:ММ`"
    }
    
    await callback.message.edit_text(field_prompts.get(field, "✏️ Введите новое значение:"))
    await state.set_state(EventManage.entering_value)
    await callback.answer()

# ================= ВВОД НОВОГО ЗНАЧЕНИЯ =================
@dp.message(EventManage.entering_value)
async def save_new_value(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        event_id = data.get('selected_event_id')
        event_raw = data.get('selected_event_raw')
        field = data.get('edit_field')
        event_type = data.get('selected_event_type')
        is_tasks_api = data.get('selected_is_tasks_api', False)
        
        if not event_id or not field or not event_raw:
            await message.answer("❌ Ошибка: данные не найдены")
            await state.clear()
            return
        
        if is_tasks_api:
            from oauth import get_credentials
            from googleapiclient.discovery import build
            cr = await get_credentials(message.from_user.id)
            if not cr:
                await message.answer("❌ Сначала подключи Google")
                await state.clear()
                return
            
            svc = build('tasks','v1',credentials=cr)
            
            try:
                task = svc.tasks().get(tasklist='@default', task=event_id).execute()
            except Exception as ex:
                if "404" in str(ex).lower():
                    await message.answer("❌ Задача не найдена")
                else:
                    await message.answer(f"❌ Ошибка: {ex}")
                await state.clear()
                return
            
            if field == "title":
                task['title'] = message.text
            elif field == "description":
                task['notes'] = message.text
            elif field in ["date", "time"]:
                try:
                    if field == "date":
                        if '.' in message.text and len(message.text.split('.')[2]) == 4:
                            new_date = datetime.strptime(message.text, "%d.%m.%Y")
                        else:
                            new_date = datetime.strptime(message.text, "%d.%m")
                            new_date = new_date.replace(year=datetime.now(tz).year)
                    else:
                        new_time = datetime.strptime(message.text, "%H:%M")
                        due = event_raw.get('due', '')
                        if due and 'T' in due:
                            old_date = due[:10]
                            new_dt = datetime.strptime(f"{old_date} {message.text}", "%Y-%m-%d %H:%M")
                        else:
                            new_dt = datetime.now(tz).replace(hour=new_time.hour, minute=new_time.minute)
                    
                    due_str = new_date.strftime("%Y-%m-%dT%H:%M:%S.000Z") if field == "date" else new_dt.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    task['due'] = due_str
                except ValueError as ve:
                    await message.answer(f"❌ Неверный формат: {ve}")
                    return
            
            try:
                svc.tasks().update(tasklist='@default', task=event_id, body=task).execute()
                await message.answer(f"✅ Обновлено!\n\nНажмите /schedule, чтобы увидеть обновлённое расписание")
            except Exception as ex:
                await message.answer(f"❌ Ошибка: {ex}")
            
            await state.clear()
            return
        
        # Calendar API обработка
        update_data = {}
        
        if field == "title":
            update_data['title'] = message.text
        elif field == "location":
            update_data['location'] = message.text
        elif field == "description":
            from gcal import TYPE_TAG_RE
            old_desc = event_raw.get('description', '')
            tag_match = TYPE_TAG_RE.search(old_desc)
            tag = tag_match.group(0) if tag_match else ''
            new_desc = message.text.strip()
            update_data['description'] = f"{new_desc}\n{tag}" if new_desc else tag
        elif field in ["date", "start_date", "end_date"]:
            try:
                if '.' in message.text and len(message.text.split('.')[2]) == 4:
                    new_date = datetime.strptime(message.text, "%d.%m.%Y")
                else:
                    new_date = datetime.strptime(message.text, "%d.%m")
                    new_date = new_date.replace(year=datetime.now(tz).year)
                
                old_start = event_raw['start'].get('dateTime') or event_raw['start'].get('date')
                old_end = event_raw['end'].get('dateTime') or event_raw['end'].get('date')
                
                if field in ["date", "start_date"] and old_start and 'T' in old_start:
                    old_time = old_start[11:16]
                    new_dt = datetime.strptime(f"{new_date.strftime('%Y-%m-%d')} {old_time}", "%Y-%m-%d %H:%M")
                    update_data['start'] = tz.localize(new_dt).isoformat()
                
                if field in ["date", "end_date"] and old_end and 'T' in old_end:
                    old_time = old_end[11:16]
                    new_dt = datetime.strptime(f"{new_date.strftime('%Y-%m-%d')} {old_time}", "%Y-%m-%d %H:%M")
                    update_data['end'] = tz.localize(new_dt).isoformat()
                    
                if 'date' in event_raw['start'] and 'dateTime' not in event_raw['start']:
                    update_data['start'] = {'date': new_date.strftime('%Y-%m-%d')}
                    update_data['end'] = {'date': new_date.strftime('%Y-%m-%d')}
                    
            except ValueError:
                await message.answer("❌ Неверный формат даты. Пример: `10.04.2026`")
                return
        elif field in ["time", "start_time", "end_time"]:
            try:
                new_time = datetime.strptime(message.text, "%H:%M")
                old_start = event_raw['start'].get('dateTime') or event_raw['start'].get('date')
                old_end = event_raw['end'].get('dateTime') or event_raw['end'].get('date')
                
                if field in ["time", "start_time"] and old_start and 'T' in old_start:
                    old_date = old_start[:10]
                    new_dt = datetime.strptime(f"{old_date} {message.text}", "%Y-%m-%d %H:%M")
                    update_data['start'] = tz.localize(new_dt).isoformat()
                
                if field in ["time", "end_time"] and old_end and 'T' in old_end:
                    old_date = old_end[:10]
                    new_dt = datetime.strptime(f"{old_date} {message.text}", "%Y-%m-%d %H:%M")
                    update_data['end'] = tz.localize(new_dt).isoformat()
            except ValueError:
                await message.answer("❌ Неверный формат времени. Пример: `14:30`")
                return
        
        # ✅ ИСПРАВЛЕНО: было "if update_" → стало "if update_data:"
        if update_data:
            success, msg = await update_event(message.from_user.id, event_id, update_data)
            await message.answer(f"{msg}\n\nНажмите /schedule, чтобы увидеть обновлённое расписание")
        else:
            await message.answer("✅ Значение обновлено")
        
        await state.clear()
    except Exception as e:
        logger.error(f"Save value error: {e}\n{traceback.format_exc()}")
        await message.answer("❌ Ошибка при сохранении")
        await state.clear()

# ================= ОТМЕНА / НАЗАД =================
@dp.callback_query(F.data == "back_to_schedule")
@dp.message(F.text.lower() == "/cancel", StateFilter(EventManage))
async def back_to_schedule(message_or_cb, state: FSMContext):
    await state.clear()
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    try:
        await response.edit_text("✏️ Отменено. Нажмите /schedule, чтобы увидеть расписание")
    except:
        await response.answer("✏️ Отменено. Нажмите /schedule, чтобы увидеть расписание")
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