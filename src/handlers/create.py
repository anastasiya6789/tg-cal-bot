# handlers/create.py
import logging
from datetime import datetime
from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter  # ✅ ДОБАВИЛ Command и StateFilter
from config import tz, logger
from states import EventCreation
from keyboards import create_type_kb, color_selection_kb, confirm_kb, cancel_kb
from gcal import create_event, create_task
from db import save_event_id

router = Router()

COLORS = {
    "meeting": [("🔵 Синий", "9"), ("🟢 Зелёный", "10"), ("🟣 Фиолетовый", "11")],
    "task": [("🔴 Красный", "4"), ("🟠 Оранжевый", "5"), ("🟡 Жёлтый", "3")],
    "event": [("🌸 Розовый", "1"), ("⚫ Чёрный", "8"), ("⚪ Серый", "7")]
}

@router.message(Command("create"))
@router.callback_query(F.data == "create")
async def start_creation(message_or_cb: types.Message | types.CallbackQuery, state: FSMContext):
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("Выберите тип события:", reply_markup=create_type_kb())
    await state.set_state(EventCreation.choosing_type)
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()

@router.callback_query(F.data.startswith("type_"))
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

@router.message(EventCreation.setting_deadline)
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

@router.message(EventCreation.setting_start)
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

@router.message(EventCreation.setting_end)
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

@router.message(EventCreation.setting_title)
async def set_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("📍 Введите локацию (или `/skip`):")
    await state.set_state(EventCreation.setting_location)

@router.message(F.text.lower() == "/skip")
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

@router.message(EventCreation.setting_location)
async def set_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await message.answer("📄 Введите описание (или `/skip`):")
    await state.set_state(EventCreation.setting_description)

@router.message(EventCreation.setting_description)
async def set_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await show_color_selection(message, state)

async def show_color_selection(message, state: FSMContext):
    data = await state.get_data()
    event_type = data.get("type", "event")
    kb = color_selection_kb(COLORS, event_type)
    await message.answer("🎨 Выберите цвет:", reply_markup=kb)
    await state.set_state(EventCreation.setting_color)

@router.callback_query(F.data.startswith("color_"))
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
    kb = confirm_kb()
    await message.answer(preview, reply_markup=kb, parse_mode="HTML")
    await state.set_state(EventCreation.confirming)

@router.callback_query(F.data == "confirm_create")
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
        if data.get("type") == "task":
            success, msg, event_id = await create_task(callback.from_user.id, event_data)
        else:
            success, msg, event_id = await create_event(callback.from_user.id, event_data)
        
        await callback.message.edit_text(msg)
        if success and event_id:
            await save_event_id(callback.from_user.id, event_id)
    except Exception as e:
        logger.error(f"Create error: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")
    await state.clear()
    await callback.answer()

@router.callback_query(F.data == "cancel")
async def cancel_creation(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Создание отменено. Введите `/create` чтобы начать заново.")
    await callback.answer()