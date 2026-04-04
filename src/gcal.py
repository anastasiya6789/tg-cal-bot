from googleapiclient.discovery import build
from oauth import get_credentials
from datetime import datetime, timedelta
import pytz
import os
import re
import logging

logger = logging.getLogger(__name__)

TZ_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
tz = pytz.timezone(TZ_NAME)

TYPE_TAG_RE = re.compile(r'<!--\s*TG_TYPE:\s*(meeting|event)\s*-->')
TASK_KEYWORDS = ['дедлайн', 'deadline', 'задача', 'task', 'сделать', 'подготовить']

TYPE_EMOJI = {
    'meeting': '📅 Встречи',
    'task': '✅ Задачи',
    'event': '🎯 Мероприятия'
}
TYPE_ORDER = ['meeting', 'task', 'event']

# Авто-описания от Google, которые нужно скрывать
GOOGLE_AUTO_DESCS = [
    'Изменения в названии, описании',
    'Изменения в описании',
    'Изменения в названии',
    'Changes to title, description',
    'Changes to description',
    'Changes to title',
    ''
]

def to_iso(dt: datetime) -> str:
    """Для Calendar API: datetime с timezone"""
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    return dt.isoformat()

def to_tasks_due(dt: datetime) -> str:
    """Для Tasks API: формат дедлайна (UTC с временем или только дата)"""
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    # Конвертируем в UTC и форматируем как требует Tasks API
    utc_dt = dt.astimezone(pytz.UTC)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

def parse_calendar_dt(dt_str: str) -> datetime:
    """Парсит дату/время из Calendar API"""
    if not dt_str:
        return None
    try:
        if 'T' in dt_str:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            dt = tz.localize(dt.replace(hour=12))
        if dt.tzinfo is None:
            dt = tz.localize(dt)
        return dt
    except:
        return None

