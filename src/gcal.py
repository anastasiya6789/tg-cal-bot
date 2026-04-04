from googleapiclient.discovery import build
from oauth import get_credentials
from datetime import datetime, timedelta
import pytz
import os
import re

TZ_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
tz = pytz.timezone(TZ_NAME)

# Скрытый тег типа в описании (не виден в UI Google)
TYPE_TAG_RE = re.compile(r'<!--\s*TG_TYPE:\s*(meeting|task|event)\s*-->')
TASK_KEYWORDS = ['дедлайн', 'deadline', 'задача', 'task', 'сделать', 'подготовить']

TYPE_EMOJI = {
    'meeting': '📅 Встречи',
    'task': '✅ Задачи',
    'event': '🎯 Мероприятия'
}
TYPE_ORDER = ['meeting', 'task', 'event']

def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    return dt.isoformat()

def format_date_range(period, start, end):
    s, e = start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y")
    return s if period == "day" else f"{s}–{e}"

def detect_type(e):
    """Определяет тип события: тег → эвристика → fallback"""
    desc = e.get('description', '')
    m = TYPE_TAG_RE.search(desc)
    if m:
        return m.group(1)
    
    # Эвристика для событий, созданных вручную в GCal
    if e.get('attendees'):
        return 'meeting'
    if any(kw in desc.lower() for kw in TASK_KEYWORDS):
        return 'task'
    return 'event'

def clean_description(desc):
    """Убирает служебные теги из описания для чистого вывода"""
    return TYPE_TAG_RE.sub('', desc).strip()

def format_event(e):
    start_dt = e['start'].get('dateTime', e['start'].get('date'))
    s_time = start_dt[11:16] if len(start_dt) > 16 else "весь день"
    title = e.get('summary', 'Без названия')
    loc = f" 📍{e['location']}" if e.get('location') else ""
    desc = clean_description(e.get('description', ''))
    desc_short = f" 💬 {desc[:30]}..." if len(desc) > 30 else (f" 💬 {desc}" if desc else "")
    return f"⏰ {s_time} — {title}{loc}{desc_short}"

async def create_event(user_id, event_data):
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google командой /connect"

    service = build('calendar', 'v3', credentials=creds)
    start_dt = datetime.fromisoformat(event_data['start'])
    end_dt = datetime.fromisoformat(event_data['end'])
    if start_dt.tzinfo is None: start_dt = tz.localize(start_dt)
    if end_dt.tzinfo is None: end_dt = tz.localize(end_dt)

    # Добавляем скрытый тег типа в конец описания
    base_desc = event_data.get('description', '')
    type_tag = f"<!-- TG_TYPE:{event_data['type']} -->"
    full_desc = f"{base_desc}\n{type_tag}" if base_desc else type_tag

    body = {
        'summary': event_data['title'],
        'description': full_desc,
        'location': event_data.get('location', ''),
        'start': {'dateTime': to_iso(start_dt), 'timeZone': TZ_NAME},
        'end': {'dateTime': to_iso(end_dt), 'timeZone': TZ_NAME},
        'colorId': event_data.get('color'),
        'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 15}, {'method': 'email', 'minutes': 60}]}
    }
    if event_data.get('type') == 'task' and event_data.get('deadline'):
        body['description'] = body['description'].replace(type_tag, '') + f"\n⏰ Дедлайн: {event_data['deadline']}\n{type_tag}"

    try:
        event = service.events().insert(calendarId='primary', body=body).execute()
        return True, f"✅ Создано!\n{event.get('htmlLink', 'Событие создано')}"
    except Exception as e:
        return False, f"❌ Ошибка Google Calendar: {str(e)}"

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

    service = build('calendar', 'v3', credentials=creds)
    try:
        res = service.events().list(
            calendarId='primary', timeMin=to_iso(start), timeMax=to_iso(end),
            singleEvents=True, orderBy='startTime'
        ).execute()
    except Exception as e:
        return False, f"❌ Ошибка API: {str(e)}", False, None

    all_events = res.get('items', [])
    
    # Определяем тип для каждого события
    for e in all_events:
        e['_type'] = detect_type(e)

    # Группируем: Тип -> Дата -> Список
    grouped = {}
    for e in all_events:
        date_key = e['start'].get('dateTime', e['start'].get('date'))[:10]
        grouped.setdefault(e['_type'], {}).setdefault(date_key, []).append(e)

    # Плоский список для пагинации (строго по порядку типов)
    flat_ordered = []
    for t in TYPE_ORDER:
        if t in grouped:
            for d in sorted(grouped[t].keys()):
                flat_ordered.extend(grouped[t][d])

    paginated = flat_ordered[offset:offset+limit]
    has_more = len(flat_ordered) > offset + limit

    # Формируем текст
    if not paginated:
        text = "📭 Нет событий на этот период."
    else:
        period_label = {"day": "день", "week": "неделю", "month": "месяц"}[period]
        text = f"📋 Расписание на {period_label} ({format_date_range(period, start, end)}):\n\n"
        
        # Перегруппировываем обрезанный список для вывода
        display_grouped = {}
        for e in paginated:
            date_key = e['start'].get('dateTime', e['start'].get('date'))[:10]
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