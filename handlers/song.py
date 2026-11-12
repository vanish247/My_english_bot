"""Текст песни: прислал mp3 или написал название → бот находит текст → карточки.

• mp3 (как аудио или как файл) — исполнитель и название берутся из свойств файла
  или из имени файла;
• «🎵 Текст песни» в «Добавить слова» или /song — пишешь название сам.

Сам звук не распознаём (на сервере это не потянуть): текст берём из базы LRCLIB
(utils/lyrics.py). Дальше всё как с обычным текстом: «Нашёл в тексте» или «Через ИИ».
"""
from collections import OrderedDict

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.dictionary import send_ai_prompt, show_extraction
from utils import dictionary, lyrics
from utils.keyboards import MENU_BUTTONS, inline
from utils.states import AddWords, Study
from utils.ui import drop_buttons, show
from utils.words import esc, is_russian, plural, short

router = Router()

SONG_HELP = ('🎵 <b>Текст песни</b>\n\n'
             'Напиши исполнителя и название:\n'
             '<code>David Bowie The Man Who Sold the World</code>\n\n'
             'Или пришли песню файлом mp3 — возьму название из файла.')
STALE = 'Этот экран устарел. Пришли песню или название ещё раз.'
MAX_SCREENS = 300

_screens = OrderedDict()        # (user_id, message_id) → {'songs': [...], 'index': 0}


def _remember(user_id, message_id, data):
    _screens[(user_id, message_id)] = data
    _screens.move_to_end((user_id, message_id))
    while len(_screens) > MAX_SCREENS:
        _screens.popitem(last=False)


def _screen(callback):
    if callback.message is None:
        return None
    return _screens.get((callback.from_user.id, callback.message.message_id))


# ─── Как попасть: кнопка, команда, mp3 ─────────────────────

async def _ask_name(bot, user_id, state):
    previous = await state.get_state()
    if previous == Study.card.state:
        previous = None
    await state.set_state(AddWords.song)
    await state.update_data(song_prev=previous)
    await bot.send_message(user_id, SONG_HELP)


@router.callback_query(F.data == 'sg:start')
async def song_button(callback: CallbackQuery, state: FSMContext):
    await _ask_name(callback.bot, callback.from_user.id, state)
    await callback.answer()


@router.message(Command('song'))
async def song_command(message: Message, state: FSMContext):
    await _ask_name(message.bot, message.from_user.id, state)


async def _restore(state):
    """Выходим из «жду название песни» туда, где были (в режим добавления или в меню)."""
    if await state.get_state() in (AddWords.song.state, AddWords.ai.state):
        data = await state.get_data()
        previous = data.get('song_prev') or data.get('ai_prev')
        if previous == AddWords.waiting.state:
            await state.set_state(AddWords.waiting)
        else:
            await state.clear()


@router.message(AddWords.song, F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith('/'))
async def song_name(message: Message, state: FSMContext):
    await _restore(state)
    await find_and_show(message, query=' '.join(message.text.split())[:150])


@router.message(StateFilter(None, AddWords.waiting, AddWords.song, AddWords.ai), F.audio)
async def audio_input(message: Message, state: FSMContext):
    await _restore(state)
    audio = message.audio
    await find_and_show(message, query=lyrics.from_filename(audio.file_name), artist=audio.performer or '',
                        title=audio.title or '', duration=audio.duration)


@router.message(AddWords.song, ~F.text.in_(MENU_BUTTONS), ~F.document, ~F.audio, ~F.voice, ~F.video_note)
async def song_not_text(message: Message):
    await message.answer('Напиши исполнителя и название текстом или пришли mp3 🙂')


@router.message(StateFilter(None, AddWords.waiting, AddWords.song), F.voice | F.video_note)
async def voice_input(message: Message):
    await message.answer('🎤 Голос и видео я не распознаю. Напиши название песни — найду её текст.',
                         reply_markup=inline([('🎵 Найти текст песни', 'sg:start')]))


# ─── Поиск и экран песни ───────────────────────────────────

