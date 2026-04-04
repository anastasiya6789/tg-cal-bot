from googleapiclient.discovery import build
from oauth import get_credentials
from datetime import datetime, timedelta
import pytz
import os

TZ_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
tz = pytz.timezone(TZ_NAME)

def to_iso(dt: datetime) -> str:
    """Конвертирует datetime в ISO 8601 с timezone для Google API"""
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    return dt.isoformat()

def format_date_range(period: str, start: datetime, end: datetime) -> str:
    """Форматирует диапазон дат для заголовка"""
    s = start.strftime("%d.%m.%Y")
    e = end.strftime("%d.%m.%Y")
    if period == "day":
        return s
    return f"{s}–{e}"

def group_events_by_day(events: list) -> dict:
    """Группирует события по датам для красивого вывода"""
    grouped = {}
    for e in events:
        start_dt = e['start'].get('dateTime', e['start'].get('date'))
        date_key = start_dt[:10]  # YYYY-MM-DD
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(e)
    return grouped

def format_event(e: dict) -> str:
    """Форматирует одно событие в строку"""
    start_dt = e['start'].get('dateTime', e['start'].get('date'))
    s_time = start_dt[11:16] if len(start_dt) > 16 else "весь день"
    title = e.get('summary', 'Без названия')
    loc = f" 📍{e.get('location', '')}" if e.get('location') else ""
    desc = e.get('description', '')
    desc_short = f" 💬 {desc[:30]}..." if len(desc) > 30 else f" 💬 {desc}" if desc else ""
    return f"⏰ {s_time} — {title}{loc}{desc_short}"

async def create_event(user_id, event_data):
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google командой /connect"

    service = build('calendar', 'v3', credentials=creds)

    start_dt = datetime.fromisoformat(event_data['start'])
    end_dt = datetime.fromisoformat(event_data['end'])
    if start_dt.tzinfo is None:
        start_dt = tz.localize(start_dt)
    if end_dt.tzinfo is None:
        end_dt = tz.localize(end_dt)

    body = {
        'summary': event_data['title'],
        'description': event_data.get('description', ''),
        'location': event_data.get('location', ''),
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

    if event_data.get('type') == 'task' and event_data.get('deadline'):
        body['description'] += f"\n\n⏰ Дедлайн: {event_data['deadline']}"

    try:
        event = service.events().insert(calendarId='primary', body=body).execute()
        link = event.get('htmlLink', 'Событие создано')
        return True, f"✅ Создано!\n{link}"
    except Exception as e:
        return False, f"❌ Ошибка Google Calendar: {str(e)}"

async def get_schedule(user_id, period="day", target_date=None, offset=0, limit=8):
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google", False, None

    # Базовая дата с таймзоной
    if target_date:
        base_dt = datetime.strptime(target_date, "%Y-%m-%d")
        base_dt = tz.localize(base_dt.replace(hour=12, minute=0, second=0))
    else:
        base_dt = datetime.now(tz)

    # Вычисляем границы периода
    if period == "day":
        start = base_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end = base_dt.replace(hour=23, minute=59, second=59, microsecond=0)
    elif period == "week":
        start = base_dt - timedelta(days=base_dt.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=6, hours=23, minutes=59, seconds=59, microseconds=0)
    elif period == "month":
        start = base_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if base_dt.month == 12:
            end = start.replace(year=base_dt.year+1, month=1, day=1, microsecond=0) - timedelta(seconds=1)
        else:
            end = start.replace(month=base_dt.month+1, day=1, microsecond=0) - timedelta(seconds=1)
    else:
        return False, "❌ Неизвестный период", False, None

    service = build('calendar', 'v3', credentials=creds)
    
    try:
        events_result = service.events().list(
            calendarId='primary',
            timeMin=to_iso(start),
            timeMax=to_iso(end),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
    except Exception as e:
        return False, f"❌ Ошибка API: {str(e)}", False, None

    all_events = events_result.get('items', [])
    paginated = all_events[offset:offset+limit]
    has_more = len(all_events) > offset + limit

    # Формируем красивый вывод с группировкой по дням
    if not paginated:
        text = "📭 Нет событий на этот период."
    else:
        period_label = {"day": "день", "week": "неделю", "month": "месяц"}[period]
        date_range = format_date_range(period, start, end)
        text = f"📋 Расписание на {period_label} ({date_range}):\n\n"
        
        # Группируем по дням и форматируем
        grouped = group_events_by_day(paginated)
        for date_key in sorted(grouped.keys()):
            day_dt = datetime.strptime(date_key, "%Y-%m-%d")
            day_header = day_dt.strftime("🗓 %d.%m.%Y")
            text += f"{day_header}\n"
            for e in grouped[date_key]:
                text += format_event(e) + "\n"
            text += "\n"  # пустая строка между днями

    # Возвращаем 4 значения, как и обещали
    period_data = {"start": start, "end": end}
    return True, text.strip(), has_more, period_data