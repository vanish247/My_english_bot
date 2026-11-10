"""Разбор слов и оформление текста."""
import html
import re

# Сначала ищем дефис с пробелами вокруг ("well-known - известный"),
# если такого нет — любой дефис ("apple-яблоко"). Тире — и iPhone их ставит.
_SPACED_DASH = re.compile(r'\s+[-–—]\s+')
_ANY_DASH = re.compile(r'\s*[-–—]\s*')


def split_pair(text):
    """'apple - яблоко' -> ('apple', 'яблоко'). Нет перевода -> (text, '')."""
    text = text.strip()
    for dash in (_SPACED_DASH, _ANY_DASH):
        parts = dash.split(text, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()
    return text, ''


def parse_words(text):
    """Разбирает сообщение на пары. Пары — через запятую, точку с запятой или с новой строки.

    Возвращает (пары (слово, перевод), куски без перевода).
    """
    good, bad = [], []
    for chunk in re.split(r'[,;\n]', text):
        chunk = chunk.strip()
        if not chunk:
            continue                      # пустые куски (лишняя запятая) пропускаем
        word, translation = split_pair(chunk)
        if translation:
            good.append((word, translation))
        else:
            bad.append(chunk)
    return good, bad


def has_latin(text):
    """Есть ли в тексте английские буквы."""
    return re.search(r'[A-Za-z]', text) is not None


def plural(n, one, few, many):
    """plural(5, 'слово', 'слова', 'слов') -> 'слов'"""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def words_count(n):
    """31 -> '31 слово'"""
    return f'{n} {plural(n, "слово", "слова", "слов")}'


def esc(text):
    """Экранирует текст пользователя: '<' и '&' сломали бы HTML-разметку сообщения."""
    return html.escape(text, quote=False)


def short(text, limit=60):
    """Обрезает слишком длинный текст, чтобы сообщение не превысило лимит Telegram."""
    return text if len(text) <= limit else text[:limit - 1] + '…'


def is_russian(text):
    """Русский текст: русских букв больше, чем английских."""
    cyrillic = len(re.findall(r'[А-Яа-яЁё]', text))
    latin = len(re.findall(r'[A-Za-z]', text))
    return cyrillic > latin
