# oauth.py
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import urllib.parse
import requests
from config import SCOPES, REDIRECT_URI, CLIENT_ID, CLIENT_SECRET, TOKEN_URI, AUTH_URI
from db import get_token, save_token

# oauth.py — get_auth_url
def get_auth_url(state):
    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'state': state,
        'access_type': 'offline',  # ✅ Обязательно
        'prompt': 'consent',        # ✅ Гарантирует получение refresh_token
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

# oauth.py
async def get_credentials(user_id):
    """Получает креды, авто-рефрешит если нужно"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from datetime import datetime, timezone
    from db import get_token, save_token
    from config import TOKEN_URI, CLIENT_ID, CLIENT_SECRET, SCOPES, logger
    
    token_data = await get_token(user_id)
    if not token_data:
        logger.warning(f"⚠️ Нет токенов в БД для user_id={user_id}")
        return None
    
    acc, ref = token_data
    
    # Создаём Credentials
    creds = Credentials(
        token=acc,
        refresh_token=ref,
        token_uri=TOKEN_URI,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )
    
    # ✅ Проверяем: если токен истёк или истечёт через 5 минут — рефрешим
    now = datetime.now(timezone.utc)
    if creds.expired or (creds.expiry and (creds.expiry - now).total_seconds() < 300):
        if creds.refresh_token:
            try:
                logger.info(f"🔄 Рефреш токена для user_id={user_id}")
                creds.refresh(Request())
                
                # ✅ Сохраняем новые токены
                if creds.token and creds.expiry:
                    expires_in = max(0, int((creds.expiry - now).total_seconds()))
                    await save_token(
                        user_id,
                        creds.token,
                        creds.refresh_token,  # Может обновиться
                        expires_in
                    )
                    logger.info(f"✅ Токен обновлён для user_id={user_id}")
                else:
                    logger.error(f"❌ После рефреша нет token/expiry для user_id={user_id}")
                    return None
            except Exception as e:
                logger.error(f"❌ Ошибка рефреша: {e}")
                return None
        else:
            logger.warning(f"⚠️ Нет refresh_token для user_id={user_id} — нужно переподключить")
            return None
    
    return creds