def parse_tasks_due(due_str: str) -> datetime:
    """Парсит дедлайн из Tasks API"""
    if not due_str:
        return None
    try:
        if 'T' in due_str:
            dt = datetime.fromisoformat(due_str.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(due_str, "%Y-%m-%d")
            dt = tz.localize(dt.replace(hour=23, minute=59))
        if dt.tzinfo is None:
            dt = tz.localize(dt)
        return dt
    except:
        return None

def format_date_range(period, start, end):
    s, e = start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y")
    return s if period == "day" else f"{s}–{e}"

def detect_type(e):
    if e.get('_is_native_task'):
        return 'task'
    desc = e.get('description', '') or ''
    m = TYPE_TAG_RE.search(desc)
    if m:
        return m.group(1)
    if e.get('attendees'):
        return 'meeting'
    if any(kw in desc.lower() for kw in TASK_KEYWORDS):
        return 'task'
    return 'event'

def clean_description(desc):
    if not desc:
        return ''
    desc = TYPE_TAG_RE.sub('', desc).strip()
    # Фильтруем авто-описания Google
    if desc in GOOGLE_AUTO_DESCS:
        return ''
    return desc

def format_time_for_display(dt_str: str, is_task: bool = False) -> str:
    if not dt_str:
        return "весь день"
    if is_task:
        # Для задач: если время 00:00 — показываем "до конца дня"
        if 'T' in dt_str:
            time_part = dt_str[11:16]
            if time_part == '00:00':
                return "до конца дня"
            return time_part
        return "до конца дня"
    else:
        if 'T' in dt_str:
            return dt_str[11:16]
        return "весь день"

def format_event(e):
    start_data = e.get('start', {})
    start_dt_str = start_data.get('dateTime') or start_data.get('date')
    is_task = e.get('_is_native_task', False)
    s_time = format_time_for_display(start_dt_str, is_task)
    title = e.get('summary', 'Без названия')
    loc = f" 📍{e.get('location')}" if e.get('location') else ""
    desc = clean_description(e.get('description', ''))
    desc_short = f" 💬 {desc[:30]}..." if len(desc) > 30 else (f" 💬 {desc}" if desc else "")
    status = "✅ " if is_task and e.get('status') == 'completed' else ""
    return f"⏰ {s_time} — {status}{title}{loc}{desc_short}"

async def create_calendar_event(user_id, event_data):
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google командой /connect"

    service = build('calendar', 'v3', credentials=creds)
    start_dt = datetime.fromisoformat(event_data['start'])
    end_dt = datetime.fromisoformat(event_data['end'])
    if start_dt.tzinfo is None: start_dt = tz.localize(start_dt)
    if end_dt.tzinfo is None: end_dt = tz.localize(end_dt)

    base_desc = (event_data.get('description') or '').strip()
    type_tag = f"<!-- TG_TYPE:{event_data['type']} -->"
    full_desc = f"{base_desc}\n{type_tag}" if base_desc else type_tag

    body = {
        'summary': event_data['title'],
        'description': full_desc,
        'location': event_data.get('location') or '',
        'start': {'dateTime': to_iso(start_dt), 'timeZone': TZ_NAME},
        'end': {'dateTime': to_iso(end_dt), 'timeZone': TZ_NAME},
        'colorId': event_data.get('color'),
        'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 15}, {'method': 'email', 'minutes': 60}]}
    }

    try:
        event = service.events().insert(calendarId='primary', body=body).execute()
        return True, f"✅ Создано в календаре!\n{event.get('htmlLink', '')}"
    except Exception as e:
        logger.error(f"Calendar API error: {e}")
        return False, f"❌ Ошибка Calendar API: {str(e)}"

async def create_task(user_id, event_data):
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google командой /connect"

    tasks_service = build('tasks', 'v1', credentials=creds)
    due_dt = datetime.fromisoformat(event_data['start'])
    if due_dt.tzinfo is None:
        due_dt = tz.localize(due_dt)
    
    notes = (event_data.get('description') or '').strip()
    if event_data.get('deadline'):
        notes = f"{notes}\n⏰ Дедлайн: {event_data['deadline']}" if notes else f"⏰ Дедлайн: {event_data['deadline']}"
    
    task_body = {
        'title': event_data['title'],
        'notes': notes,
        'due': to_tasks_due(due_dt),
        'status': 'needsAction'
    }
    
    try:
        task = tasks_service.tasks().insert(tasklist='@default', body=task_body).execute()
        return True, f"✅ Задача создана!"
    except Exception as e:
        logger.error(f"Tasks API error: {e}")
        return False, f"❌ Ошибка Tasks API: {str(e)}"

async def create_event(user_id, event_data):
    if event_data.get('type') == 'task':
        return await create_task(user_id, event_data)
    else:
        return await create_calendar_event(user_id, event_data)

async def get_schedule(user_id, period="day", target_date=None, offset=0, limit=20):
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google", False, None

    base_dt = datetime.strptime(target_date, "%Y-%m-%d") if target_date else datetime.now(tz)
    base_dt = tz.localize(base_dt.replace(hour=12, minute=0, second=0))

    if period == "day":
        start = base_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end = base_dt.replace(hour=23, minute=59, second=59, microsecond=0)
    elif period == "week":
        start = (base_dt - timedelta(days=base_dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=6, hours=23, minutes=59, seconds=59, microseconds=0)
    elif period == "month":
        start = base_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_month = 1 if base_dt.month == 12 else base_dt.month + 1
        next_year = base_dt.year + 1 if base_dt.month == 12 else base_dt.year
        end = start.replace(month=next_month, day=1, year=next_year) - timedelta(seconds=1)
    else:
        return False, "❌ Неизвестный период", False, None

    all_items = []

    # 1. Calendar Events
    try:
        cal_service = build('calendar', 'v3', credentials=creds)
        res = cal_service.events().list(
            calendarId='primary', timeMin=to_iso(start), timeMax=to_iso(end),
            singleEvents=True, orderBy='startTime'
        ).execute()
        events = res.get('items', [])
        for e in events:
            sort_dt = parse_calendar_dt(e.get('start', {}).get('dateTime') or e.get('start', {}).get('date'))
            all_items.append({
                'summary': e.get('summary', 'Без названия'),
                'description': e.get('description', ''),
                'location': e.get('location', ''),
                'start': e.get('start', {}),
                '_is_native_task': False,
                'status': None,
                '_sort_dt': sort_dt,
                '_date_key': sort_dt.strftime("%Y-%m-%d") if sort_dt else None
            })
    except Exception as e:
        logger.error(f"Calendar API error: {e}")

    # 2. Tasks API — с расширенным диапазоном для надёжности
    try:
        tasks_service = build('tasks', 'v1', credentials=creds)
        # Расширяем диапазон на 1 день в каждую сторону
        task_start = (start - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
        task_end = (end + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59.000Z")
        
        tasks_res = tasks_service.tasks().list(
            tasklist='@default', dueMin=task_start, dueMax=task_end,
            showCompleted=False, showHidden=False
        ).execute()
        tasks = tasks_res.get('items', [])
        
        for t in tasks:
            due = t.get('due')
            if not due:
                continue
            due_dt = parse_tasks_due(due)
            # Фильтруем: задача должна попадать в запрошенный период
            if not due_dt or due_dt < start or due_dt > end:
                continue
            all_items.append({
                'summary': t['title'],
                'description': t.get('notes', ''),
                'location': '',
                'start': {'dateTime': due},
                '_is_native_task': True,
                'status': t.get('status'),
                '_sort_dt': due_dt,
                '_date_key': due_dt.strftime("%Y-%m-%d")
            })
    except Exception as e:
        logger.error(f"Tasks API error: {e}")

    # Сортируем по времени
    all_items.sort(key=lambda x: x.get('_sort_dt') or datetime.max.replace(tzinfo=tz))

    # Определяем типы
    for e in all_items:
        e['_type'] = detect_type(e)

    # Группируем: Тип -> Дата -> Список
    grouped = {}
    for e in all_items:
        date_key = e.get('_date_key')
        if not date_key:
            continue
        grouped.setdefault(e['_type'], {}).setdefault(date_key, []).append(e)

    # Плоский список для пагинации
    flat_ordered = []
    for t in TYPE_ORDER:
        if t in grouped:
            for d in sorted(grouped[t].keys()):
                flat_ordered.extend(grouped[t][d])

    paginated = flat_ordered[offset:offset+limit]
    has_more = len(flat_ordered) > offset + limit

    # Формируем текст
    if not paginated:
        text = "📭 Нет событий и задач на этот период."
    else:
        period_label = {"day": "день", "week": "неделю", "month": "месяц"}[period]
        text = f"📋 Расписание на {period_label} ({format_date_range(period, start, end)}):\n\n"
        
        display_grouped = {}
        for e in paginated:
            date_key = e.get('_date_key')
            if not date_key:
                continue
            display_grouped.setdefault(e['_type'], {}).setdefault(date_key, []).append(e)

        for t in TYPE_ORDER:
            if t in display_grouped:
                text += f"{TYPE_EMOJI[t]}:\n"
                for date_key in sorted(display_grouped[t].keys()):
                    day_dt = datetime.strptime(date_key, "%Y-%m-%d")
                    text += f"🗓 {day_dt.strftime('%d.%m.%Y')}\n"
                    for e in display_grouped[t][date_key]:
                        text += format_event(e) + "\n"
                    text += "\n"

    return True, text.strip(), has_more, {"start": start, "end": end}