"""Все клавиатуры бота.

Два вида кнопок:
• Reply-клавиатура (main_menu) — постоянное меню внизу экрана.
  Нажатие отправляет боту текст кнопки, как будто его напечатали.
• Inline-кнопки — прикреплены к конкретному сообщению.
  Нажатие присылает боту скрытую строку callback_data, например 'study:reveal'.
"""
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
)

# Тексты кнопок меню. Хэндлеры сравнивают с этими же константами —
# поэтому текст кнопки достаточно поменять в одном месте.
BTN_ADD = '📚 Добавить слова'
BTN_STUDY = '🎴 Учить'
BTN_WORDS = '📋 Мои слова'
BTN_HELP = 'ℹ️ Помощь'
OLD_BTN_MAILING = '🔔 Рассылка'   # кнопка прошлой версии: у кого-то ещё висит старое меню

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ADD)],
        [KeyboardButton(text=BTN_STUDY), KeyboardButton(text=BTN_WORDS)],
        [KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
    is_persistent=True,                        # меню не сворачивается
    input_field_placeholder='Выбери действие 👇',
)


def inline(*rows):
    """Собирает inline-клавиатуру. Каждый ряд — список пар (текст, callback_data)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows
    ])


# ─── Добавление слов ───────────────────────────────────────
add_kb = inline([('✅ Готово', 'add:done')])
empty_kb = inline([('📚 Добавить слова', 'add:start')])

# ─── «Как будем учить?» ────────────────────────────────────
# Показывается по кнопке «Учить» и после «Готово» при добавлении слов
study_menu_kb = inline(
    [('🎴 Карточки — прямо сейчас', 'study:cards')],
    [('🔔 Присылать по расписанию', 'study:mailing')],
    [('📋 Мои слова', 'voc:page:0')],
)

# ─── Карточки ──────────────────────────────────────────────
card_front_kb = inline(
    [('👀 Показать перевод', 'study:reveal')],
    [('⏭ Пропустить', 'study:dontknow'), ('⏹ Закончить', 'study:stop')],
)
card_back_kb = inline(
    [('✅ Знаю', 'study:know'), ('🔁 Не знаю', 'study:dontknow')],
    [('⏹ Закончить', 'study:stop')],
)
study_done_kb = inline(
    [('🔁 Ещё раз', 'study:cards'), ('◀️ К выбору', 'study:menu')],
)


# ─── Словарь ───────────────────────────────────────────────
def vocabulary_kb(page, pages):
    rows = []
    if pages > 1:
        rows.append([
            ('◀️', f'voc:page:{page - 1}') if page > 0 else ('·', 'noop'),
            (f'{page + 1} / {pages}', 'noop'),
            ('▶️', f'voc:page:{page + 1}') if page < pages - 1 else ('·', 'noop'),
        ])
    rows.append([('🎴 Учить', 'study:menu'), ('🗑 Очистить', 'voc:clear')])
    return inline(*rows)


confirm_clear_kb = inline(
    [('🗑 Да, удалить всё', 'voc:clear:yes')],
    [('◀️ Отмена', 'voc:page:0')],
)

# ─── Рассылка по расписанию ────────────────────────────────
# (минуты, текст кнопки, как писать в статусе)
INTERVALS = [
    (5, '5 мин', 'каждые 5 минут'),
    (15, '15 мин', 'каждые 15 минут'),
    (30, '30 мин', 'каждые 30 минут'),
    (60, '1 час', 'каждый час'),
    (180, '3 часа', 'каждые 3 часа'),
]
PHRASES = {minutes: phrase for minutes, _, phrase in INTERVALS}


def mailing_kb(current):
    """Выбор интервала. Текущий отмечен галочкой."""
    buttons = [
        (('✅ ' if minutes == current else '') + label, f'mail:{minutes}')
        for minutes, label, _ in INTERVALS
    ]
    rows = [buttons[:3], buttons[3:]]
    if current:
        rows.append([('🔕 Выключить рассылку', 'mail:off')])
    rows.append([('◀️ Назад', 'study:menu')])
    return inline(*rows)
