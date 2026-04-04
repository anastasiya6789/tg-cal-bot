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

TYPE_TAG_RE = re.compile(r'<!--\s*TG_TYPE:\s*(meeting|event)\s*-->', re.IGNORECASE)
TASK_KEYWORDS = ['дедлайн', 'deadline', 'задача', 'сделать', 'подготовить', 'лаба', 'реферат', 'отчет', 'дз']

TYPE_EMOJI = {
    'meeting': '📅 Встречи',
    'task': '✅ Задачи',
    'event': '🎯 Мероприятия'
}
TYPE_ORDER = ['meeting', 'task', 'event']

GOOGLE_AUTO_DESCS = [
    'изменения в названии, описании', 'изменения в описании', 'изменения в названии',
    'изменения в местоположении', 'changes to title, description', 'changes to description',
    'changes to title', 'changes to location', 'конференция: присоединиться через google meet',
    'video call: join with google meet', ''
]

def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    return dt.isoformat()

def parse_google_dt(dt_data: dict) -> datetime:
    """Парсит дату/время из Google API"""
    if not dt_data:
        return None
    dt_str = dt_data.get('dateTime') or dt_data.get('date')
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
    except Exception as e:
        logger.warning(f"Failed to parse date {dt_str}: {e}")
        return None

def format_date_range(period, start, end):
    s, e = start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y")
    return s if period == "day" else f"{s}–{e}"

def detect_type(e):
    """Определяет тип события"""
    if e.get('_is_native_task'):
        return 'task'
    desc = (e.get('description') or '').lower()
    title = (e.get('summary') or '').lower()
    m = TYPE_TAG_RE.search(e.get('description') or '')
    if m:
        return m.group(1).lower()
    if e.get('attendees'):
        return 'meeting'
    if any(kw in f"{title} {desc}" for kw in TASK_KEYWORDS):
        return 'task'
    return 'event'

def clean_description(desc):
    """Убирает служебные теги и авто-описания"""
    if not desc:
        return ''
    desc = TYPE_TAG_RE.sub('', desc).strip()
    if desc.lower().strip() in GOOGLE_AUTO_DESCS or not desc:
        return ''
    return desc

def format_time_range(start_data, end_data, display_time=None) -> str:
    """Форматирует время для отображения"""
    if display_time:
        return display_time
    start_dt = parse_google_dt(start_data)
    end_dt = parse_google_dt(end_data)
    if not start_dt:
        return "весь день"
    if 'date' in start_data and 'dateTime' not in start_data:
        return "весь день"
    start_str = start_dt.strftime("%H:%M")
    if end_dt:
        end_str = end_dt.strftime("%H:%M")
        if start_str == end_str:
            return start_str
        return f"{start_str}-{end_str}"
    return start_str

def format_event(e):
    """Форматирует событие в строку"""
    time_range = format_time_range(e.get('start'), e.get('end'), e.get('_display_time'))
    title = e.get('summary', 'Без названия')
    loc = f" 📍{e.get('location')}" if e.get('location') else ""
    desc = clean_description(e.get('description', ''))
    desc_short = f" 💬 {desc[:30]}..." if len(desc) > 30 else (f" 💬 {desc}" if desc else "")
    return f"⏰ {time_range} — {title}{loc}{desc_short}"

async def create_event(user_id, event_data):
    """Создаёт событие в нужном API"""
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google командой /connect"

    # Задачи -> Tasks API
    if event_data.get('type') == 'task':
        tasks_service = build('tasks', 'v1', credentials=creds)
        due_dt = datetime.fromisoformat(event_data['start'])
        if due_dt.tzinfo is None:
            due_dt = tz.localize(due_dt)
        due_utc = due_dt.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        
        task_body = {
            'title': event_data['title'],
            'notes': event_data.get('description') or '',
            'due': due_utc,
            'status': 'needsAction'
        }
        try:
            tasks_service.tasks().insert(tasklist='@default', body=task_body).execute()
            return True, "✅ Задача создана!"
        except Exception as e:
            logger.error(f"Tasks API error: {e}")
            return False, f"❌ Ошибка: {str(e)}"

    # Встречи/мероприятия -> Calendar API
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
        link = event.get('htmlLink', '')
        return True, f"✅ Создано!\n{link}" if link else "✅ Создано!"
    except Exception as e:
        logger.error(f"Calendar API error: {e}")
        return False, f"❌ Ошибка: {str(e)}"

