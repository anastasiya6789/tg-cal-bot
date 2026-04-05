# keyboards.py
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать событие", callback_data="create")],
        [InlineKeyboardButton(text="📅 Моё расписание", callback_data="schedule")],
        [InlineKeyboardButton(text="🔗 Подключить Google", callback_data="connect")]
    ])

def create_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Встреча", callback_data="type_meeting"),
         InlineKeyboardButton(text="✅ Задача", callback_data="type_task"),
         InlineKeyboardButton(text="🎯 Мероприятие", callback_data="type_event")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def color_selection_kb(colors: dict, event_type: str):
    kb = [
        [InlineKeyboardButton(text=name, callback_data=f"color_{code}")]
        for name, code in colors.get(event_type, [])
    ]
    kb.append([InlineKeyboardButton(text="⏭ Стандартный", callback_data="color_skip")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def schedule_nav_kb(period: str, date_str: str, offset: int, has_more: bool):
    kb = [
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sched|{period}|{date_str}|{offset}|prev"),
         InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"sched|{period}|{date_str}|{offset}|next")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"sched_edit|{date_str}"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"sched_delete|{date_str}")],
        [InlineKeyboardButton(text="📅 Другая дата", callback_data="sched_custom"),
         InlineKeyboardButton(text="🔄 Обновить", callback_data=f"sched|{period}|{date_str}|0|refresh")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="start")]
    ]
    if has_more:
        kb.append([InlineKeyboardButton(text="📄 Ещё события", callback_data=f"sched|{period}|{date_str}|{offset+8}|more")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def period_select_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📆 День", callback_data="sched_init|day"),
         InlineKeyboardButton(text="🗓 Неделя", callback_data="sched_init|week"),
         InlineKeyboardButton(text="📅 Месяц", callback_data="sched_init|month")]
    ])

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def back_to_schedule_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_schedule")]
    ])

def confirm_kb(confirm_text: str = "✅ Да", cancel_text: str = "❌ Отмена", confirm_data: str = "confirm_create"):
    """Клавиатура подтверждения"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=confirm_text, callback_data=confirm_data),
         InlineKeyboardButton(text=cancel_text, callback_data="cancel")]
    ])

def delete_confirm_kb(idx: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete|{idx}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_schedule")]
    ])

def edit_fields_kb(is_task: bool):
    if is_task:
        fields = [("🔖 Название", "title"), ("📅 Дата", "date"), ("⏰ Время", "time")]
    else:
        fields = [
            ("🔖 Название", "title"), ("📍 Локация", "location"), ("📝 Описание", "description"),
            ("📅 Дата начала", "start_date"), ("⏰ Время начала", "start_time"),
            ("📅 Дата окончания", "end_date"), ("⏰ Время окончания", "end_time")
        ]
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"edit_field|{field}")] for name, field in fields]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_schedule")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)