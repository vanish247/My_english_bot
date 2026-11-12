import hashlib
import sqlite3
from collections import namedtuple

from utils.dictionary import normalize_case, transcription
from utils.words import split_pair

DB = 'bot_database.db'

# Одно слово из словаря пользователя.
#   word — английское слово, translation — русский перевод (для карточек «с русского» тоже);
#   example_ru — перевод примера; direction — 'en' (учим английское → русское)
#   или 'ru' (карточка показывает русское, вспоминаем английское);
#   id — номер в базе (у слов, прочитанных из базы)
Word = namedtuple('Word', 'word translation transcription example example_ru direction id',
                  defaults=('', 'en', None))


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
        for column in ('translation', 'transcription', 'example', 'example_ru', 'last_answer'):
            if column not in columns:
                conn.execute(f"ALTER TABLE words ADD COLUMN {column} TEXT DEFAULT ''")
        if 'direction' not in columns:
            conn.execute("ALTER TABLE words ADD COLUMN direction TEXT DEFAULT 'en'")
        # Ответы в карточках: «Знаю» / «Не знаю» — для режима «Только незнакомые» и статистики
        for column in ('know_count', 'dontknow_count'):
            if column not in columns:
                conn.execute(f'ALTER TABLE words ADD COLUMN {column} INTEGER DEFAULT 0')
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
        if 'direction' not in [row[1] for row in conn.execute('PRAGMA table_info(ai_texts)')]:
            conn.execute("ALTER TABLE ai_texts ADD COLUMN direction TEXT DEFAULT 'en'")

        # Фразы из inline-режима нужны, только пока человек жмёт «📥» — неделю храним, потом чистим
        conn.execute("DELETE FROM inline_texts WHERE created_at < datetime('now', '-7 days')")

        # Слова — маленькими буквами (кроме имён собственных), дубли после этого убираем
        for row_id, word in conn.execute('SELECT id, word FROM words').fetchall():
            fixed = normalize_case(word)
            if fixed != word:
                conn.execute('UPDATE words SET word = ? WHERE id = ?', (fixed, row_id))
        conn.execute('DELETE FROM words WHERE id NOT IN '
                     '(SELECT MIN(id) FROM words GROUP BY user_id, word, translation, direction)')

        # Транскрипция у всех слов: дописываем тем, у кого её нет
        for row_id, word in conn.execute("SELECT id, word FROM words WHERE transcription = ''").fetchall():
            ipa = transcription(word)
            if ipa:
                conn.execute('UPDATE words SET transcription = ? WHERE id = ?', (ipa, row_id))


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
    «found — находить» — два разных значения, оба добавятся. Карточка
    «с русского» (dog ← собака) — не повтор английской (dog → собака).
    Возвращает (добавленные, повторы).
    """
    def key(item):
        return item.word.lower(), item.translation.lower(), item.direction or 'en'

    existing = {key(w) for w in get_words(user_id)}
    added, duplicates = [], []
    with sqlite3.connect(DB) as conn:
        for item in words:
            item = item._replace(word=normalize_case(item.word))   # Tomorrow → tomorrow
            if not item.transcription:
                item = item._replace(transcription=transcription(item.word))   # транскрипция — всегда
            if key(item) in existing:
                duplicates.append(item)
                continue
            conn.execute(
                'INSERT INTO words (user_id, word, translation, transcription, example, example_ru, direction) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (user_id, item.word, item.translation, item.transcription, item.example, item.example_ru,
                 item.direction or 'en')
            )
            existing.add(key(item))
            added.append(item)
    return added, duplicates


def get_words(user_id: int, only_unknown=False) -> list:
    """Слова пользователя (список Word) в порядке добавления.
    only_unknown — только незнакомые: последний ответ не «Знаю» (или ещё не учили)."""
    condition = " AND last_answer != 'know'" if only_unknown else ''
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            'SELECT word, translation, transcription, example, example_ru, direction, id FROM words '
            f'WHERE user_id = ?{condition} ORDER BY id',
            (user_id,)
        ).fetchall()
    return [Word(*(value or '' for value in row[:5]), direction=row[5] or 'en', id=row[6]) for row in rows]


def record_answer(user_id: int, word_id: int, known: bool):
    """Запоминает ответ в карточке: «Знаю» или «Не знаю / Пропустить»."""
    column = 'know_count' if known else 'dontknow_count'
    with sqlite3.connect(DB) as conn:
        conn.execute(f'UPDATE words SET last_answer = ?, {column} = {column} + 1 WHERE id = ? AND user_id = ?',
                     ('know' if known else 'dontknow', word_id, user_id))


def word_stats(user_id: int):
    """(всего, знаю, не знаю, ещё не учил)"""
    with sqlite3.connect(DB) as conn:
        rows = dict(conn.execute('SELECT last_answer, COUNT(*) FROM words WHERE user_id = ? GROUP BY last_answer',
                                 (user_id,)).fetchall())
    known, unknown, new = rows.get('know', 0), rows.get('dontknow', 0), rows.get('', 0) + rows.get(None, 0)
    return known + unknown + new, known, unknown, new


def hard_words(user_id: int, limit=20) -> list:
    """Слова, на которые последний ответ «Не знаю»: чаще забываемые — выше."""
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            'SELECT word, translation, transcription, example, example_ru, direction, id FROM words '
            "WHERE user_id = ? AND last_answer = 'dontknow' ORDER BY dontknow_count DESC, id LIMIT ?",
            (user_id, limit)).fetchall()
    return [Word(*(value or '' for value in row[:5]), direction=row[5] or 'en', id=row[6]) for row in rows]


def update_word(user_id: int, word_id: int, word: str):
    """Исправляет английскую сторону карточки (у карточек «с русского»)"""
    with sqlite3.connect(DB) as conn:
        conn.execute('UPDATE words SET word = ?, transcription = ? WHERE id = ? AND user_id = ?',
                     (word, transcription(word), word_id, user_id))


def update_translation(user_id: int, word_id: int, translation: str):
    """Исправляет перевод одного слова"""
    with sqlite3.connect(DB) as conn:
        conn.execute('UPDATE words SET translation = ? WHERE id = ? AND user_id = ?',
                     (translation, word_id, user_id))


def delete_word(user_id: int, word_id: int):
    """Удаляет одно слово"""
    with sqlite3.connect(DB) as conn:
        conn.execute('DELETE FROM words WHERE id = ? AND user_id = ?', (word_id, user_id))


def delete_word_ids(user_id: int, word_ids) -> int:
    """Удаляет выбранные слова, возвращает, сколько удалено"""
    word_ids = list(word_ids)
    if not word_ids:
        return 0
    with sqlite3.connect(DB) as conn:
        marks = ','.join('?' * len(word_ids))
        return conn.execute(f'DELETE FROM words WHERE user_id = ? AND id IN ({marks})',
                            (user_id, *word_ids)).rowcount


def delete_known(user_id: int) -> int:
    """Удаляет выученные слова (последний ответ «Знаю»), возвращает, сколько удалено"""
    with sqlite3.connect(DB) as conn:
        return conn.execute("DELETE FROM words WHERE user_id = ? AND last_answer = 'know'",
                            (user_id,)).rowcount


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


def save_ai_text(user_id: int, text: str, direction: str = 'en'):
    """Запоминает текст, из которого собран промпт для ИИ (храним только последний),
    и направление: 'en' — английский текст, 'ru' — русский (карточки с русского)."""
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT OR REPLACE INTO ai_texts (user_id, text, direction, created_at) "
                     "VALUES (?, ?, ?, datetime('now'))", (user_id, text, direction))


def get_ai_text(user_id: int):
    """(текст последнего промпта, направление) или (None, 'en')."""
    with sqlite3.connect(DB) as conn:
        row = conn.execute('SELECT text, direction FROM ai_texts WHERE user_id = ?', (user_id,)).fetchone()
    return (row[0], row[1] or 'en') if row else (None, 'en')


def get_mailings() -> list:
    """Все включённые рассылки: [(user_id, минуты), ...] — для восстановления при старте"""
    with sqlite3.connect(DB) as conn:
        return conn.execute(
            'SELECT user_id, mailing_minutes FROM users WHERE mailing_minutes > 0'
        ).fetchall()
