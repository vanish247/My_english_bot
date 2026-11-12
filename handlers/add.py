"""Добавление слов.

Боту можно прислать что угодно — он сам поймёт, что это:
• пары «слово - перевод»   → добавляет как есть;
• одно слово или выражение → варианты перевода из словаря (handlers/dictionary.py);
• текст, пост, песню       → находит в нём слова и выражения, отмечаешь нужные;
• русское слово или текст  → предлагает карточки «с русского на английский»;
• таблицу или файл         → .txt, .docx (таблица «слово | транскрипция | перевод»
                             сразу становится парами), .pdf с текстом.

Работает и в режиме «📚 Добавить слова», и просто из главного меню.
"""
import asyncio
import logging
import unicodedata

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.dictionary import AI_PROMPT_PASTED, offer_russian, send_ai_prompt, show_ai_cards, show_for_text
from handlers.song import find_and_show
from handlers.study import study_menu
from utils import ai_prompt, database, dictionary, files, lyrics
from utils.database import Word
from utils.keyboards import BTN_ADD, add_kb, add_prompt_kb, added_kb, empty_kb, main_menu
from utils.states import AddWords
from utils.ui import drop_buttons, show
from utils.words import esc, has_latin, is_russian, parse_words, short, words_count

log = logging.getLogger(__name__)

router = Router()