async def get_schedule(user_id, period="day", target_date=None, offset=0, limit=20):
    """Получает события из Calendar + задачи из Tasks"""
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
        for e in res.get('items', []):
            sort_dt = parse_google_dt(e.get('start'))
            all_items.append({
                'summary': e.get('summary', 'Без названия'),
                'description': e.get('description', ''),
                'location': e.get('location', ''),
                'start': e.get('start', {}),
                'end': e.get('end', {}),
                '_is_native_task': False,
                '_display_time': None,
                '_sort_dt': sort_dt,
                '_date_key': sort_dt.strftime("%Y-%m-%d") if sort_dt else None
            })
    except Exception as e:
        logger.error(f"Calendar API error: {e}")

    # 2. Tasks API
    try:
        tasks_service = build('tasks', 'v1', credentials=creds)
        task_min = (start - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
        task_max = (end + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59.000Z")
        
        tasks_res = tasks_service.tasks().list(
            tasklist='@default', dueMin=task_min, dueMax=task_max,
            showCompleted=False, showHidden=False
        ).execute()
        
        for t in tasks_res.get('items', []):
            due_str = t.get('due')
            if not due_str:
                continue
            
            # Конвертация времени из UTC в локальный часовой пояс
            if 'T' in due_str:
                dt_utc = datetime.fromisoformat(due_str.replace('Z', '+00:00'))
                dt_local = dt_utc.astimezone(tz)
                display_time = dt_local.strftime("%H:%M")
                sort_dt = dt_local
            else:
                dt_local = tz.localize(datetime.strptime(due_str, "%Y-%m-%d").replace(hour=23, minute=59))
                display_time = "до конца дня"
                sort_dt = dt_local
            
            if sort_dt < start or sort_dt > end:
                continue

            all_items.append({
                'summary': t['title'],
                'description': t.get('notes', ''),
                'location': '',
                'start': {'dateTime': due_str},
                'end': {'dateTime': due_str},
                '_is_native_task': True,
                '_display_time': display_time,
                '_sort_dt': sort_dt,
                '_date_key': sort_dt.strftime("%Y-%m-%d")
            })
    except Exception as e:
        logger.error(f"Tasks API error: {e}")

    # Сортируем по времени
    all_items.sort(key=lambda x: x.get('_sort_dt') or datetime.max.replace(tzinfo=tz))

    # Определяем типы
    for e in all_items:
        e['_type'] = detect_type(e)

    # Группируем
    grouped = {}
    for e in all_items:
        dk = e.get('_date_key')
        if not dk: 
            continue
        t_type = e.get('_type', 'event')
        grouped.setdefault(t_type, {}).setdefault(dk, []).append(e)

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
            dk = e.get('_date_key')
            if not dk: 
                continue
            t_type = e.get('_type', 'event')
            display_grouped.setdefault(t_type, {}).setdefault(dk, []).append(e)

        for t in TYPE_ORDER:
            if t in display_grouped:
                text += f"{TYPE_EMOJI[t]}:\n"
                for dk in sorted(display_grouped[t].keys()):
                    day_dt = datetime.strptime(dk, "%Y-%m-%d")
                    text += f"🗓 {day_dt.strftime('%d.%m.%Y')}\n"
                    for e in display_grouped[t][dk]:
                        text += format_event(e) + "\n"
                    text += "\n"

    return True, text.strip(), has_more, {"start": start, "end": end}