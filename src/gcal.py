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

# Скрытый тег типа в описании (не виден в UI Google)
TYPE_TAG_RE = re.compile(r'<!--\s*TG_TYPE:\s*(meeting|task|event)\s*-->', re.IGNORECASE)
TASK_KEYWORDS = ['дедлайн', 'deadline', 'задача', 'сделать', 'подготовить']

TYPE_EMOJI = {
    'meeting': '📅 Встречи',
    'task': '✅ Задачи',
    'event': '🎯 Мероприятия'
}
TYPE_ORDER = ['meeting', 'task', 'event']

# Авто-описания от Google, которые нужно скрывать (расширенный список)
GOOGLE_AUTO_DESCS = [
    'изменения в названии, описании',
    'изменения в описании',
    'изменения в названии',
    'изменения в местоположении',
    'changes to title, description',
    'changes to description',
    'changes to title',
    'changes to location',
    'конференция: присоединиться через google meet',
    'video call: join with google meet',
]

def to_iso(dt: datetime) -> str:
    """Конвертирует datetime в ISO 8601 с timezone для Google API"""
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    return dt.isoformat()

def parse_google_dt(dt_data: dict) -> datetime:
    """Парсит дату/время из Google Calendar API (dateTime или date)"""
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
            dt = tz.localize(dt.replace(hour=12))  # середина дня для all-day
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
    """Определяет тип события по скрытому тегу или эвристике"""
    desc = (e.get('description') or '').lower()
    m = TYPE_TAG_RE.search(e.get('description') or '')
    if m:
        return m.group(1).lower()
    # Эвристика для событий без тега
    if e.get('attendees'):
        return 'meeting'
    if any(kw in desc for kw in TASK_KEYWORDS):
        return 'task'
    return 'event'

def clean_description(desc):
    """Убирает служебные теги и авто-описания Google"""
    if not desc:
        return ''
    # Удаляем наш тег
    desc = TYPE_TAG_RE.sub('', desc).strip()
    # Фильтруем авто-описания (регистронезависимо)
    desc_lower = desc.lower().strip()
    if desc_lower in GOOGLE_AUTO_DESCS or not desc_lower:
        return ''
    return desc

def format_time_range(start_data, end_data) -> str:
    """Форматирует время как '19:00-20:00' или 'весь день'"""
    start_dt = parse_google_dt(start_data)
    end_dt = parse_google_dt(end_data)
    
    if not start_dt:
        return "весь день"
    
    # Если это all-day событие (нет времени)
    if 'date' in start_data and 'dateTime' not in start_data:
        return "весь день"
    
    start_str = start_dt.strftime("%H:%M")
    if end_dt:
        end_str = end_dt.strftime("%H:%M")
        # Если начало и конец совпадают — показываем только начало
        if start_str == end_str:
            return start_str
        return f"{start_str}-{end_str}"
    return start_str

def format_event(e):
    """Форматирует событие в строку для Telegram"""
    time_range = format_time_range(e.get('start'), e.get('end'))
    title = e.get('summary', 'Без названия')
    loc = f" 📍{e.get('location')}" if e.get('location') else ""
    desc = clean_description(e.get('description', ''))
    desc_short = f" 💬 {desc[:30]}..." if len(desc) > 30 else (f" 💬 {desc}" if desc else "")
    
    # Статус для задач (если нужно показать ✅)
    status = ""
    if e.get('_type') == 'task' and e.get('status') == 'completed':
        status = "✅ "
    
    return f"⏰ {time_range} — {status}{title}{loc}{desc_short}"

async def create_event(user_id, event_data):
    """Создаёт событие в Google Calendar (для всех типов)"""
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google командой /connect"

    service = build('calendar', 'v3', credentials=creds)
    
    # Парсим даты с таймзоной
    start_dt = datetime.fromisoformat(event_data['start'])
    end_dt = datetime.fromisoformat(event_data['end'])
    if start_dt.tzinfo is None: start_dt = tz.localize(start_dt)
    if end_dt.tzinfo is None: end_dt = tz.localize(end_dt)

    # Формируем описание с скрытым тегом типа
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
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'popup', 'minutes': 15},
                {'method': 'email', 'minutes': 60}
            ]
        }
    }
    
    # Для задач добавляем дедлайн в описание (для наглядности)
    if event_data.get('type') == 'task' and event_data.get('deadline'):
        body['description'] += f"\n⏰ Дедлайн: {event_data['deadline']}"

    try:
        event = service.events().insert(calendarId='primary', body=body).execute()
        link = event.get('htmlLink', '')
        return True, f"✅ Создано!\n{link}" if link else "✅ Создано!"
    except Exception as e:
        logger.error(f"Calendar API error: {e}")
        return False, f"❌ Ошибка: {str(e)}"

async def get_schedule(user_id, period="day", target_date=None, offset=0, limit=20):
    """Получает события из Calendar API, группирует по типам и датам"""
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google", False, None

    # Базовая дата с таймзоной
    base_dt = datetime.strptime(target_date, "%Y-%m-%d") if target_date else datetime.now(tz)
    base_dt = tz.localize(base_dt.replace(hour=12, minute=0, second=0))

    # Вычисляем границы периода
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

    service = build('calendar', 'v3', credentials=creds)
    
    try:
        res = service.events().list(
            calendarId='primary',
            timeMin=to_iso(start),
            timeMax=to_iso(end),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
    except Exception as e:
        logger.error(f"API error: {e}")
        return False, f"❌ Ошибка API: {str(e)}", False, None

    all_events = res.get('items', [])
    
    # 1. Определяем тип и добавляем служебные поля для сортировки
    for e in all_events:
        e['_type'] = detect_type(e)
        e['_sort_dt'] = parse_google_dt(e.get('start'))
        e['_date_key'] = e['_sort_dt'].strftime("%Y-%m-%d") if e['_sort_dt'] else None

    # 2. Группируем: Тип -> Дата -> Список событий
    grouped = {}
    for e in all_events:
        if not e.get('_date_key'):
            continue
        grouped.setdefault(e['_type'], {}).setdefault(e['_date_key'], []).append(e)

    # 3. Плоский список для пагинации (строго по порядку типов)
    flat_ordered = []
    for t in TYPE_ORDER:
        if t in grouped:
            for d in sorted(grouped[t].keys()):
                flat_ordered.extend(grouped[t][d])

    paginated = flat_ordered[offset:offset+limit]
    has_more = len(flat_ordered) > offset + limit

    # 4. Формируем красивый текст
    if not paginated:
        text = "📭 Нет событий на этот период."
    else:
        period_label = {"day": "день", "week": "неделю", "month": "месяц"}[period]
        text = f"📋 Расписание на {period_label} ({format_date_range(period, start, end)}):\n\n"
        
        # Перегруппировываем обрезанный список для вывода
        display_grouped = {}
        for e in paginated:
            if not e.get('_date_key'):
                continue
            display_grouped.setdefault(e['_type'], {}).setdefault(e['_date_key'], []).append(e)

        for t in TYPE_ORDER:
            if t in display_grouped:
                text += f"{TYPE_EMOJI[t]}:\n"
                for date_key in sorted(display_grouped[t].keys()):
                    day_dt = datetime.strptime(date_key, "%Y-%m-%d")
                    text += f"🗓 {day_dt.strftime('%d.%m.%Y')}\n"
                    for e in display_grouped[t][date_key]:
                        text += format_event(e) + "\n"
                    text += "\n"  # пустая строка между днями

    return True, text.strip(), has_more, {"start": start, "end": end}