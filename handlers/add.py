"""Добавление слов.

Боту можно прислать что угодно — он сам поймёт, что это:
• пары «слово - перевод»   → добавляет как есть;
• одно слово или выражение → варианты перевода из словаря (handlers/dictionary.py);
• текст, пост, песню       → находит в нём слова и выражения, отмечаешь нужные.

Работает и в режиме «📚 Добавить слова», и просто из главного меню.
"""
import re

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.dictionary import show_extraction, show_lookup
from handlers.study import study_menu
from utils import database, dictionary
from utils.database import Word
from utils.keyboards import BTN_ADD, add_kb, added_kb, empty_kb, main_menu
from utils.states import AddWords
from utils.ui import drop_buttons, show
from utils.words import esc, has_latin, parse_words, short, words_count

router = Router()

PROMPT = (
    '📚 <b>Добавление слов</b>\n\n'
    'Пришли мне:\n'
    '🔤 <b>слово</b> — найду перевод в словаре: <code>ubiquitous</code>\n'
    '📖 <b>текст</b>, пост или песню — найду в нём слова и выражения\n'
    '✍️ <b>пары</b> — <code>apple - яблоко, fly - летать</code>\n\n'
    'Можно несколькими сообщениями подряд. Когда закончишь — жми «Готово».'
)
NOT_UNDERSTOOD = 'Не понял 🤔 Пришли английское слово, текст или выбери действие в меню 👇'
SHORT = 4          # до стольких слов без точек — это «слово», длиннее — «текст»
MAX_SHOWN = 10     # больше не перечисляем, чтобы ответ не превысил лимит Telegram


def _as_one_word(text):
    """Показать как одно слово (экран «Выбери значение») или разобрать по частям?

    Одно слово — всегда как слово. Короткая фраза — как слово, только если она
    есть в словаре целиком («look for»). Иначе — по частям, как текст:
    «waiting for people to» → нашёл «waiting for». «Не нашёл» — только если
    по частям тоже ничего нет.
    """
    if len(text.split()) == 1 or not dictionary.available():
        return True
    return bool(dictionary.meanings(text)) or not dictionary.extract(text)


def _bullets(items):
    lines = [f'• {esc(short(item))}' for item in items[:MAX_SHOWN]]
    if len(items) > MAX_SHOWN:
        lines.append(f'…и ещё {len(items) - MAX_SHOWN}')
    return lines


@router.message(Command('add'))
@router.message(F.text == BTN_ADD)
async def add_from_menu(message: Message, state: FSMContext):
    await state.set_state(AddWords.waiting)
    await message.answer(PROMPT, reply_markup=add_kb)


@router.callback_query(F.data == 'add:start')
async def add_from_button(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddWords.waiting)
    await show(callback, PROMPT, add_kb)
    await callback.answer()


@router.message(StateFilter(None, AddWords.waiting), F.text | F.caption)
async def smart_input(message: Message, state: FSMContext):
    """Определяет, что прислали: пары, слово или текст."""
    text = (message.text or message.caption or '').strip()
    adding = await state.get_state() == AddWords.waiting.state

    if text.startswith('/') or not has_latin(text):
        if adding:
            await message.answer('Жду английское слово, текст или пары «слово - перевод».\n'
                                 'Или нажми «Готово».', reply_markup=add_kb)
        else:
            await message.answer(NOT_UNDERSTOOD, reply_markup=main_menu)
        return

    pairs, bad = parse_words(text)
    if pairs and any(has_latin(word) for word, _ in pairs) and len(pairs) >= len(bad):
        await _add_pairs(message, pairs, bad, adding)
    elif len(text.split()) <= SHORT and not re.search(r'[.!?]', text) and _as_one_word(text):
        await show_lookup(message, text)
    else:
        await show_extraction(message, text)


async def _add_pairs(message: Message, pairs, bad, adding):
    user_id = message.from_user.id
    added, duplicates = database.add_words(user_id, [Word(w, t, '', '') for w, t in pairs])

    lines = []
    if added:
        lines.append(f'✅ Добавил {words_count(len(added))}:')
        lines += _bullets([f'{w.word} — {w.translation}' for w in added])
    if duplicates:
        lines += [''] if lines else []
        lines.append('☑️ Уже были в словаре:')
        lines += _bullets([f'{w.word} — {w.translation}' for w in duplicates])
    if bad:
        lines += [''] if lines else []
        lines.append('⚠️ Не понял — между словом и переводом нужен дефис:')
        lines += _bullets(bad)
        lines.append('Пример: <code>apple - яблоко</code>')

    lines += ['', f'📚 В словаре: {words_count(database.count_words(user_id))}']
    if adding:
        lines.append('Присылай ещё или жми «Готово».')
    await message.answer('\n'.join(lines), reply_markup=add_kb if adding else added_kb)


@router.message(AddWords.waiting)
async def add_not_text(message: Message):
    """Прислали стикер, фото без подписи и т.п. вместо текста."""
    await message.answer('Жду текст: слово, пост или пары «слово - перевод».\n'
                         'Или нажми «Готово».', reply_markup=add_kb)


@router.callback_query(F.data == 'add:done')
async def add_done(callback: CallbackQuery, state: FSMContext):
    # Сбрасываем состояние, только если сейчас идёт добавление —
    # старая кнопка «Готово» не должна прерывать, например, тренировку
    if await state.get_state() == AddWords.waiting.state:
        await state.clear()
    user_id = callback.from_user.id
    await drop_buttons(callback)
    # Сразу показываем «Как будем учить?»: карточки или рассылка по расписанию
    if database.count_words(user_id):
        text, kb = study_menu(user_id, header='👍 <b>Готово!</b> Слова сохранены.\n\n')
    else:
        text, kb = '👍 Готово! Словарь пока пуст.', empty_kb
    await callback.bot.send_message(user_id, text, reply_markup=kb)
    await callback.answer()
