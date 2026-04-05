# config.py
import os
import pytz
import logging

# Bot settings
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_BASE_URL")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")  # укажи в переменных окружения Render

# Timezone
TZ_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
tz = pytz.timezone(TZ_NAME)

# Database
DB_PATH = os.getenv("DB_PATH", "./tokens.db")

# Google OAuth
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/tasks'
]
REDIRECT_URI = os.getenv('REDIRECT_URI')
CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
TOKEN_URI = 'https://oauth2.googleapis.com/token'
AUTH_URI = 'https://accounts.google.com/o/oauth2/auth'

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)