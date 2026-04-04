from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import os
import urllib.parse
import requests
from db import get_token, save_token

SCOPES = ['https://www.googleapis.com/auth/calendar']
REDIRECT_URI = os.getenv('REDIRECT_URI')
CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
TOKEN_URI = 'https://oauth2.googleapis.com/token'
AUTH_URI = 'https://accounts.google.com/o/oauth2/auth'

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

async def get_credentials(user_id):
    token_data = await get_token(user_id)
    if not token_data:
        return None
    acc, ref = token_data
    creds = Credentials(
        token=acc,
        refresh_token=ref,
        token_uri=TOKEN_URI,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        from datetime import datetime, timezone
        expires_in = max(0, int((creds.expiry - datetime.now(timezone.utc)).total_seconds()))
        await save_token(user_id, creds.token, creds.refresh_token, expires_in)
    return creds