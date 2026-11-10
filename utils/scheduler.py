"""Рассылка слов по расписанию (APScheduler).

Интервал рассылки хранится в базе (users.mailing_minutes), поэтому
после перезапуска бота рассылки восстанавливаются в restore_mailings().
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from utils import database
from utils.keyboards import mailing_word_kb
from utils.words import esc

log = logging.getLogger(__name__)

# Один планировщик на всё приложение
scheduler = AsyncIOScheduler()

# Тихие часы: ночью слова не присылаем (по московскому времени)
TIMEZONE = ZoneInfo('Europe/Moscow')
QUIET_FROM, QUIET_TO = 22, 9


def _now():
    return datetime.now(TIMEZONE)


def is_quiet_time():
    """С 22:00 до 9:00 — тихие часы."""
    hour = _now().hour
    return hour >= QUIET_FROM or hour < QUIET_TO

# Какое слово рассылки следующее — у каждого пользователя своя позиция.
# Отдельно от тренировки, чтобы рассылка не сбивала карточки.
_positions = {}


def _job_id(user_id):
    return f'mailing_{user_id}'


async def send_next_word(bot: Bot, user_id: int):
    """Отправляет следующее слово — вызывается планировщиком по расписанию"""
    if is_quiet_time():
        return                      # ночью молчим; утром рассылка продолжится с того же слова
    words = database.get_words(user_id)
    if not words:
        stop_mailing(user_id)
        await _send(bot, user_id, '🔕 Словарь пуст — рассылку выключил.')
        return

    # % защищает от выхода за границы, если слов стало меньше
    position = _positions.get(user_id, 0) % len(words)
    item = words[position]
    english = esc(item.word) + (f'  {esc(item.transcription)}' if item.transcription else '')
    if item.direction == 'ru':          # карточка «с русского»: под спойлером английское
        text = (f'🔔 <b>Как это по-английски?</b>\n\n🇷🇺 <b>{esc(item.translation)}</b>'
                f'\n🇬🇧 <tg-spoiler>{english}</tg-spoiler>')
    else:
        text = f'🔔 <b>Повторим слово</b>\n\n🇬🇧 <b>{esc(item.word)}</b>'
        if item.transcription:
            text += f'  {esc(item.transcription)}'
        if item.translation:
            text += f'\n🇷🇺 <tg-spoiler>{esc(item.translation)}</tg-spoiler>'
    if item.example:
        text += f'\n\n💬 <i>{esc(item.example)}</i>'
        if item.example_ru:
            text += f'\n<tg-spoiler>{esc(item.example_ru)}</tg-spoiler>'

    if await _send(bot, user_id, text, mailing_word_kb):
        _positions[user_id] = position + 1


async def _send(bot: Bot, user_id: int, text: str, reply_markup=None) -> bool:
    try:
        await bot.send_message(user_id, text, reply_markup=reply_markup)
        return True
    except TelegramForbiddenError:
        # Пользователь заблокировал бота — слать дальше бессмысленно
        log.info('Пользователь %s заблокировал бота, рассылка выключена', user_id)
        stop_mailing(user_id)
        return False


def _schedule(bot: Bot, user_id: int, minutes: int):
    scheduler.add_job(
        send_next_word,
        trigger='interval',
        minutes=minutes,
        args=[bot, user_id],
        id=_job_id(user_id),
        replace_existing=True,        # если уже была — заменить на новую
    )


def start_mailing(bot: Bot, user_id: int, minutes: int):
    """Включает рассылку и запоминает интервал в базе"""
    database.set_mailing(user_id, minutes)
    _schedule(bot, user_id, minutes)


def stop_mailing(user_id: int):
    """Выключает рассылку"""
    database.set_mailing(user_id, 0)
    if scheduler.get_job(_job_id(user_id)):
        scheduler.remove_job(_job_id(user_id))


def restore_mailings(bot: Bot):
    """Восстанавливает рассылки из базы после запуска бота"""
    mailings = database.get_mailings()
    for user_id, minutes in mailings:
        _schedule(bot, user_id, minutes)
    log.info('Восстановлено рассылок: %d', len(mailings))
