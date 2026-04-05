# handlers/schedule.py
import logging
from datetime import datetime, timedelta
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter  # ✅ ДОБАВИЛ
from config import tz, logger
from states import ScheduleFSM
from keyboards import period_select_kb, schedule_nav_kb, back_to_schedule_kb
from gcal import get_schedule

router = Router()

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

        nav_kb = schedule_nav_kb(period, date_str, offset, has_more)

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
        logger.error(f"Ошибка в show_schedule_view: {e}")
        err_text = "❌ Ошибка загрузки расписания"
        if isinstance(message_or_cb, types.Message):
            await message_or_cb.answer(err_text)
        else:
            await message_or_cb.message.edit_text(err_text)
            await message_or_cb.answer()

@router.message(Command("schedule"))
@router.callback_query(F.data == "schedule")
async def cmd_schedule(message_or_cb: types.Message | types.CallbackQuery):
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("Выберите период:", reply_markup=period_select_kb())

@router.callback_query(F.data.startswith("sched_init|"))
async def init_schedule(callback: types.CallbackQuery):
    period = callback.data.split("|")[1]
    today = datetime.now(tz).strftime("%Y-%m-%d")
    await show_schedule_view(callback, callback.from_user.id, period, today, 0)
    await callback.answer()

@router.callback_query(F.data.startswith("sched|"))
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
        logger.error(f"Ошибка в navigate_schedule: {e}")
        await callback.answer("❌ Ошибка навигации", show_alert=True)

@router.callback_query(F.data == "sched_custom")
async def ask_custom_date(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ScheduleFSM.waiting_custom_date)
    kb = back_to_schedule_kb()
    await callback.message.edit_text("📅 Введите дату: `ДД.ММ.ГГГГ` (пример: `15.04.2026`):", reply_markup=kb)
    await callback.answer()

@router.message(ScheduleFSM.waiting_custom_date, F.text.regexp(r"^\d{2}\.\d{2}\.\d{4}$"))
async def handle_custom_date(message: types.Message, state: FSMContext):
    try:
        dt = datetime.strptime(message.text, "%d.%m.%Y")
        dt = tz.localize(dt.replace(hour=12, minute=0, second=0))
        await state.clear()
        await show_schedule_view(message, message.from_user.id, "day", dt.strftime("%Y-%m-%d"), 0)
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: `15.04.2026`")

@router.message(ScheduleFSM.waiting_custom_date)
async def invalid_custom_date(message: types.Message, state: FSMContext):
    if message.text.lower() not in ["/skip", "отмена", "назад"]:
        await message.answer("❌ Неверный формат. Введите `15.04.2026` или нажмите /skip")

@router.callback_query(F.data == "schedule", StateFilter(ScheduleFSM.waiting_custom_date))
@router.message(F.text.lower() == "/skip", StateFilter(ScheduleFSM.waiting_custom_date))
async def cancel_custom_date(message_or_cb: types.Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("❌ Отменено. Выберите период:")
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()