import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from config import BOT_TOKEN
from handlers import add, fallback, mailing, start, study, vocabulary
from utils.database import init_db
from utils.scheduler import restore_mailings, scheduler

# Ошибки с полным трейсбеком попадут в journalctl
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

# parse_mode='HTML' — во всех сообщениях работают <b>жирный</b>, <i>курсив</i> и т.д.
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()

# Порядок важен: кнопки меню должны срабатывать раньше, чем «ввод слов»,
# а fallback (ловит всё остальное) — самым последним
dp.include_routers(
    start.router,
    study.router,
    vocabulary.router,
    mailing.router,
    add.router,
    fallback.router,
)

# Команды в синей кнопке «Меню» рядом с полем ввода
COMMANDS = [
    BotCommand(command='start', description='Главное меню'),
    BotCommand(command='add', description='Добавить слова'),
    BotCommand(command='study', description='Учить: карточки или по расписанию'),
    BotCommand(command='vocabulary', description='Мой словарь'),
    BotCommand(command='mailing', description='Настроить рассылку слов'),
    BotCommand(command='help', description='Помощь'),
]


async def main():
    init_db()                     # создаём таблицы если их нет
    scheduler.start()             # запускаем планировщик рассылки
    restore_mailings(bot)         # возвращаем рассылки, включённые до перезапуска
    await bot.set_my_commands(COMMANDS)
    await dp.start_polling(bot)   # запускаем бота


if __name__ == '__main__':
    asyncio.run(main())
