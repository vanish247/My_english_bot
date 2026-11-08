import hashlib
import sqlite3
from collections import namedtuple

from utils.dictionary import normalize_case
from utils.words import split_pair

DB = 'bot_database.db'

# Одно слово из словаря пользователя.
# example_ru — перевод примера (его даёт ИИ), id — номер в базе (у слов, прочитанных из базы)
Word = namedtuple('Word', 'word translation transcription example example_ru id', defaults=('', None))


def init_db():
    """Создаёт таблицы при первом запуске и дописывает новые колонки в старые базы."""
    with sqlite3.connect(DB) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                created_at    TEXT DEFAULT (datetime('now')),
                words_learned INTEGER DEFAULT 0
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS words (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                word     TEXT NOT NULL,
                added_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        # Миграция: интервал рассылки храним в базе, чтобы она пережила перезапуск бота
        columns = [row[1] for row in conn.execute('PRAGMA table_info(users)')]
        if 'mailing_minutes' not in columns:
            conn.execute('ALTER TABLE users ADD COLUMN mailing_minutes INTEGER DEFAULT 0')

        # Миграция: раньше слово хранилось одной строкой «apple - яблоко»,
        # теперь слово, перевод, транскрипция и пример — в отдельных колонках
        columns = [row[1] for row in conn.execute('PRAGMA table_info(words)')]
        for column in ('translation', 'transcription', 'example', 'example_ru'):
            if column not in columns:
                conn.execute(f"ALTER TABLE words ADD COLUMN {column} TEXT DEFAULT ''")
        old_rows = conn.execute(
            "SELECT id, word FROM words WHERE translation = '' AND word LIKE '%-%'").fetchall()
        for row_id, text in old_rows:
            word, translation = split_pair(text)
            if translation:
                conn.execute('UPDATE words SET word = ?, translation = ? WHERE id = ?',
                             (word, translation, row_id))

        # Карточки, отправленные в чаты через inline-режим: кнопка «➕ В мой словарь»
        # под ними должна работать и через месяц, поэтому храним их в базе
        conn.execute('''
            CREATE TABLE IF NOT EXISTS shared_cards (
                id TEXT PRIMARY KEY, word TEXT, translation TEXT, transcription TEXT,
                example TEXT, created_at TEXT DEFAULT (datetime('now')))
        ''')
        # Фразы из inline-режима для кнопки «📥 Выбрать слова и добавить в бота»
        conn.execute('''
            CREATE TABLE IF NOT EXISTS inline_texts (
                id TEXT PRIMARY KEY, text TEXT, created_at TEXT DEFAULT (datetime('now')))
        ''')

        # Текст, из которого собран последний промпт для ИИ: по нему карточки из ответа ИИ
        # расставляются в порядке текста (ИИ любит их перемешивать)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ai_texts (
                user_id INTEGER PRIMARY KEY, text TEXT, created_at TEXT DEFAULT (datetime('now')))
        ''')

        # Фразы из inline-режима нужны, только пока человек жмёт «📥» — неделю храним, потом чистим
        conn.execute("DELETE FROM inline_texts WHERE created_at < datetime('now', '-7 days')")

        # Слова — маленькими буквами (кроме имён собственных), дубли после этого убираем
        for row_id, word in conn.execute('SELECT id, word FROM words').fetchall():
            fixed = normalize_case(word)
            if fixed != word:
                conn.execute('UPDATE words SET word = ? WHERE id = ?', (fixed, row_id))
        conn.execute('DELETE FROM words WHERE id NOT IN '
                     '(SELECT MIN(id) FROM words GROUP BY user_id, word, translation)')


def init_user(user_id: int, username: str):
    """Добавляет пользователя если его ещё нет"""
    with sqlite3.connect(DB) as conn:
        conn.execute(
            'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
            (user_id, username)
        )


def add_words(user_id: int, words: list):
    """Добавляет слова (список Word), пропуская те, что уже есть в словаре.

    Повтор — то же слово с тем же переводом: «found — основывать» и
    «found — находить» — два разных значения, оба добавятся.
    Возвращает (добавленные, повторы).
    """
    def key(item):
        return item.word.lower(), item.translation.lower()

    existing = {key(w) for w in get_words(user_id)}
    added, duplicates = [], []
    with sqlite3.connect(DB) as conn:
        for item in words:
            item = item._replace(word=normalize_case(item.word))   # Tomorrow → tomorrow
            if key(item) in existing:
                duplicates.append(item)
                continue
            conn.execute(
                'INSERT INTO words (user_id, word, translation, transcription, example, example_ru) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (user_id, item.word, item.translation, item.transcription, item.example, item.example_ru)
            )
            existing.add(key(item))
            added.append(item)
    return added, duplicates


def get_words(user_id: int) -> list:
    """Все слова пользователя (список Word) в порядке добавления"""
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            'SELECT word, translation, transcription, example, example_ru, id FROM words '
            'WHERE user_id = ? ORDER BY id',
            (user_id,)
        ).fetchall()
    return [Word(*(value or '' for value in row[:5]), id=row[5]) for row in rows]


def update_translation(user_id: int, word_id: int, translation: str):
    """Исправляет перевод одного слова"""
    with sqlite3.connect(DB) as conn:
        conn.execute('UPDATE words SET translation = ? WHERE id = ? AND user_id = ?',
                     (translation, word_id, user_id))


def delete_word(user_id: int, word_id: int):
    """Удаляет одно слово"""
    with sqlite3.connect(DB) as conn:
        conn.execute('DELETE FROM words WHERE id = ? AND user_id = ?', (word_id, user_id))


def count_words(user_id: int) -> int:
    """Сколько слов в словаре пользователя"""
    with sqlite3.connect(DB) as conn:
        return conn.execute(
            'SELECT COUNT(*) FROM words WHERE user_id = ?', (user_id,)
        ).fetchone()[0]


def delete_words(user_id: int):
    """Удаляет все слова пользователя"""
    with sqlite3.connect(DB) as conn:
        conn.execute('DELETE FROM words WHERE user_id = ?', (user_id,))


def increment_learned(user_id: int):
    """Увеличивает счётчик изученных слов на 1"""
    with sqlite3.connect(DB) as conn:
        conn.execute(
            'UPDATE users SET words_learned = words_learned + 1 WHERE user_id = ?',
            (user_id,)
        )


def set_mailing(user_id: int, minutes: int):
    """Запоминает интервал рассылки (0 — выключена)"""
    with sqlite3.connect(DB) as conn:
        conn.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
        conn.execute(
            'UPDATE users SET mailing_minutes = ? WHERE user_id = ?',
            (minutes, user_id)
        )


def get_mailing(user_id: int) -> int:
    """Текущий интервал рассылки пользователя в минутах (0 — выключена)"""
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            'SELECT mailing_minutes FROM users WHERE user_id = ?', (user_id,)
        ).fetchone()
    return row[0] if row and row[0] else 0


def user_exists(user_id: int) -> bool:
    """Запускал ли человек бота (есть ли он в таблице users)"""
    with sqlite3.connect(DB) as conn:
        return conn.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,)).fetchone() is not None


def _short_id(*parts):
    """Короткий постоянный номер по содержимому — для callback_data и ссылок."""
    return hashlib.sha1('\x1f'.join(parts).encode()).hexdigest()[:16]


def save_shared_card(word, translation, transcription, example) -> str:
    """Запоминает карточку из inline-режима, возвращает её номер."""
    card_id = _short_id(word, translation, example)
    with sqlite3.connect(DB) as conn:
        conn.execute('INSERT OR IGNORE INTO shared_cards (id, word, translation, transcription, example) '
                     'VALUES (?, ?, ?, ?, ?)', (card_id, word, translation, transcription, example))
    return card_id


def get_shared_card(card_id):
    """Карточка из inline-режима (Word) или None."""
    with sqlite3.connect(DB) as conn:
        row = conn.execute('SELECT word, translation, transcription, example FROM shared_cards WHERE id = ?',
                           (card_id,)).fetchone()
    return Word(*row) if row else None


def save_inline_text(text) -> str:
    """Запоминает фразу из inline-режима, возвращает её номер для ссылки /start q_<номер>."""
    text_id = _short_id(text)
    with sqlite3.connect(DB) as conn:
        conn.execute('INSERT OR IGNORE INTO inline_texts (id, text) VALUES (?, ?)', (text_id, text))
    return text_id


def get_inline_text(text_id):
    with sqlite3.connect(DB) as conn:
        row = conn.execute('SELECT text FROM inline_texts WHERE id = ?', (text_id,)).fetchone()
    return row[0] if row else None


def save_ai_text(user_id: int, text: str):
    """Запоминает текст, из которого собран промпт для ИИ (храним только последний)."""
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT OR REPLACE INTO ai_texts (user_id, text, created_at) "
                     "VALUES (?, ?, datetime('now'))", (user_id, text))


def get_ai_text(user_id: int):
    with sqlite3.connect(DB) as conn:
        row = conn.execute('SELECT text FROM ai_texts WHERE user_id = ?', (user_id,)).fetchone()
    return row[0] if row else None


def get_mailings() -> list:
    """Все включённые рассылки: [(user_id, минуты), ...] — для восстановления при старте"""
    with sqlite3.connect(DB) as conn:
        return conn.execute(
            'SELECT user_id, mailing_minutes FROM users WHERE mailing_minutes > 0'
        ).fetchall()
