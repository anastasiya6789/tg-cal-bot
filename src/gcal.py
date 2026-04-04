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

async def create_event(user_id, event_data):
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google командой /connect"

    service = build('calendar', 'v3', credentials=creds)

    # Парсим start/end из строк в datetime с таймзоной
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
        return False, "❌ Сначала подключи Google", False

    # Базовая дата с таймзоной
    if target_date:
        base_dt = datetime.strptime(target_date, "%Y-%m-%d")
        base_dt = tz.localize(base_dt.replace(hour=12, minute=0, second=0))  # середина дня
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
        return False, "❌ Неизвестный период", False

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
        return False, f"❌ Ошибка API: {str(e)}", False

    all_events = events_result.get('items', [])
    paginated = all_events[offset:offset+limit]
    has_more = len(all_events) > offset + limit

    if not paginated:
        text = "📭 Нет событий на этот период."
    else:
        period_label = {"day": "день", "week": "неделю", "month": "месяц"}[period]
        date_str = base_dt.strftime("%d.%m.%Y")
        text = f"📋 Расписание на {period_label} ({date_str}):\n\n"
        for e in paginated:
            start_dt = e['start'].get('dateTime', e['start'].get('date'))
            s_time = start_dt[11:16] if len(start_dt) > 16 else "весь день"
            title = e.get('summary', 'Без названия')
            loc = f" 📍{e.get('location', '')}" if e.get('location') else ""
            desc = e.get('description', '')
            desc_short = f" 💬 {desc[:35]}..." if len(desc) > 35 else f" 💬 {desc}" if desc else ""
            text += f"⏰ {s_time} — {title}{loc}{desc_short}\n"

    return True, text, has_more