"""Раздел «Учить».

Сначала экран выбора — как учить:
• 🎴 Карточки — прямо сейчас: слово → показать перевод → знаю / не знаю / пропустить
• 🔔 По расписанию — бот сам присылает слова (это в handlers/mailing.py)

Тренировка по карточкам — одно сообщение, которое бот редактирует на месте.
Список карточек и позиция хранятся в данных состояния (FSM), поэтому
у каждого пользователя своя тренировка, и рассылка её не сбивает.
"""
import random

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from utils import database
from utils.keyboards import (
    BTN_STUDY, MENU_BUTTONS, PHRASES, card_back_kb, card_front_kb, edit_cancel_kb, empty_kb,
    study_done_kb, study_menu_kb,
)
from utils.states import Study
from utils.ui import drop_buttons, show
from utils.database import Word
from utils.words import esc, words_count

router = Router()

EMPTY_TEXT = '🎴 Словарь пока пуст — учить нечего.\nДобавь первые слова 👇'
STALE_TEXT = 'Эта карточка устарела. Нажми 🎴 Учить, чтобы начать заново.'


# ─── Экран «Как будем учить?» ──────────────────────────────

def study_menu(user_id, header=''):
    """Текст и кнопки экрана выбора. header — строка сверху, например «Готово!»."""
    total = database.count_words(user_id)
    if total == 0:
        return EMPTY_TEXT, empty_kb
    minutes = database.get_mailing(user_id)
    mailing = f'✅ {PHRASES.get(minutes, f"каждые {minutes} мин")}' if minutes else '⛔ выключена'
    text = (f'{header}'
            '🎴 <b>Как будем учить?</b>\n\n'
            f'📚 В словаре: {words_count(total)}\n'
            f'🔔 Рассылка: {mailing}\n\n'
            '🎴 <b>Карточки</b> — пройдёшь слова прямо сейчас: '
            'видишь слово, вспоминаешь, проверяешь перевод\n'
            '🔔 <b>По расписанию</b> — буду сам присылать по слову раз в несколько минут')
    return text, study_menu_kb


@router.message(Command('study'))
@router.message(F.text == BTN_STUDY)
async def study_from_menu(message: Message, state: FSMContext):
    await state.clear()
    text, kb = study_menu(message.from_user.id)
    await message.answer(text, reply_markup=kb)


