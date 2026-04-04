from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import os
from db import get_token, save_token

SCOPES = ['https://www.googleapis.com/auth/calendar']
REDIRECT_URI = os.getenv('REDIRECT_URI')
CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')

CLIENT_CONFIG = {
    "web": {
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI],
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
    }
}

def get_auth_url(state):
    # use_pkce=False для веб-приложений с редиректом
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        prompt='consent',
        state=state,
        include_granted_scopes='true'
    )
    return auth_url

async def handle_callback(code, state, user_id):
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI
    # Важно: use_pkce=False, чтобы не требовался code_verifier
    flow.fetch_token(code=code, use_pkce=False)
    creds = flow.credentials
    
    # Вычисляем expires_in вручную
    from datetime import datetime, timezone
    expires_in = 3600
    if creds.expiry:
        expires_in = max(0, int((creds.expiry - datetime.now(timezone.utc)).total_seconds()))
    
    await save_token(user_id, creds.token, creds.refresh_token, expires_in)
    return creds

async def get_credentials(user_id):
    token_data = await get_token(user_id)
    if not token_data:
        return None
    acc, ref = token_data
    creds = Credentials(
        token=acc, refresh_token=ref,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
        scopes=SCOPES
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        from datetime import datetime, timezone
        expires_in = max(0, int((creds.expiry - datetime.now(timezone.utc)).total_seconds()))
        await save_token(user_id, creds.token, creds.refresh_token, expires_in)
    return creds