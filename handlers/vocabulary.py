"""«📋 Мои слова»: список по страницам с отметками «знаю / не знаю», удаление.
Изменить слово — handlers/editor.py, статистика — handlers/study.py.

Удаление — экран с галочками по страницам: отметить отдельные слова, всю страницу,
удалить выученные («Знаю») или всё. Каждое удаление — с подтверждением.
Отметки хранятся в памяти (после перезапуска бота сбрасываются).
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from utils import database
from utils.keyboards import BTN_WORDS, confirm_clear_kb, empty_kb, inline, vocabulary_kb
from utils.scheduler import stop_mailing
from utils.ui import show
from utils.words import esc, short, words_count

router = Router()

PAGE_SIZE = 20   # слов на странице: длинный список не влезет в одно сообщение


def _page(user_id, page):
    """Текст и кнопки нужной страницы словаря."""
    words = database.get_words(user_id)
    if not words:
        return '📋 Мои слова пока пусты.\nДобавь первые слова 👇', empty_kb

    pages = (len(words) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))          # защита от несуществующей страницы
    start = page * PAGE_SIZE

    lines = [f'📋 <b>Мои слова</b> — {len(words)}']
    known = sum(w.answer == 'know' for w in words)
    unknown = sum(w.answer == 'dontknow' for w in words)
    if known or unknown:
        lines.append(f'✅ {known} знаю · 🔁 {unknown} не знаю · 🆕 {len(words) - known - unknown} новых')
    lines.append('')
    for number, item in enumerate(words[start:start + PAGE_SIZE], start=start + 1):
        mark = {'know': '✅ ', 'dontknow': '🔁 '}.get(item.answer, '')
        ipa = f' {esc(short(item.transcription, 40))}' if item.transcription else ''
        if item.direction == 'ru':          # карточка «с русского» — русское первым
            lines.append(f'{number}. {mark}🇷🇺 <b>{esc(short(item.translation, 40))}</b> — '
                         f'{esc(short(item.word, 40))}{ipa}')
        elif item.translation:
            lines.append(f'{number}. {mark}<b>{esc(short(item.word, 40))}</b>{ipa} — {esc(short(item.translation, 40))}')
        else:
            lines.append(f'{number}. {mark}{esc(short(item.word, 80))}{ipa}')
    return '\n'.join(lines), vocabulary_kb(page, pages)


@router.message(Command('words', 'vocabulary'))
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


# ─── Выборочное удаление ───────────────────────────────────

_marks = {}        # user_id → номера (id) отмеченных для удаления слов


def _delete_view(user_id, page):
    words = database.get_words(user_id)
    if not words:
        return '📋 Мои слова пока пусты.', empty_kb
    ids = {w.id for w in words}
    marked = _marks.setdefault(user_id, set())
    marked &= ids                                 # слова могли удалить в другом месте
    pages = (len(words) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    on_page = words[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    text = (f'🗑 <b>Удаление слов</b> — отметь лишние'
            + (f' (стр. {page + 1} / {pages})' if pages > 1 else '')
            + f'\nОтмечено: {len(marked)}')
    rows = []
    for number, item in enumerate(on_page, start=page * PAGE_SIZE + 1):
        front = item.translation if item.direction == 'ru' else item.word
        back = item.word if item.direction == 'ru' else item.translation
        mark = '☑️' if item.id in marked else '☐'
        rows.append([(f'{mark} {number}. {short(front, 25)} — {short(back, 20)}', f'vd:t:{item.id}:{page}')])
    page_ids = {w.id for w in on_page}
    rows.append([('☐ Снять страницу' if page_ids <= marked else '☑️ Вся страница', f'vd:all:{page}')])
    if pages > 1:
        rows.append([
            ('◀️', f'vd:p:{page - 1}') if page > 0 else ('·', 'noop'),
            (f'{page + 1} / {pages}', 'noop'),
            ('▶️', f'vd:p:{page + 1}') if page < pages - 1 else ('·', 'noop'),
        ])
    rows.append([(f'🗑 Удалить отмеченные ({len(marked)})', 'vd:go')])
    _, known, _, _ = database.word_stats(user_id)
    extra = [(f'🗑 Выученные ({known})', 'vd:known')] if known else []
    rows.append(extra + [('🗑 Всё', 'voc:clear')])
    rows.append([('◀️ Отмена', 'voc:page:0')])
    return text, inline(*rows)


@router.callback_query(F.data == 'voc:del')
async def delete_start(callback: CallbackQuery):
    _marks[callback.from_user.id] = set()
    text, kb = _delete_view(callback.from_user.id, 0)
    await show(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith('vd:p:'))
async def delete_page(callback: CallbackQuery):
    text, kb = _delete_view(callback.from_user.id, int(callback.data.rsplit(':', 1)[1]))
    await show(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith('vd:t:'))
async def delete_toggle(callback: CallbackQuery):
    _, _, word_id, page = callback.data.split(':')
    _marks.setdefault(callback.from_user.id, set()).symmetric_difference_update({int(word_id)})
    text, kb = _delete_view(callback.from_user.id, int(page))
    await show(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith('vd:all:'))
async def delete_toggle_page(callback: CallbackQuery):
    user_id = callback.from_user.id
    page = int(callback.data.rsplit(':', 1)[1])
    words = database.get_words(user_id)[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    page_ids = {w.id for w in words}
    marked = _marks.setdefault(user_id, set())
    if page_ids <= marked:
        marked -= page_ids
    else:
        marked |= page_ids
    text, kb = _delete_view(user_id, page)
    await show(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data == 'vd:go')
async def delete_confirm(callback: CallbackQuery):
    marked = _marks.get(callback.from_user.id) or set()
    if not marked:
        await callback.answer('Сначала отметь слова, которые хочешь удалить')
        return
    await show(callback, f'🗑 Удалить {words_count(len(marked))}?\n\nЭто действие нельзя отменить.',
               inline([('🗑 Да, удалить', 'vd:yes')], [('◀️ Назад', 'vd:p:0')]))
    await callback.answer()


@router.callback_query(F.data == 'vd:known')
async def delete_known_confirm(callback: CallbackQuery):
    _, known, _, _ = database.word_stats(callback.from_user.id)
    if not known:
        await callback.answer('Выученных слов пока нет')
        return
    await show(callback, f'🗑 Удалить выученные слова — {words_count(known)} с ответом «Знаю»?\n\n'
                         'Это действие нельзя отменить.',
               inline([('🗑 Да, удалить выученные', 'vd:known:yes')], [('◀️ Назад', 'vd:p:0')]))
    await callback.answer()


@router.callback_query(F.data.in_({'vd:yes', 'vd:known:yes'}))
async def delete_do(callback: CallbackQuery):
    user_id = callback.from_user.id
    if callback.data == 'vd:yes':
        removed = database.delete_word_ids(user_id, _marks.pop(user_id, set()))
    else:
        removed = database.delete_known(user_id)
        _marks.pop(user_id, None)
    if not database.count_words(user_id):
        stop_mailing(user_id)                     # слать больше нечего
    text, kb = _page(user_id, 0)
    await show(callback, f'🗑 Удалил: {words_count(removed)}\n\n{text}', kb)
    await callback.answer('Удалено')


@router.callback_query(F.data == 'voc:clear')
async def ask_clear(callback: CallbackQuery):
    total = database.count_words(callback.from_user.id)
    if total == 0:
        await callback.answer('Слов и так нет')
        return
    await show(callback,
               f'🗑 Удалить все слова — {words_count(total)}?\n\nЭто действие нельзя отменить.',
               confirm_clear_kb)
    await callback.answer()


@router.callback_query(F.data == 'voc:clear:yes')
async def clear(callback: CallbackQuery):
    user_id = callback.from_user.id
    database.delete_words(user_id)
    stop_mailing(user_id)                 # слать больше нечего
    await show(callback, '🗑 Все слова удалены. Рассылка тоже выключена.', empty_kb)
    await callback.answer('Удалено')