PROMPT = (
    '📚 <b>Добавление слов</b>\n\n'
    'Пришли мне:\n'
    '🔤 <b>слово</b> — найду перевод в словаре: <code>ubiquitous</code>\n'
    '📖 <b>текст</b>, пост или песню — найду в нём слова и выражения\n'
    '✍️ <b>пары</b> — <code>apple - яблоко, fly - летать</code>\n'
    '🇷🇺 <b>по-русски</b> — сделаю карточки с русского на английский\n'
    '📄 <b>файл</b> — .txt, .docx, .pdf; таблицу «слово | транскрипция | перевод» добавлю сразу\n'
    '🎵 <b>песню</b> — mp3 или название, найду её текст\n'
    '🤖 или <b>через ИИ</b> — соберу промпт для ChatGPT, Claude, DeepSeek, Алисы, '
    'а из ответа сделаю карточки\n\n'
    'Можно несколькими сообщениями подряд. Когда закончишь — жми «Готово».'
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
@router.message(F.text == BTN_ADD)
async def add_from_menu(message: Message, state: FSMContext):
    await state.set_state(AddWords.waiting)
    await message.answer(PROMPT, reply_markup=add_prompt_kb)


@router.callback_query(F.data == 'add:start')
async def add_from_button(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddWords.waiting)
    await show(callback, PROMPT, add_prompt_kb)
    await callback.answer()


@router.message(StateFilter(None, AddWords.waiting), F.text | F.caption, ~F.document)
async def smart_input(message: Message, state: FSMContext):
    """Определяет, что прислали: ответ ИИ, пары, слово или текст."""
    text = (message.text or message.caption or '').strip()
    adding = await state.get_state() == AddWords.waiting.state
    user_id = message.from_user.id
    if user_id in _pending:                       # продолжение длинного ответа ИИ
        _pending[user_id].append(text)
        return
    if len(text) >= GLUE_FROM and '|' in text:
        _pending[user_id] = [text]
        await asyncio.sleep(GLUE_DELAY)
        text = ai_prompt.glue(_pending.pop(user_id))
    await handle_text(message, text, adding)


async def handle_text(message: Message, text, adding, from_file=False):
    """Разбирает текст из сообщения или файла."""
    if text.startswith('/') or not text:
        await _not_understood(message, adding)
        return
    if ai_prompt.is_prompt(text):                 # по ошибке прислали сам промпт, а не ответ ИИ
        await message.answer(AI_PROMPT_PASTED)
        return
    rows = files.table_rows(text)                 # таблица из Word / Excel
    table = files.parse_table(rows) if rows else []
    if table:
        await _add_table(message, table, adding)
        return
    cards = ai_prompt.parse_answer(text)          # строки «фраза | перевод | …» — ответ ИИ
    if cards:
        await show_ai_cards(message, cards)
        return
    pairs, bad = parse_words(text)
    if pairs and any(has_latin(word) for word, _ in pairs) and len(pairs) >= len(bad):
        await _add_pairs(message, pairs, bad, adding)
    elif _old_menu_button(text):                  # «📖 Учить» из старого меню — даём новое
        await message.answer('Меню обновилось 👇', reply_markup=main_menu)
    elif is_russian(text):                        # по-русски → карточки с русского?
        await offer_russian(message, text)
    elif not has_latin(text):
        await _not_understood(message, adding)
    else:
        if from_file and len(text) > dictionary.MAX_TEXT:
            await message.answer(
                f'📄 Текст длинный ({len(text)} символов). Словарём разберу начало, а целиком — '
                'кнопкой «🤖 Разобрать точнее через ИИ»: пришлю промпт файлом.')
        await show_for_text(message, text)


def _old_menu_button(text):
    """Кнопка старого меню: значок и пара слов («📖 Учить»)."""
    return len(text.split()) <= 3 and unicodedata.category(text[0]) == 'So'


async def _not_understood(message: Message, adding):
    if adding:
        await message.answer('Жду слово, текст, файл или пары «слово - перевод».\n'
                             'Или нажми «Готово».', reply_markup=add_kb)
    else:
        await message.answer(NOT_UNDERSTOOD, reply_markup=main_menu)


async def _add_table(message: Message, table, adding):
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
    await _add_pairs(message, words, missing, adding, missing_note='⚠️ Не нашёл перевод — пришли парой «слово - перевод»:')


async def _add_pairs(message: Message, pairs, bad, adding, missing_note=None):
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
        lines.append('☑️ Уже были в словаре:')
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

    lines += ['', f'📚 В словаре: {words_count(database.count_words(user_id))}']
    if adding:
        lines.append('Присылай ещё или жми «Готово».')
    await message.answer('\n'.join(lines), reply_markup=add_kb if adding else added_kb)


@router.message(StateFilter(None, AddWords.waiting, AddWords.ai, AddWords.song), F.document)
async def document_input(message: Message, state: FSMContext, bot: Bot):
    """Прислали файл: .txt, .docx, .pdf, .srt — или песню mp3 файлом."""
    document = message.document
    current = await state.get_state()
    if lyrics.is_audio_name(document.file_name) or (document.mime_type or '').startswith('audio/'):
        if current in (AddWords.ai.state, AddWords.song.state):
            data = await state.get_data()
            previous = data.get('song_prev') or data.get('ai_prev')
            await (state.set_state(AddWords.waiting) if previous == AddWords.waiting.state else state.clear())
        await find_and_show(message, query=lyrics.from_filename(document.file_name))
        return
    if current == AddWords.song.state:
        await message.answer('🎵 Жду название песни или mp3. Текст песни можно вставить сообщением.')
        return
    adding = current == AddWords.waiting.state
    if not files.kind(document.file_name):
        await message.answer('📄 Пока понимаю файлы .txt, .docx, .pdf и .srt.',
                             reply_markup=add_kb if adding else None)
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
        data = await state.get_data()
        if data.get('ai_prev') == AddWords.waiting.state:
            await state.set_state(AddWords.waiting)
        else:
            await state.clear()
        text = content.text or '\n'.join(f'{w} — {t}' for w, t, _ in content.pairs)
        if text.strip():
            await send_ai_prompt(bot, message.from_user.id, text)
        else:
            await message.answer('📄 В файле не нашёл текста.')
        return

    if content.pairs:
        await _add_table(message, content.pairs, adding)
    if content.text.strip():
        await handle_text(message, content.text.strip(), adding, from_file=True)
    elif not content.pairs:
        await message.answer('📄 В файле не нашёл текста.')


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
