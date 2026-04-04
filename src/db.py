import aiosqlite
from datetime import datetime, timezone

DB_PATH = "./tokens.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS user_tokens (
            user_id INTEGER PRIMARY KEY,
            access_token TEXT, refresh_token TEXT, expires_at REAL)''')
        await db.commit()

async def save_token(user_id, access_token, refresh_token, expires_in):
    expires_at = datetime.now(timezone.utc).timestamp() + expires_in
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''INSERT OR REPLACE INTO user_tokens VALUES (?, ?, ?, ?)''',
                         (user_id, access_token, refresh_token, expires_at))
        await db.commit()

async def get_token(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT access_token, refresh_token, expires_at FROM user_tokens WHERE user_id = ?', (user_id,)) as cur:
            row = await cur.fetchone()
            if not row: return None
            acc, ref, exp = row
            if datetime.now(timezone.utc).timestamp() > exp - 300:
                return None
            return acc, ref