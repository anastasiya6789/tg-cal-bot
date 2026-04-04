from googleapiclient.discovery import build
from oauth import get_credentials

async def create_events(user_id, events, tz_name):
    creds = await get_credentials(user_id)
    if not creds:
        return False, "❌ Сначала подключи Google командой /connect"
    
    service = build('calendar', 'v3', credentials=creds)
    created = 0
    for ev in events:
        body = {
            'summary': ev['summary'],
            'start': {'dateTime': ev['start'].isoformat(), 'timeZone': tz_name},
            'end': {'dateTime': ev['end'].isoformat(), 'timeZone': tz_name},
        }
        try:
            service.events().insert(calendarId='primary', body=body).execute()
            created += 1
        except Exception as e:
            print(f"Error: {e}")
    return True, f"✅ Успешно добавлено {created} событий!"