# 'study:start' — кнопки прошлой версии, которые ещё висят в старых сообщениях
@router.callback_query(F.data.in_({'study:menu', 'study:start'}))
async def study_menu_button(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() == Study.card.state:
        await state.clear()
    text, kb = study_menu(callback.from_user.id)
    await show(callback, text, kb)
    await callback.answer()


# ─── Карточки ──────────────────────────────────────────────

def _card(data, revealed):
    item = Word(*data['cards'][data['pos']])
    text = f'🎴 Карточка {data["pos"] + 1} из {len(data["cards"])}\n\n🇬🇧 <b>{esc(item.word)}</b>'
    if item.transcription:
        text += f'  {esc(item.transcription)}'
    if revealed:
        text += f'\n🇷🇺 {esc(item.translation) if item.translation else "—"}'
        if item.example:
            text += f'\n\n💬 <i>{esc(item.example)}</i>'
    else:
        text += '\n\n<i>Вспомни перевод и нажми «Показать»</i>'
    return text


def _summary(data, finished):
    answered, known = data['pos'], data['known']
    title = '🎉 <b>Тренировка завершена!</b>' if finished else '⏹ <b>Тренировка остановлена</b>'
    if answered == 0:
        return f'{title}\n\nНи одной карточки не пройдено.'
    return (f'{title}\n\n'
            f'Пройдено: {answered} из {len(data["cards"])}\n'
            f'✅ Знаю: {known}\n'
            f'🔁 Повторить: {answered - known}')


async def _session(callback, state):
    """Данные тренировки, если кнопку нажали на актуальной карточке. Иначе None."""
    data = await state.get_data()
    if callback.message is None or data.get('message_id') != callback.message.message_id:
        await callback.answer(STALE_TEXT, show_alert=True)
        return None
    return data


async def _show_card(callback, state, data, revealed):
    """Показывает карточку и запоминает, в каком сообщении она теперь."""
    shown = await show(callback, _card(data, revealed),
                       card_back_kb if revealed else card_front_kb)
    data['message_id'] = shown.message_id
    await state.set_data(data)


@router.callback_query(F.data == 'study:cards')
async def cards_start(callback: CallbackQuery, state: FSMContext):
    words = database.get_words(callback.from_user.id)
    if not words:
        await state.clear()
        await show(callback, EMPTY_TEXT, empty_kb)
        await callback.answer()
        return
    random.shuffle(words)                       # каждый раз новый порядок
    await state.set_state(Study.card)
    cards = [tuple(w) for w in words]
    await _show_card(callback, state, {'cards': cards, 'pos': 0, 'known': 0}, revealed=False)
    await callback.answer()


@router.callback_query(Study.card, F.data == 'study:reveal')
async def reveal(callback: CallbackQuery, state: FSMContext):
    data = await _session(callback, state)
    if data is None:
        return
    await _show_card(callback, state, data, revealed=True)
    await callback.answer()


# 'study:dontknow' — это и «Не знаю» после перевода, и «Пропустить» до него
@router.callback_query(Study.card, F.data.in_({'study:know', 'study:dontknow'}))
async def answer(callback: CallbackQuery, state: FSMContext):
    data = await _session(callback, state)
    if data is None:
        return
    if callback.data == 'study:know':
        data['known'] += 1
        database.increment_learned(callback.from_user.id)
    data['pos'] += 1

    if data['pos'] >= len(data['cards']):
        await state.clear()
        await show(callback, _summary(data, finished=True), study_done_kb)
    else:
        await _show_card(callback, state, data, revealed=False)
    await callback.answer()


@router.callback_query(Study.card, F.data == 'study:stop')
async def stop(callback: CallbackQuery, state: FSMContext):
    data = await _session(callback, state)
    if data is None:
        return
    await state.clear()
    await show(callback, _summary(data, finished=False), study_done_kb)
    await callback.answer()


# ─── Исправить или удалить слово прямо в карточке ──────────

@router.callback_query(Study.card, F.data == 'study:edit')
async def edit_start(callback: CallbackQuery, state: FSMContext):
    data = await _session(callback, state)
    if data is None:
        return
    item = Word(*data['cards'][data['pos']])
    await state.set_state(Study.edit)            # данные тренировки сохраняются
    await callback.bot.send_message(
        callback.from_user.id,
        f'✏️ Напиши правильный перевод для «<b>{esc(item.word)}</b>»\n'
        f'Сейчас: {esc(item.translation) if item.translation else "—"}',
        reply_markup=edit_cancel_kb)
    await callback.answer()


@router.message(Study.edit, F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith('/'))
async def edit_save(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    item = Word(*data['cards'][data['pos']])
    translation = ' '.join(message.text.split())[:100]
    database.update_translation(user_id, item.id, translation)
    data['cards'][data['pos']] = tuple(item._replace(translation=translation))

    # Старую карточку закрываем, исправленную присылаем вниз — продолжать удобнее
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=user_id, message_id=data['message_id'], reply_markup=None)
    except Exception:
        pass
    sent = await message.answer('✅ Перевод исправлен\n\n' + _card(data, revealed=True),
                                reply_markup=card_back_kb)
    data['message_id'] = sent.message_id
    await state.set_state(Study.card)
    await state.set_data(data)


@router.message(Study.edit, ~F.text.in_(MENU_BUTTONS))
async def edit_not_text(message: Message):
    await message.answer('Напиши перевод текстом 🙂', reply_markup=edit_cancel_kb)


@router.callback_query(F.data == 'study:edit_cancel')
async def edit_cancel(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() == Study.edit.state:
        await state.set_state(Study.card)
    await drop_buttons(callback)
    await callback.answer('Оставил как было')


@router.callback_query(Study.card, F.data == 'study:delete')
async def delete_card(callback: CallbackQuery, state: FSMContext):
    data = await _session(callback, state)
    if data is None:
        return
    item = Word(*data['cards'].pop(data['pos']))
    database.delete_word(callback.from_user.id, item.id)

    if data['pos'] >= len(data['cards']):        # это была последняя карточка
        await state.clear()
        await show(callback, _summary(data, finished=True), study_done_kb)
    else:
        await _show_card(callback, state, data, revealed=False)
    await callback.answer(f'🗑 Удалил «{item.word}» из словаря')
