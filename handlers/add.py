"""Добавление слов.

Боту можно прислать что угодно — в любой момент, без отдельного режима, — он сам поймёт:
• пары «слово - перевод»   → добавляет как есть;
• одно слово или выражение → варианты перевода из словаря (handlers/dictionary.py);
• текст, пост, песню       → находит в нём слова и выражения, отмечаешь нужные;
• русское слово или текст  → предлагает карточки «с русского на английский»;
• таблицу или файл         → .txt, .docx (таблица «слово | транскрипция | перевод»
                             сразу становится парами), .pdf с текстом;
• mp3                      → ищет текст песни (handlers/song.py).

Кнопка «➕ Добавить» только подсказывает, что можно прислать.
"""
import asyncio
import logging
import unicodedata

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.dictionary import AI_PROMPT_PASTED, offer_russian, send_ai_prompt, show_ai_cards, show_for_text
from handlers.song import find_and_show, leave_waiting
from handlers.study import study_menu
from utils import ai_prompt, database, dictionary, files, lyrics
from utils.database import Word
from utils.keyboards import BTN_ADD, OLD_BTN_ADD, added_kb, empty_kb, main_menu
from utils.states import AddWords
from utils.ui import drop_buttons, show
from utils.words import esc, has_latin, is_russian, parse_words, short, words_count

log = logging.getLogger(__name__)

router = Router()

HOW_TO_ADD = (
    '➕ <b>Как добавить слова</b>\n\n'
    'Пришли мне что угодно:\n'
    '• слово или текст на английском\n'
    '• по-русски — сделаю карточки с русского\n'
    '• файл .txt / .docx / .pdf или mp3\n'
    '• пары: <code>apple - яблоко</code>\n\n'
    'Ещё в меню: <b>🤖 Через ИИ</b> и <b>🎵 Песня</b>'
)
NOT_UNDERSTOOD = 'Не понял 🤔 Пришли английское слово, текст или выбери действие в меню 👇'
MAX_SHOWN = 10     # больше не перечисляем, чтобы ответ не превысил лимит Telegram

# Длинный ответ ИИ Telegram режет на куски по 4096 символов, и они приходят подряд.
# Длинное сообщение с «|» ждём GLUE_DELAY секунд: всё, что придёт следом, — его продолжение.
GLUE_FROM = 3000
GLUE_DELAY = 2.0
_pending = {}      # user_id → куски ответа ИИ, которые ещё собираем


def _bullets(items):
    lines = [f'• {esc(short(item))}' for item in items[:MAX_SHOWN]]
    if len(items) > MAX_SHOWN:
        lines.append(f'…и ещё {len(items) - MAX_SHOWN}')
    return lines


@router.message(Command('add'))
@router.message(F.text.in_({BTN_ADD, OLD_BTN_ADD}))
async def add_from_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HOW_TO_ADD, reply_markup=main_menu)


@router.callback_query(F.data == 'add:start')
async def add_from_button(callback: CallbackQuery):
    await show(callback, HOW_TO_ADD)
    await callback.answer()


@router.message(StateFilter(None), F.text | F.caption, ~F.document)
async def smart_input(message: Message):
    """Определяет, что прислали: ответ ИИ, пары, слово или текст."""
    text = (message.text or message.caption or '').strip()
    user_id = message.from_user.id
    if user_id in _pending:                       # продолжение длинного ответа ИИ
        _pending[user_id].append(text)
        return
    if len(text) >= GLUE_FROM and '|' in text:
        _pending[user_id] = [text]
        await asyncio.sleep(GLUE_DELAY)
        text = ai_prompt.glue(_pending.pop(user_id))
    await handle_text(message, text)


async def handle_text(message: Message, text, from_file=False):
    """Разбирает текст из сообщения или файла."""
    if text.startswith('/') or not text:
        await message.answer(NOT_UNDERSTOOD, reply_markup=main_menu)
        return
    if ai_prompt.is_prompt(text):                 # по ошибке прислали сам промпт, а не ответ ИИ
        await message.answer(AI_PROMPT_PASTED)
        return
    rows = files.table_rows(text)                 # таблица из Word / Excel
    table = files.parse_table(rows) if rows else []
    if table:
        await _add_table(message, table)
        return
    cards = ai_prompt.parse_answer(text)          # строки «фраза | перевод | …» — ответ ИИ
    if cards:
        await show_ai_cards(message, cards)
        return
    pairs, bad = parse_words(text)
    if pairs and any(has_latin(word) for word, _ in pairs) and len(pairs) >= len(bad):
        await _add_pairs(message, pairs, bad)
    elif _old_menu_button(text):                  # «📖 Учить» из старого меню — даём новое
        await message.answer('Меню обновилось 👇', reply_markup=main_menu)
    elif is_russian(text):                        # по-русски → карточки с русского?
        await offer_russian(message, text)
    elif not has_latin(text):
        await message.answer(NOT_UNDERSTOOD, reply_markup=main_menu)
    else:
        if from_file and len(text) > dictionary.MAX_TEXT:
            await message.answer(
                f'📄 Текст длинный ({len(text)} символов). Словарём разберу начало, а целиком — '
                'кнопкой «🤖 Разобрать точнее через ИИ»: пришлю промпт файлом.')
        await show_for_text(message, text)


