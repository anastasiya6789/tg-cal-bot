import re
from datetime import datetime, timedelta
import pytz

DAYS_RU = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}

def parse_schedule(text, tz_name="Europe/Moscow"):
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    # Ищем: День Время-Время Название
    pattern = r"([а-яА-ЯёЁ]{2})\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s+(.+)"
    matches = re.finditer(pattern, text)
    events = []
    for m in matches:
        day_str, start_str, end_str, name = m.groups()
        day_idx = DAYS_RU.get(day_str.lower().strip())
        if day_idx is None: continue
        
        start_h, start_m = map(int, start_str.split(":"))
        end_h, end_m = map(int, end_str.split(":"))
        
        days_ahead = day_idx - now.weekday()
        if days_ahead < 0: days_ahead += 7
        if days_ahead == 0 and (now.hour > start_h or (now.hour == start_h and now.minute > start_m)):
            days_ahead = 7
            
        start_dt = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0) + timedelta(days=days_ahead)
        end_dt = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0) + timedelta(days=days_ahead)
        events.append({"summary": name.strip(), "start": start_dt, "end": end_dt})
    return events