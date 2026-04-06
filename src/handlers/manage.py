# handlers/manage.py
import logging
from datetime import datetime
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter
from config import tz, logger
from states import EventManage
from keyboards import back_to_schedule_kb, delete_confirm_kb, edit_fields_kb
from gcal import delete_event, delete_task, update_event, update_task, detect_type, _fetch_manageable_events, clean_description
from db import delete_event_id

router = Router()

@router.callback_query(F.data.startswith("sched_edit|"))
@router.callback_query(F.data.startswith("sched_delete|"))
async def start_manage(callback: types.CallbackQuery, state: FSMContext):
    try:
        action = "edit" if "sched_edit" in callback.data else "delete"
        date_str = callback.data.split("|")[1]
        await state.update_data(manage_action=action, manage_date=date_str)
        
        events = await _fetch_manageable_events(callback.from_user.id, date_str, period="day")
        if not events:
            await callback.answer("📭 Нет событий для управления на эту дату", show_alert=True)
            return
        
        event_map = {str(i): ev for i, ev in enumerate(events[:10])}
        await state.update_data(event_map=event_map)
        
        buttons = []
        for idx, ev in event_map.items():
            start = ev.get('start', {})
            end = ev.get('end', {})
            is_tasks = ev.get('_is_tasks_api', False)
            
            if is_tasks:
                # ✅ Задачи из Tasks API — без времени
                time_str = ""
                prefix = "📌 "
            else:
                # ✅ События из Calendar API — с временем
                start_dt_str = start.get('dateTime') or start.get('date')
                if not start_dt_str or 'T' not in start_dt_str:
                    time_str = "весь день"
                else:
                    try:
                        t_start = start_dt_str.split('T')[1][:5]
                        end_dt_str = end.get('dateTime') or end.get('date')
                        if end_dt_str and 'T' in end_dt_str:
                            t_end = end_dt_str.split('T')[1][:5]
                            time_str = t_start if t_start == t_end else f"{t_start}-{t_end}"
                        else:
                            time_str = t_start
                    except:
                        time_str = "весь день"
                prefix = ""

            title = ev['summary'][:40]  # Чуть длиннее, чтобы влезало с описанием
            
            # ✅ ДОБАВЛЯЕМ ОПИСАНИЕ В КНОПКУ
            desc = clean_description(ev.get('description', ''))
            if desc:
                desc_short = desc[:50] + "…" if len(desc) > 50 else desc
                title_with_desc = f"{title}\n📝 {desc_short}"
            else:
                title_with_desc = title
            
            cb_data = f"select_{action}|{idx}"
            
            if time_str:
                btn_text = f"{int(idx)+1}. {time_str} — {title_with_desc}"
            else:
                btn_text = f"{int(idx)+1}. {prefix}{title_with_desc}"
                
            buttons.append([types.InlineKeyboardButton(text=btn_text, callback_data=cb_data)])
        
        buttons.append([types.InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_schedule")])
        kb = types.InlineKeyboardMarkup(inline_keyboard=buttons)
        
        action_text = "✏️ Выберите событие для редактирования:" if action == "edit" else "🗑 Выберите событие для удаления:"
        await callback.message.edit_text(f"{action_text}\n\n(Показаны события на {date_str})", reply_markup=kb)
        await callback.answer()
    except Exception as e:
        logger.error(f"Manage start error: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)

@router.callback_query(F.data.startswith("select_edit|"))
@router.callback_query(F.data.startswith("select_delete|"))
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
            kb = delete_confirm_kb(idx)
            await callback.message.edit_text(f"⚠️ Удалить событие?\n📌 {event['summary']}\nЭто действие нельзя отменить.", reply_markup=kb)
        else:
            event_type = detect_type(event['_raw']) if not event.get('_is_tasks_api') else 'task'
            await state.update_data(selected_event_type=event_type)
            kb = edit_fields_kb(event_type == "task" or event.get('_is_tasks_api'))
            await callback.message.edit_text("✏️ Что изменить?", reply_markup=kb)
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Select error: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)

@router.callback_query(F.data.startswith("confirm_delete|"))
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
            success, msg = await delete_task(callback.from_user.id, event_id)
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

@router.callback_query(F.data.startswith("edit_field|"))
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

@router.message(EventManage.entering_value)
async def save_new_value(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        event_id = data.get('selected_event_id')
        event_raw = data.get('selected_event_raw')
        field = data.get('edit_field')
        is_tasks_api = data.get('selected_is_tasks_api', False)
        
        if not event_id or not field or not event_raw:
            await message.answer("❌ Ошибка: данные не найдены")
            await state.clear()
            return
        
        if is_tasks_api:
            update_data = {}
            
            if field == "title":
                update_data['title'] = message.text
            elif field == "description":
                update_data['description'] = message.text
            elif field in ["date", "time"]:
                try:
                    if field == "date":
                        if '.' in message.text and len(message.text.split('.')[2]) == 4:
                            new_date = datetime.strptime(message.text, "%d.%m.%Y")
                        else:
                            new_date = datetime.strptime(message.text, "%d.%m")
                            new_date = new_date.replace(year=datetime.now(tz).year)
                        new_dt = tz.localize(new_date.replace(hour=12))
                    else:
                        new_time = datetime.strptime(message.text, "%H:%M")
                        due = event_raw.get('due', '')
                        if due and 'T' in due:
                            old_date = due[:10]
                            new_dt = datetime.strptime(f"{old_date} {message.text}", "%Y-%m-%d %H:%M")
                        else:
                            new_dt = datetime.now(tz).replace(hour=new_time.hour, minute=new_time.minute)
                        if new_dt.tzinfo is None: new_dt = tz.localize(new_dt)
                    update_data['due'] = new_dt.isoformat()
                except ValueError as ve:
                    await message.answer(f"❌ Неверный формат: {ve}")
                    return
            
            if update_data:
                success, msg = await update_task(message.from_user.id, event_id, update_data)
                await message.answer(f"{msg}\n\nНажмите /schedule, чтобы увидеть обновлённое расписание")
            else:
                await message.answer("✅ Значение обновлено")
            await state.clear()
            return
        
        # Calendar API
        update_data = {}
        from gcal import TYPE_TAG_RE, to_iso
        
        if field == "title":
            update_data['title'] = message.text
        elif field == "location":
            update_data['location'] = message.text
        elif field == "description":
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
                    update_data['start'] = to_iso(tz.localize(new_dt))
                if field in ["date", "end_date"] and old_end and 'T' in old_end:
                    old_time = old_end[11:16]
                    new_dt = datetime.strptime(f"{new_date.strftime('%Y-%m-%d')} {old_time}", "%Y-%m-%d %H:%M")
                    update_data['end'] = to_iso(tz.localize(new_dt))
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
                    update_data['start'] = to_iso(tz.localize(new_dt))
                if field in ["time", "end_time"] and old_end and 'T' in old_end:
                    old_date = old_end[:10]
                    new_dt = datetime.strptime(f"{old_date} {message.text}", "%Y-%m-%d %H:%M")
                    update_data['end'] = to_iso(tz.localize(new_dt))
            except ValueError:
                await message.answer("❌ Неверный формат времени. Пример: `14:30`")
                return
        
        if update_data:
            success, msg = await update_event(message.from_user.id, event_id, update_data)
            await message.answer(f"{msg}\n\nНажмите /schedule, чтобы увидеть обновлённое расписание")
        else:
            await message.answer("✅ Значение обновлено")
        await state.clear()
    except Exception as e:
        logger.error(f"Save value error: {e}")
        await message.answer("❌ Ошибка при сохранении")
        await state.clear()

@router.callback_query(F.data == "back_to_schedule")
@router.message(F.text.lower() == "/cancel", StateFilter(EventManage))
async def back_to_schedule(message_or_cb, state: FSMContext):
    await state.clear()
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    try:
        await response.edit_text("✏️ Отменено. Нажмите /schedule, чтобы увидеть расписание")
    except:
        await response.answer("✏️ Отменено. Нажмите /schedule, чтобы увидеть расписание")
    if hasattr(message_or_cb, 'answer'): await message_or_cb.answer()