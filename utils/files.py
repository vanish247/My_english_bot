"""Файлы и таблицы: достаём из них текст и пары «слово — перевод».

• .txt / .srt — просто текст (у субтитров убираем номера и тайминги);
• .docx — текст абзацев; таблица «слово | транскрипция | перевод» (как у репетитора)
  становится готовыми парами. Читаем без лишних библиотек: docx — это zip с XML;
• .pdf — только текстовый слой (библиотека pypdf). Сканы не распознаём.

Таблицу можно и вставить сообщением — скопированная из Word или Excel,
она приходит строками с табуляцией между ячейками.
"""
import io
import re
import zipfile
from collections import namedtuple
from xml.etree import ElementTree

MAX_FILE = 5 * 1024 * 1024        # больше 5 МБ не скачиваем
MAX_XML = 50 * 1024 * 1024        # защита от «zip-бомбы» внутри docx
MAX_PDF_PAGES = 100
KINDS = ('.txt', '.docx', '.pdf', '.srt', '.md')

# text — текст для разбора, pairs — [(слово, перевод, транскрипция)] из таблиц
Content = namedtuple('Content', 'text pairs')


class FileError(Exception):
    """Понятная пользователю причина, почему файл не прочитался."""


def kind(filename):
    """Расширение, если файл нам понятен, иначе None."""
    name = (filename or '').lower()
    return next((ext for ext in KINDS if name.endswith(ext)), None)


# ─── Таблицы ───────────────────────────────────────────────

_CYRILLIC = re.compile(r'[А-Яа-яЁё]')
_LATIN = re.compile(r'[A-Za-z]')
_IPA_CHARS = re.compile(r'[ˈˌːəɪʊæɒʌɔɜɑθðʃʒŋɛ]')
_HEADERS = {'word', 'words', 'english', 'phrase', 'expression', 'transcription', 'translation',
            'слово', 'перевод', 'транскрипция'}


def _is_ipa(cell):
    cell = cell.strip()
    return bool(re.fullmatch(r'[/\[].+[/\]]', cell)) or (
        bool(_IPA_CHARS.search(cell)) and not _CYRILLIC.search(cell))


def _ipa(cell):
    cell = cell.strip()
    return cell if cell[:1] in '/[' else f'/{cell}/'


def parse_table(rows):
    """Строки таблицы (списки ячеек) → [(слово, перевод, транскрипция)].

    В какой колонке что — понимаем по содержимому: английское, транскрипция,
    русское. Порядок колонок любой, лишние колонки (номер, пример) не мешают.
    Пустой список — это не таблица слов.
    """
    result = []
    for row in rows:
        cells = [' '.join(c.split()) for c in row if c and c.strip()]
        ipa = next((c for c in cells if _is_ipa(c)), '')
        english = next((c for c in cells if _LATIN.search(c) and not _CYRILLIC.search(c) and c != ipa), '')
        russian = next((c for c in cells if _CYRILLIC.search(c)), '')
        if not english or english.lower() in _HEADERS or len(english.split()) > 6:
            continue
        result.append((english[:60], russian[:100], _ipa(ipa)[:60] if ipa else ''))
    # Таблица слов — если хотя бы у половины строк есть перевод или транскрипция
    filled = sum(1 for _, ru, ipa in result if ru or ipa)
    return result if result and filled * 2 >= len(result) else []


def table_rows(text):
    """Таблица, вставленная сообщением → строки ячеек. None — это не таблица.

    Из Word и Excel строки приходят с табуляцией. Через «|» — только если в
    строках есть транскрипция: иначе это скорее ответ ИИ («фраза | перевод | …»).
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if sum('\t' in line for line in lines) * 2 >= len(lines):
        return [line.split('\t') for line in lines]
    piped = [[c for c in line.strip().strip('|').split('|')] for line in lines if '|' in line]
    if piped and len(piped) * 2 >= len(lines) and all(len(r) <= 3 for r in piped) \
            and sum(any(_is_ipa(c) for c in r) for r in piped) * 2 >= len(piped):
        return piped
    return None


# ─── Чтение файлов ─────────────────────────────────────────

def _decode(data):
    for encoding in ('utf-8-sig', 'cp1251'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='ignore')


_SRT_JUNK = re.compile(r'^\s*(\d+|\d\d:\d\d:\d\d[,.]\d+\s*-->.*)\s*$')


def _srt_text(text):
    lines = [re.sub(r'<[^>]+>|\{[^}]*\}', '', line) for line in text.splitlines() if not _SRT_JUNK.match(line)]
    return '\n'.join(line.strip() for line in lines if line.strip())


_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _paragraph_text(element):
    parts = []
    for node in element.iter():
        if node.tag == f'{_W}t' and node.text:
            parts.append(node.text)
        elif node.tag == f'{_W}tab':
            parts.append('\t')
        elif node.tag in (f'{_W}br', f'{_W}cr'):
            parts.append('\n')
    return ''.join(parts)


def _docx(data):
    """docx → (абзацы, таблицы). Таблица — список строк, строка — список ячеек."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        info = archive.getinfo('word/document.xml')
    except (zipfile.BadZipFile, KeyError):
        raise FileError('Не получилось открыть .docx — файл повреждён или это другой формат.')
    if info.file_size > MAX_XML:
        raise FileError('Документ слишком большой.')
    body = ElementTree.fromstring(archive.read(info)).find(f'{_W}body')
    paragraphs, tables = [], []
    for child in (body if body is not None else []):
        if child.tag == f'{_W}p':
            paragraphs.append(_paragraph_text(child))
        elif child.tag == f'{_W}tbl':
            tables.append([[' '.join(_paragraph_text(p) for p in cell.iter(f'{_W}p'))
                            for cell in row.findall(f'{_W}tc')]
                           for row in child.iter(f'{_W}tr')])
    return paragraphs, tables


def _pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise FileError('PDF пока не читаю — пришли текст сообщением или файлом .txt / .docx.')
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or '' for page in reader.pages[:MAX_PDF_PAGES]]
    except Exception:
        raise FileError('Не получилось открыть PDF — файл повреждён или защищён паролем.')
    text = '\n'.join(pages)
    if len(text.strip()) < 20:
        raise FileError('В этом PDF нет текста — похоже, это скан или картинка. '
                        'Такие я пока не читаю: пришли текст сообщением.')
    return text


def read(data, filename):
    """Байты файла → Content(текст, пары из таблиц)."""
    ext = kind(filename)
    if ext == '.docx':
        paragraphs, tables = _docx(data)
        pairs = []
        for table in tables:
            found = parse_table(table)
            if found:
                pairs += found                    # таблица слов → готовые пары
            else:                                 # обычная таблица — это просто текст
                paragraphs += ['\t'.join(row) for row in table]
        return Content('\n'.join(p for p in paragraphs if p.strip()), pairs)
    if ext == '.pdf':
        return Content(_pdf(data), [])
    if ext in ('.txt', '.md', '.srt'):
        text = _decode(data)
        if ext == '.srt':
            text = _srt_text(text)
        rows = table_rows(text)                   # txt может оказаться таблицей с табуляцией
        pairs = parse_table(rows) if rows else []
        return Content('' if pairs else text, pairs)
    raise FileError('Пока понимаю файлы .txt, .docx, .pdf и .srt.')