def _old_menu_button(text):
    """Кнопка старого меню: значок и пара слов («📖 Учить»)."""
    return len(text.split()) <= 3 and unicodedata.category(text[0]) == 'So'


async def _add_table(message: Message, table):
    """Таблица «слово | транскрипция | перевод»: пустой перевод берём из словаря."""
    words, missing = [], []
    for word, translation, ipa in table:
        if not translation:
            found = dictionary.meanings(word, limit=1)
            translation = found[0].translation if found else ''
        if translation:
            words.append(Word(word, translation, ipa, ''))
        else:
            missing.append(word)
    await _add_pairs(message, words, missing, missing_note='⚠️ Не нашёл перевод — пришли парой «слово - перевод»:')


async def _add_pairs(message: Message, pairs, bad, missing_note=None):
    """pairs — [(слово, перевод)] или готовые Word."""
    user_id = message.from_user.id
    words = [item if isinstance(item, Word) else Word(item[0], item[1], '', '') for item in pairs]
    added, duplicates = database.add_words(user_id, words)

    lines = []
    if added:
        lines.append(f'✅ Добавил {words_count(len(added))}:')
        lines += _bullets([f'{w.word} — {w.translation}' for w in added])
    if duplicates:
        lines += [''] if lines else []
        lines.append('☑️ Уже были в «Моих словах»:')
        lines += _bullets([f'{w.word} — {w.translation}' for w in duplicates])
    if bad and missing_note:
        lines += [''] if lines else []
        lines.append(missing_note)
        lines += _bullets(bad)
    elif bad:
        lines += [''] if lines else []
        lines.append('⚠️ Не понял — между словом и переводом нужен дефис:')
        lines += _bullets(bad)
        lines.append('Пример: <code>apple - яблоко</code>')

    lines += ['', f'📋 Мои слова: {database.count_words(user_id)}']
    await message.answer('\n'.join(lines), reply_markup=added_kb)


@router.message(StateFilter(None, AddWords.ai, AddWords.song), F.document)
async def document_input(message: Message, state: FSMContext, bot: Bot):
    """Прислали файл: .txt, .docx, .pdf, .srt — или песню mp3 файлом."""
    document = message.document
    current = await state.get_state()
    if lyrics.is_audio_name(document.file_name) or (document.mime_type or '').startswith('audio/'):
        await leave_waiting(state)
        await find_and_show(message, query=lyrics.from_filename(document.file_name))
        return
    if current == AddWords.song.state:
        await message.answer('🎵 Жду название песни или mp3. Текст песни можно вставить сообщением.')
        return
    if not files.kind(document.file_name):
        await message.answer('📄 Пока понимаю файлы .txt, .docx, .pdf и .srt.')
        return
    if document.file_size and document.file_size > files.MAX_FILE:
        await message.answer('📄 Файл больше 5 МБ — пришли его частями.')
        return
    try:
        buffer = await bot.download(document)
        content = files.read(buffer.read(), document.file_name)
    except files.FileError as error:
        await message.answer(f'📄 {error}')
        return
    except Exception:
        log.exception('Не прочитался файл %s', document.file_name)
        await message.answer('📄 Не получилось прочитать файл. Пришли текст сообщением.')
        return

    if current == AddWords.ai.state:              # нажали «🤖 Через ИИ» и прислали файл
        await state.clear()
        text = content.text or '\n'.join(f'{w} — {t}' for w, t, _ in content.pairs)
        if text.strip():
            await send_ai_prompt(bot, message.from_user.id, text)
        else:
            await message.answer('📄 В файле не нашёл текста.')
        return

    if content.pairs:
        await _add_table(message, content.pairs)
    if content.text.strip():
        await handle_text(message, content.text.strip(), from_file=True)
    elif not content.pairs:
        await message.answer('📄 В файле не нашёл текста.')


@router.callback_query(F.data == 'add:done')
async def add_done(callback: CallbackQuery, state: FSMContext):
    """«✅ Готово» из прошлой версии (режима добавления больше нет) — ведём в «Учить»."""
    if await state.get_state() == AddWords.waiting.state:
        await state.clear()
    user_id = callback.from_user.id
    await drop_buttons(callback)
    if database.count_words(user_id):
        text, kb = study_menu(user_id)
    else:
        text, kb = HOW_TO_ADD, empty_kb
    await callback.bot.send_message(user_id, text, reply_markup=kb)
    await callback.answer()
