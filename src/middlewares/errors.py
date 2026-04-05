# middlewares/errors.py
import logging
import traceback
from aiogram import types
from aiogram.dispatcher.middlewares.base import BaseMiddleware

logger = logging.getLogger(__name__)

class ErrorsMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except Exception as exception:
            logger.error(f"❌ Ошибка: {exception}\n{traceback.format_exc()}")
            return True