"""Состояния диалога (FSM — машина состояний aiogram).

Состояние — это «на каком шаге сейчас пользователь». От него зависит,
как бот поймёт следующее сообщение: в режиме добавления текст — это слова,
в обычном режиме — непонятная команда.
"""
from aiogram.fsm.state import State, StatesGroup


class AddWords(StatesGroup):
    waiting = State()   # ждём пары «слово - перевод»


class Study(StatesGroup):
    card = State()      # идёт тренировка по карточкам
