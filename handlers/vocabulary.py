"""Словарь: список слов по страницам и очистка с подтверждением."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from utils import database
from utils.keyboards import BTN_WORDS, confirm_clear_kb, empty_kb, vocabulary_kb
from utils.scheduler import stop_mailing
from utils.ui import show
from utils.words import esc, short, words_count

router = Router()

PAGE_SIZE = 20   # слов на странице: длинный список не влезет в одно сообщение


def _page(user_id, page):
    """Текст и кнопки нужной страницы словаря."""
    words = database.get_words(user_id)
    if not words:
        return '📋 Словарь пока пуст.\nДобавь первые слова 👇', empty_kb

    pages = (len(words) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))          # защита от несуществующей страницы
    start = page * PAGE_SIZE

    lines = [f'📋 <b>Твой словарь</b> — {words_count(len(words))}', '']
    for number, item in enumerate(words[start:start + PAGE_SIZE], start=start + 1):
        ipa = f' {esc(short(item.transcription, 40))}' if item.transcription else ''
        if item.direction == 'ru':          # карточка «с русского» — русское первым
            lines.append(f'{number}. 🇷🇺 <b>{esc(short(item.translation, 40))}</b> — '
                         f'{esc(short(item.word, 40))}{ipa}')
        elif item.translation:
            lines.append(f'{number}. <b>{esc(short(item.word, 40))}</b>{ipa} — {esc(short(item.translation, 40))}')
        else:
            lines.append(f'{number}. {esc(short(item.word, 80))}{ipa}')
    return '\n'.join(lines), vocabulary_kb(page, pages)


@router.message(Command('vocabulary'))
@router.message(F.text == BTN_WORDS)
async def vocabulary_from_menu(message: Message, state: FSMContext):
    await state.clear()
    text, kb = _page(message.from_user.id, 0)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith('voc:page:'))
async def vocabulary_page(callback: CallbackQuery):
    page = int(callback.data.rsplit(':', 1)[1])
    text, kb = _page(callback.from_user.id, page)
    await show(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data == 'voc:clear')
async def ask_clear(callback: CallbackQuery):
    total = database.count_words(callback.from_user.id)
    if total == 0:
        await callback.answer('Словарь и так пуст')
        return
    await show(callback,
               f'🗑 Удалить все {words_count(total)} из словаря?\n\nЭто действие нельзя отменить.',
               confirm_clear_kb)
    await callback.answer()


@router.callback_query(F.data == 'voc:clear:yes')
async def clear(callback: CallbackQuery):
    user_id = callback.from_user.id
    database.delete_words(user_id)
    stop_mailing(user_id)                 # слать больше нечего
    await show(callback, '🗑 Словарь очищен. Рассылка тоже выключена.', empty_kb)
    await callback.answer('Словарь очищен')
