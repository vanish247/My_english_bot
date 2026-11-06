import sqlite3
from collections import namedtuple

from utils.words import split_pair

DB = 'bot_database.db'

# Одно слово из словаря пользователя
Word = namedtuple('Word', 'word translation transcription example')


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
        for column in ('translation', 'transcription', 'example'):
            if column not in columns:
                conn.execute(f"ALTER TABLE words ADD COLUMN {column} TEXT DEFAULT ''")
        old_rows = conn.execute(
            "SELECT id, word FROM words WHERE translation = '' AND word LIKE '%-%'").fetchall()
        for row_id, text in old_rows:
            word, translation = split_pair(text)
            if translation:
                conn.execute('UPDATE words SET word = ?, translation = ? WHERE id = ?',
                             (word, translation, row_id))


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
            if key(item) in existing:
                duplicates.append(item)
                continue
            conn.execute(
                'INSERT INTO words (user_id, word, translation, transcription, example) '
                'VALUES (?, ?, ?, ?, ?)',
                (user_id, item.word, item.translation, item.transcription, item.example)
            )
            existing.add(key(item))
            added.append(item)
    return added, duplicates


def get_words(user_id: int) -> list:
    """Все слова пользователя (список Word) в порядке добавления"""
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            'SELECT word, translation, transcription, example FROM words '
            'WHERE user_id = ? ORDER BY id',
            (user_id,)
        ).fetchall()
    return [Word(*(value or '' for value in row)) for row in rows]


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


def get_mailings() -> list:
    """Все включённые рассылки: [(user_id, минуты), ...] — для восстановления при старте"""
    with sqlite3.connect(DB) as conn:
        return conn.execute(
            'SELECT user_id, mailing_minutes FROM users WHERE mailing_minutes > 0'
        ).fetchall()