async def find_and_show(message: Message, query='', artist='', title='', duration=None):
    """Ищет текст песни и показывает экран «Нашёл текст»."""
    name = f'{artist} — {title}' if artist and title else (title or query)
    retry = inline([('🎵 Искать ещё раз', 'sg:start')])
    if not name:
        await message.answer('🎵 У файла нет названия. Напиши исполнителя и название — найду текст.',
                             reply_markup=retry)
        return
    try:
        songs = await lyrics.search(query=query, artist=artist, title=title, duration=duration)
    except lyrics.LyricsUnavailable:
        await message.answer('🎵 Сервис текстов песен сейчас не отвечает. '
                             'Вставь текст песни сюда — разберу его.', reply_markup=retry)
        return
    if not songs:
        await message.answer(f'😕 Не нашёл текст песни «{esc(short(name, 100))}».\n'
                             'Проверь название или вставь текст песни сюда — разберу его.', reply_markup=retry)
        return
    data = {'songs': songs, 'index': 0}
    view, kb = _song_view(data)
    sent = await message.answer(view, reply_markup=kb)
    _remember(message.from_user.id, sent.message_id, data)


def _song_view(data):
    song = data['songs'][data['index']]
    lines = [line.strip() for line in song.lyrics.splitlines() if line.strip()]
    preview = ' / '.join(lines[:2])
    text = (f'🎵 <b>{esc(song.artist)} — {esc(song.title)}</b>\n'
            f'Нашёл текст: {len(lines)} {plural(len(lines), "строка", "строки", "строк")}\n\n'
            f'<i>{esc(short(preview, 150))}…</i>')
    if is_russian(song.lyrics):                  # русская песня — карточки с русского через ИИ
        rows = [[('🤖 Через ИИ', 'sg:ai')]]
    else:
        rows = [[('📖 Найти слова', 'sg:words'), ('🤖 Через ИИ', 'sg:ai')]]
    if len(data['songs']) > 1:
        rows.append([('🔁 Не та песня', 'sg:other')])
    rows.append([('✖️ Не надо', 'sg:close')])
    return text, inline(*rows)


@router.callback_query(F.data == 'sg:words')
async def song_words(callback: CallbackQuery):
    data = _screen(callback)
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    song = data['songs'][data['index']]
    if len(song.lyrics) > dictionary.MAX_TEXT:
        await callback.message.answer('🎵 Песня длинная: словарём разберу начало, '
                                      'а целиком — кнопкой «🤖 Разобрать точнее через ИИ».')
    await show_extraction(callback.message, song.lyrics, user_id=callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == 'sg:ai')
async def song_ai(callback: CallbackQuery, bot: Bot):
    data = _screen(callback)
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    await send_ai_prompt(bot, callback.from_user.id, data['songs'][data['index']].lyrics)
    await callback.answer()


@router.callback_query(F.data == 'sg:other')
async def song_other(callback: CallbackQuery):
    data = _screen(callback)
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    rows = [[(short(f'{s.artist} — {s.title}', 50), f'sg:pick:{i}')]
            for i, s in enumerate(data['songs']) if i != data['index']]
    rows.append([('◀️ Назад', 'sg:back')])
    await show(callback, '🎵 Какая из этих?', inline(*rows))
    await callback.answer()


@router.callback_query(F.data.regexp(r'^sg:pick:\d+$') | (F.data == 'sg:back'))
async def song_pick(callback: CallbackQuery):
    data = _screen(callback)
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    if callback.data != 'sg:back':
        index = int(callback.data.rsplit(':', 1)[1])
        if index >= len(data['songs']):
            await callback.answer(STALE, show_alert=True)
            return
        data['index'] = index
    view, kb = _song_view(data)
    await show(callback, view, kb)
    await callback.answer()


@router.callback_query(F.data == 'sg:close')
async def song_close(callback: CallbackQuery):
    if callback.message is not None:
        _screens.pop((callback.from_user.id, callback.message.message_id), None)
    await drop_buttons(callback)
    await callback.answer()
