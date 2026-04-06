# handlers/reminders.py
from aiogram import Router, types, F
from aiogram.filters import Command
from config import logger
import aiosqlite
from config import DB_PATH

router = Router()

@router.message(Command("reminders"))
async def cmd_reminders(message: types.Message):
    """Показывает список активных напоминаний"""
    user_id = message.from_user.id
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                'SELECT event_id, remind_minutes, is_sent FROM reminders WHERE user_id = ?', 
                (user_id,)
            ) as cur:
                rows = await cur.fetchall()
        
        if not rows:
            await message.answer("🔔 У тебя нет активных напоминаний.")
            return
            
        text = "🔔 Твои напоминания:\n\n"
        active_count = 0
        
        for event_id, minutes, is_sent in rows:
            if not is_sent:
                text += f"• Событие `...{event_id[-5:]}`: за {minutes} мин\n"
                active_count += 1
        
        if active_count == 0:
            text = "🔔 Все напоминания уже отправлены."
        else:
            text += f"\nВсего активных: {active_count}"
            
        await message.answer(text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Reminders error: {e}")
        await message.answer("❌ Ошибка при загрузке напоминаний.")