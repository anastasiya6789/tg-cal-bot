# db.py
import aiosqlite
from datetime import datetime, timezone
from config import DB_PATH

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS user_tokens (
            user_id INTEGER PRIMARY KEY,
            access_token TEXT, refresh_token TEXT, expires_at REAL)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS user_events (
            user_id INTEGER,
            gcal_event_id TEXT,
            created_at REAL,
            PRIMARY KEY (user_id, gcal_event_id))''')
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

async def save_event_id(user_id, gcal_event_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''INSERT OR REPLACE INTO user_events VALUES (?, ?, ?)''',
                         (user_id, gcal_event_id, datetime.now(timezone.utc).timestamp()))
        await db.commit()

async def get_event_ids(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT gcal_event_id FROM user_events WHERE user_id = ?', (user_id,)) as cur:
            return [row[0] for row in await cur.fetchall()]

async def delete_event_id(user_id, gcal_event_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM user_events WHERE user_id = ? AND gcal_event_id = ?', (user_id, gcal_event_id))
        await db.commit()