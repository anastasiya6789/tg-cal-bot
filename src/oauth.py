# oauth.py
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import urllib.parse
import requests
from config import SCOPES, REDIRECT_URI, CLIENT_ID, CLIENT_SECRET, TOKEN_URI, AUTH_URI
from db import get_token, save_token

def get_auth_url(state):
    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'state': state,
        'access_type': 'offline',
        'prompt': 'consent',
    }
    return f"{AUTH_URI}?{urllib.parse.urlencode(params)}"

async def handle_callback(code, state, user_id):
    data = {
        'code': code,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code',
    }
    response = requests.post(TOKEN_URI, data=data)
    response.raise_for_status()
    result = response.json()

    access_token = result.get('access_token')
    refresh_token = result.get('refresh_token')
    expires_in = result.get('expires_in', 3600)

    if not access_token:
        raise ValueError("No access token in response")

    await save_token(user_id, access_token, refresh_token, expires_in)
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )

# oauth.py

# oauth.py

async def get_credentials(user_id):
    """Получает креды пользователя, авто-рефрешит если истёк"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from datetime import datetime, timezone
    from db import get_token, save_token
    from config import TOKEN_URI, CLIENT_ID, CLIENT_SECRET, SCOPES, logger
    
    token_data = await get_token(user_id)
    if not token_data:  # ✅ ИСПРАВЛЕНО: было token_
        return None
    
    acc, ref = token_data
    
    # Создаём Credentials объект
    creds = Credentials(
        token=acc,
        refresh_token=ref,
        token_uri=TOKEN_URI,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )
    
    # ✅ ПРОВЕРКА: если токен истёк или истекает через 5 минут — рефрешим
    if creds.expired or (creds.expiry and (creds.expiry - datetime.now(timezone.utc)).total_seconds() < 300):
        if creds.refresh_token:
            try:
                logger.info(f"🔄 Рефреш токена для user_id={user_id}")
                creds.refresh(Request())
                
                # ✅ Сохраняем НОВЫЙ access_token в БД
                expires_in = max(0, int((creds.expiry - datetime.now(timezone.utc)).total_seconds()))
                await save_token(
                    user_id, 
                    creds.token,           # новый access_token
                    creds.refresh_token,   # refresh_token (может обновиться)
                    expires_in
                )
                logger.info(f"✅ Токен обновлён для user_id={user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка рефреша токена: {e}")
                return None  # Вернём None, чтобы вызывающий код обработал ошибку авторизации
        else:
            logger.warning(f"⚠️ Нет refresh_token для user_id={user_id}")
            return None
    
    return creds