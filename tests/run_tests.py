"""Прогон бота без Telegram: подменяем сетевой слой и кормим диспетчер апдейтами.

Запуск из папки проекта:   python3 tests/run_tests.py

Тесты работают во временной папке со своей пустой базой — твоя bot_database.db не трогается.
"""
import asyncio
import atexit
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WORKDIR = tempfile.mkdtemp(prefix='english_bot_tests_')
os.chdir(WORKDIR)
# Временную папку убираем за собой: на сервере /tmp лежит в памяти, а в папке — копия словаря
atexit.register(shutil.rmtree, WORKDIR, ignore_errors=True)
if os.path.exists(os.path.join(ROOT, 'dictionary.db')):          # словарь — если он есть в проекте
    shutil.copy(os.path.join(ROOT, 'dictionary.db'), 'dictionary.db')

os.environ['BOT_TOKEN'] = '123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'

from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import (Chat, File, InlineKeyboardMarkup, Message,  # noqa: E402
                           ReplyKeyboardMarkup, User)

UID = 555
BOT_ID = 123456789


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.next_id = 1000
        self.files = {}          # file_id → содержимое файла для bot.download()

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name == 'SendMessage':
            self.next_id += 1
            return Message(message_id=self.next_id, date=datetime.now(),
                           chat=Chat(id=method.chat_id, type='private'), text=method.text)
        if name == 'EditMessageText':
            return Message(message_id=method.message_id, date=datetime.now(),
                           chat=Chat(id=method.chat_id, type='private'), text=method.text)
        if name == 'GetMe':
            return User(id=BOT_ID, is_bot=True, first_name='Bot', username='Allomisha_bot')
        if name == 'GetFile':
            return File(file_id=method.file_id, file_unique_id=method.file_id, file_path=method.file_id)
        return True

    async def stream_content(self, url, *a, **k):
        yield self.files[url.rsplit('/', 1)[1]]

    async def close(self):
        pass


ALLOWED = {'b', 'i', 'u', 's', 'code', 'pre', 'a', 'tg-spoiler'}
TAG = re.compile(r'<(/?)([a-z-]+)[^>]*>')


def check_html(text):
    stack = []
    for closing, tag in TAG.findall(text):
        assert tag in ALLOWED, f'недопустимый тег <{tag}> в: {text!r}'
        if closing:
            assert stack and stack[-1] == tag, f'несбалансированный </{tag}> в: {text!r}'
            stack.pop()
        else:
            stack.append(tag)
    assert not stack, f'незакрытые теги {stack} в: {text!r}'
    rest = TAG.sub('', text)
    assert '<' not in rest and '>' not in rest, f'неэкранированные < > в: {text!r}'
    for amp in re.finditer(r'&', rest):
        assert re.match(r'&(lt|gt|amp|quot);', rest[amp.start():]), f'голый & в: {text!r}'


def check_markup(markup):
    if isinstance(markup, InlineKeyboardMarkup):
        for row in markup.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data.encode()) <= 64, btn.callback_data


uid_counter = [0]


def user():
    return {'id': UID, 'is_bot': False, 'first_name': 'Тест', 'username': 'tester'}


def msg_update(text=None, dice=False):
    uid_counter[0] += 1
    m = {'message_id': uid_counter[0], 'date': int(time.time()),
         'chat': {'id': UID, 'type': 'private'}, 'from': user()}
    if dice:
        m['dice'] = {'emoji': '🎲', 'value': 3}
    else:
        m['text'] = text
    return {'update_id': uid_counter[0], 'message': m}


def inline_query_update(query):
    uid_counter[0] += 1
    return {'update_id': uid_counter[0], 'inline_query': {
        'id': str(uid_counter[0]), 'from': user(), 'query': query, 'offset': ''}}


def inline_cb_update(data, user_id=UID):
    """Нажатие кнопки под сообщением, отправленным через inline-режим (у него нет chat/message)."""
    uid_counter[0] += 1
    return {'update_id': uid_counter[0], 'callback_query': {
        'id': str(uid_counter[0]), 'chat_instance': 'ci', 'data': data, 'inline_message_id': 'imsg',
        'from': {'id': user_id, 'is_bot': False, 'first_name': 'Друг', 'username': f'u{user_id}'}}}


def cb_update(data, message_id):
    uid_counter[0] += 1
    return {'update_id': uid_counter[0], 'callback_query': {
        'id': str(uid_counter[0]), 'from': user(), 'chat_instance': 'ci', 'data': data,
        'message': {'message_id': message_id, 'date': int(time.time()),
                    'chat': {'id': UID, 'type': 'private'},
                    'from': {'id': BOT_ID, 'is_bot': True, 'first_name': 'Bot'}, 'text': 'x'}}}


async def main():
    import english_bot
    from utils import database, scheduler as sch
    session = FakeSession()
    english_bot.bot.session = session
    bot, dp = english_bot.bot, english_bot.dp

    database.init_db()
    sch.scheduler.start()
    # Тесты не должны зависеть от времени суток: по умолчанию «полдень», а не тихие часы
    sch._now = lambda: datetime(2026, 11, 8, 12, 0, tzinfo=sch.TIMEZONE)
    failures = []
    passed = [0]

    async def send(update, label):
        """Отправляет апдейт, проверяет общие правила, возвращает ответы бота."""
        before = len(session.calls)
        await dp.feed_raw_update(bot, update)
        new = session.calls[before:]
        texts = [c for c in new if type(c).__name__ in ('SendMessage', 'EditMessageText')]
        answers = [c for c in new if type(c).__name__ == 'AnswerCallbackQuery']
        is_cb = 'callback_query' in update
        if is_cb:
            assert answers, f'[{label}] на нажатие кнопки нет callback.answer() — крутилка зависнет'
        else:
            assert texts, f'[{label}] БОТ ПРОМОЛЧАЛ'
        for c in texts:
            assert len(c.text) <= 4096, f'[{label}] сообщение длиной {len(c.text)} > 4096'
            check_html(c.text)
            check_markup(c.reply_markup)
        return texts, answers

    async def case(label, fn):
        try:
            await fn()
            passed[0] += 1
            print(f'  ✅ {label}')
        except Exception as e:
            failures.append(label)
            print(f'  ❌ {label}: {e}')

    def last_id():
        sends = [c for c in session.calls if type(c).__name__ == 'SendMessage']
        return session.next_id if sends else 0

    # ───────── сценарии ─────────
    async def t_start():
        texts, _ = await send(msg_update('/start'), 'start')
        assert isinstance(texts[-1].reply_markup, ReplyKeyboardMarkup)
        assert texts[-1].reply_markup.is_persistent
        labels = [b.text for row in texts[-1].reply_markup.keyboard for b in row]
        assert labels == ['🎴 Учить', '📋 Мои слова', '🤖 Через ИИ', '🎵 Песня', '➕ Добавить', 'ℹ️ Помощь'], labels
        assert 'файл' in texts[-1].reply_markup.input_field_placeholder, 'подсказка в поле ввода'
    await case('/start показывает постоянное меню', t_start)

    async def t_study_empty():
        texts, _ = await send(msg_update('🎴 Учить'), 'study empty')
        assert 'пуст' in texts[-1].text and texts[-1].reply_markup is not None
    await case('Учить при пустом словаре → подсказка с кнопкой', t_study_empty)

    async def t_dash_idle():
        texts, _ = await send(msg_update('привет - пока'), 'dash idle')
        assert 'с русского на английский' in texts[-1].text, texts[-1].text
    await case('Текст с дефисом вне режима добавления → бот отвечает (раньше молчал)', t_dash_idle)

    async def t_yes_idle():
        texts, _ = await send(msg_update('да'), 'yes idle')
        assert 'с русского на английский' in texts[-1].text
        texts, _ = await send(cb_update('ru:no', session.next_id), 'ru no')
        assert 'Хорошо' in texts[-1].text
        texts, _ = await send(msg_update('🙂'), 'emoji idle')
        assert isinstance(texts[-1].reply_markup, ReplyKeyboardMarkup)
    await case('Русское вне диалога → «Делаем карточки с русского?», «Нет» → подсказка', t_yes_idle)

    async def t_add():
        texts, _ = await send(msg_update('➕ Добавить'), 'add hint')
        assert 'Как добавить' in texts[-1].text and isinstance(texts[-1].reply_markup, ReplyKeyboardMarkup)
        texts, _ = await send(msg_update('📚 Добавить слова'), 'old add button')
        assert 'Как добавить' in texts[-1].text, 'кнопка старого меню тоже работает'
        texts, _ = await send(msg_update(
            'apple - яблоко, fly-летать, well-known - известный\nrun — бежать, просто текст, , <3 - love & joy'),
            'add words')
        t = texts[-1].text
        assert 'Добавил 5 слов' in t, t
        assert 'просто текст' in t and '&lt;3' in t
        assert database.count_words(UID) == 5
        words = database.get_words(UID)
        pairs = [(w.word, w.translation) for w in words]
        assert ('well-known', 'известный') in pairs and ('run', 'бежать') in pairs, pairs
    await case('Добавление: дефис/тире/без пробелов/перенос строки, кривые пары, спецсимволы', t_add)

    async def t_add_dup():
        texts, _ = await send(msg_update('apple - яблоко, cat - кот'), 'dup')
        assert 'Уже были' in texts[-1].text and database.count_words(UID) == 6
    await case('Повторное слово не дублируется', t_add_dup)

    async def t_add_second_without_yes():
        texts, _ = await send(msg_update('dog - собака'), 'second batch')
        assert 'Добавил 1 слово' in texts[-1].text
    await case('Вторая порция слов без «Да» — принимается (раньше молчал)', t_add_second_without_yes)

    async def t_add_sticker():
        texts, _ = await send(msg_update(dice=True), 'sticker')
        assert 'Не понял' in texts[-1].text
    await case('Стикер/кубик → подсказка и меню', t_add_sticker)

    async def t_add_huge():
        big = ', '.join(f'word{i} - слово{i}' for i in range(300))
        texts, _ = await send(msg_update(big), 'huge paste')
        assert '…и ещё 290' in texts[-1].text
        assert database.count_words(UID) == 307
    await case('Вставка 300 пар за раз → ответ не превышает лимит', t_add_huge)

    async def t_menu_escape():
        texts, _ = await send(msg_update('📋 Мои слова'), 'my words')
        assert 'Мои слова</b> — 307' in texts[-1].text, texts[-1].text
        texts, _ = await send(msg_update('cow - корова'), 'add after')
        data = [b.callback_data for row in texts[-1].reply_markup.inline_keyboard for b in row]
        assert data == ['study:menu', 'voc:page:0'] and 'Мои слова: 308' in texts[-1].text, data
    await case('После добавления — кнопки «Учить» и «Мои слова», без режима и «Готово»', t_menu_escape)

    async def t_vocab_pages():
        texts, _ = await send(msg_update('/words'), 'vocab')
        mid = session.next_id
        kb = texts[-1].reply_markup.inline_keyboard
        assert kb[0][1].text == '1 / 16', kb[0][1].text
        texts, _ = await send(cb_update('voc:page:15', mid), 'vocab last page')
        assert '308.' in texts[-1].text
        texts, _ = await send(cb_update('voc:page:99', mid), 'vocab bad page')
        assert '308.' in texts[-1].text
        await send(cb_update('noop', mid), 'noop')
    await case('Словарь по страницам (308 слов → 16 страниц), лишняя страница не ломает', t_vocab_pages)

    def kb_first(msg):
        return msg.reply_markup.inline_keyboard[0][0].text

    async def t_study_flow():
        database.delete_words(UID)
        database.add_words(UID, [database.Word('apple', 'яблоко', '/ˈæp.əl/', 'An apple a day.'),
                                 database.Word('cat', 'кот', '', ''),
                                 database.Word('dog', 'собака', '', '')])
        texts, _ = await send(msg_update('🎴 Учить'), 'study menu')
        mid = session.next_id
        assert '🎴 <b>Учить</b> — 3 слова' == texts[-1].text, texts[-1].text
        kb = texts[-1].reply_markup.inline_keyboard
        assert [(b.text, b.callback_data) for row in kb for b in row] == [
            ('▶️ Начать', 'study:cards'), ('🔀 Вперемешку', 'study:shuffle'),
            ('🔔 Рассылка: выкл', 'study:mailing')], kb
        texts, _ = await send(cb_update('study:cards', mid), 'cards')
        assert 'Карточка 1 из 3' in texts[-1].text and '🇷🇺' not in texts[-1].text
        front = [b.callback_data for row in texts[-1].reply_markup.inline_keyboard for b in row]
        assert front == ['study:reveal', 'study:stop'], 'на лице только «Показать» и «Закончить»'
        texts, _ = await send(cb_update('study:reveal', mid), 'reveal')
        assert '🇷🇺' in texts[-1].text
        await send(cb_update('study:know', mid), 'know')
        texts, _ = await send(cb_update('study:dontknow', mid), 'skip without reveal')
        assert 'Карточка 3 из 3' in texts[-1].text
        await send(cb_update('study:reveal', mid), 'reveal3')
        texts, _ = await send(cb_update('study:know', mid), 'last')
        assert 'завершена' in texts[-1].text and 'Знаю: 2' in texts[-1].text, texts[-1].text
        texts, _ = await send(cb_update('study:menu', mid), 'back to choice')
        assert '<b>Учить</b>' in texts[-1].text and kb_first(texts[-1]) == '▶️ Незнакомые (1)', kb_first(texts[-1])
    await case('Учить → «▶️ Начать» → карточки: показать / знаю → итог → «▶️ Незнакомые»', t_study_flow)

    async def t_study_stale():
        texts, _ = await send(msg_update('🎴 Учить'), 'study again')
        await send(cb_update('study:cards', session.next_id), 'cards again')
        _, answers = await send(cb_update('study:reveal', 1), 'old card')
        assert answers[-1].show_alert and 'устарела' in answers[-1].text
        mid = session.next_id
        texts, _ = await send(cb_update('study:stop', mid), 'stop')
        assert 'остановлена' in texts[-1].text
        _, answers = await send(cb_update('study:know', mid), 'after stop')
        assert answers[-1].show_alert
    await case('Старая карточка и нажатия после остановки → «устарела», без падений', t_study_stale)

    async def t_study_text():
        await send(msg_update('🎴 Учить'), 'study for text')
        await send(cb_update('study:cards', session.next_id), 'cards for text')
        texts, _ = await send(msg_update('что-то пишу'), 'text in study')
        assert 'идёт тренировка' in texts[-1].text
    await case('Текст во время тренировки → подсказка', t_study_text)

    async def t_mailing():
        texts, _ = await send(msg_update('🎴 Учить'), 'study menu for mailing')
        mid = session.next_id
        texts, _ = await send(cb_update('study:mailing', mid), 'open mailing')
        assert '🔔 <b>Рассылка</b>' in texts[-1].text and 'выключена' in texts[-1].text
        assert texts[-1].reply_markup.inline_keyboard[-1][0].callback_data == 'study:menu', 'нет кнопки Назад'
        texts, _ = await send(cb_update('mail:30', mid), 'mail on')
        assert 'каждые 30 минут' in texts[-1].text
        assert sch.scheduler.get_job(f'mailing_{UID}') and database.get_mailing(UID) == 30
        before = len(session.calls)
        await sch.send_next_word(bot, UID)
        sent = session.calls[before:][-1]
        check_html(sent.text)
        assert '<tg-spoiler>' in sent.text, sent.text
        texts, _ = await send(cb_update('study:menu', mid), 'back to choice')
        assert '🔔 Рассылка: каждые 30 минут' in [b.text for row in texts[-1].reply_markup.inline_keyboard for b in row]
        texts, _ = await send(cb_update('study:mailing', mid), 'reopen mailing')
        texts, _ = await send(cb_update('mail:off', mid), 'mail off')
        assert sch.scheduler.get_job(f'mailing_{UID}') is None and database.get_mailing(UID) == 0
        _, answers = await send(cb_update('mail:999', mid), 'mail bad')
    await case('Учить → По расписанию → 30 мин → слово со спойлером → статус на экране выбора → выкл', t_mailing)

    async def t_mailing_legacy():
        texts, _ = await send(msg_update('🔔 Рассылка'), 'old mailing button')
        assert '🔔 <b>Рассылка</b>' in texts[-1].text
        texts, _ = await send(msg_update('/mailing'), 'mailing command')
        assert '🔔 <b>Рассылка</b>' in texts[-1].text
        texts, _ = await send(cb_update('study:start', 77), 'old study:start button')
        assert '<b>Учить</b>' in texts[-1].text
        texts, _ = await send(msg_update('/vocabulary'), 'old vocabulary command')
        assert 'Мои слова' in texts[-1].text
    await case('Кнопки и команды старой версии ведут в новые экраны', t_mailing_legacy)

    async def t_mailing_restore():
        database.set_mailing(UID, 15)
        sch.restore_mailings(bot)
        assert sch.scheduler.get_job(f'mailing_{UID}')
        sch.stop_mailing(UID)
    await case('Рассылка восстанавливается из базы после перезапуска', t_mailing_restore)

    async def t_mailing_index():
        sch._positions[UID] = 50          # позиция больше числа слов
        before = len(session.calls)
        await sch.send_next_word(bot, UID)
        assert session.calls[before:], 'рассылка упала на выходе за границы'
    await case('Рассылка не падает, если слов стало меньше позиции', t_mailing_index)

    async def t_clear():
        database.set_mailing(UID, 5)
        sch._schedule(bot, UID, 5)
        texts, _ = await send(msg_update('📋 Мои слова'), 'vocab for clear')
        mid = session.next_id
        texts, _ = await send(cb_update('voc:clear', mid), 'ask clear')
        assert 'нельзя отменить' in texts[-1].text
        texts, _ = await send(cb_update('voc:clear:yes', mid), 'clear yes')
        assert database.count_words(UID) == 0 and database.get_mailing(UID) == 0
        assert sch.scheduler.get_job(f'mailing_{UID}') is None
    await case('Очистка словаря с подтверждением + выключение рассылки', t_clear)

    async def t_restart_stale():
        _, answers = await send(cb_update('study:reveal', 42), 'stale after restart')
        assert answers[-1].show_alert
        _, answers = await send(cb_update('garbage', 42), 'garbage')
        assert answers[-1].show_alert
    await case('Кнопки после перезапуска бота → «устарела»', t_restart_stale)

    async def t_old_buttons():
        texts, _ = await send(msg_update('📖 Учить'), 'old label')
        assert isinstance(texts[-1].reply_markup, ReplyKeyboardMarkup)
    await case('Кнопка старой версии «📖 Учить» → бот присылает новое меню', t_old_buttons)

    async def t_help_and_commands():
        for t in ('/ai', '/song', '/help', 'ℹ️ Помощь', '/add', '/study', '/mailing', '/words', '/unknown', '/start'):
            await send(msg_update(t), t)
        await english_bot.bot.set_my_commands(english_bot.COMMANDS)
    await case('Все команды отвечают', t_help_and_commands)

    async def t_add_done():
        await send(msg_update('a - б'), 'one word')
        texts, _ = await send(cb_update('add:done', 99), 'old done button')
        assert '<b>Учить</b>' in texts[-1].text, 'старая «Готово» ведёт в «Учить»'
        done_id = session.next_id
        texts, _ = await send(cb_update('study:mailing', done_id), 'mailing from study')
        texts, _ = await send(cb_update('mail:15', done_id), 'mail 15')
        assert database.get_mailing(UID) == 15 and sch.scheduler.get_job(f'mailing_{UID}')
        sch.stop_mailing(UID)
    await case('Старая кнопка «Готово» → «Учить» → включить рассылку оттуда', t_add_done)

    def cb_list(msg):
        return [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row]

    async def t_mailing_buttons():
        database.delete_words(UID)
        database.add_words(UID, [database.Word('apple', 'яблоко', '', '')])
        sch.start_mailing(bot, UID, 5)
        before = len(session.calls)
        await sch.send_next_word(bot, UID)
        sent = session.calls[before:][-1]
        assert cb_list(sent) == ['mail:stop', 'mail:settings'], cb_list(sent)
        word_msg = session.next_id
        texts, _ = await send(cb_update('mail:settings', word_msg), 'mailing settings from word')
        assert '🔔 <b>Рассылка</b>' in texts[-1].text and 'включена' in texts[-1].text
        _, answers = await send(cb_update('mail:stop', word_msg), 'stop from word')
        assert answers[-1].show_alert and 'остановлена' in answers[-1].text
        assert sch.scheduler.get_job(f'mailing_{UID}') is None and database.get_mailing(UID) == 0
    await case('Под словом рассылки кнопки «Остановить» и «Как часто»', t_mailing_buttons)

    async def start_cards(words):
        database.delete_words(UID)
        database.add_words(UID, [database.Word(w, t, '', '') for w, t in words])
        await send(msg_update('🎴 Учить'), 'study menu')
        mid = session.next_id
        await send(cb_update('study:cards', mid), 'cards')
        return mid

    async def t_card_order():
        import random
        words = [('passed upon', 'встретились на'), ('spoke of', 'говорили о'), ('came as a surprise', 'стало неожиданностью'),
                 ('into his eyes', 'ему в глаза'), ('died alone', 'умер в одиночестве'), ('lost control', 'потерял контроль')]
        database.delete_words(UID)
        database.add_words(UID, [database.Word(w, t, '', '') for w, t in words])

        async def run(mode):
            await send(msg_update('🎴 Учить'), 'study menu')
            mid = session.next_id
            texts, _ = await send(cb_update(f'study:{mode}', mid), f'cards {mode}')
            seen = []
            for _ in words:
                seen.append(texts[-1].text.split('<b>')[1].split('</b>')[0])
                texts, _ = await send(cb_update('study:dontknow', mid), 'next')
            return seen, cb_list(texts[-1])

        seen, done = await run('cards')
        assert seen == [w for w, _ in words], f'по порядку: {seen}'
        assert done[0] == 'study:cards', '«Ещё раз» — снова по порядку'
        random.seed(1)
        seen, done = await run('shuffle')
        assert sorted(seen) == sorted(w for w, _ in words) and seen != [w for w, _ in words], seen
        assert done[0] == 'study:shuffle', '«Ещё раз» — снова вперемешку'
    await case('Карточки по порядку — как в тексте/песне; вперемешку — перемешаны; «Ещё раз» помнит режим', t_card_order)

    async def t_card_edit():
        mid = await start_cards([('found', 'основывать'), ('cat', 'кот')])
        texts, _ = await send(cb_update('study:reveal', mid), 'reveal')
        assert 'study:edit' in cb_list(texts[-1]) and 'study:delete' in cb_list(texts[-1])
        texts, _ = await send(cb_update('study:edit', mid), 'edit')
        assert 'Изменить карточку' in texts[-1].text and 'основывать' in texts[-1].text
        assert cb_list(texts[-1]) == ['we:word', 'we:tr', 'we:ipa', 'we:ex', 'we:exru', 'we:back'], cb_list(texts[-1])
        texts, _ = await send(cb_update('we:tr', mid), 'edit translation')
        assert 'Напиши перевод' in texts[-1].text and 'основывать' in texts[-1].text
        texts, _ = await send(msg_update('нашёл'), 'new translation')
        assert 'Исправил' in texts[-1].text and '🇷🇺 нашёл' in texts[-1].text, texts[-1].text
        m2 = session.next_id
        await send(cb_update('we:ex', m2), 'edit example')
        await send(msg_update('I found my keys.'), 'new example')
        m3 = session.next_id
        assert [(w.translation, w.example) for w in database.get_words(UID)][0] == ('нашёл', 'I found my keys.')
        texts, _ = await send(cb_update('we:back', m3), 'back to card')
        assert 'Карточка 1 из 2' in texts[-1].text and 'нашёл' in texts[-1].text and 'I found my keys.' in texts[-1].text
        texts, _ = await send(cb_update('study:know', m3), 'know after edit')
        assert 'Карточка 2 из 2' in texts[-1].text, 'тренировка продолжается'
        await send(cb_update('study:stop', m3), 'stop')
    await case('Карточка: «✏️ Изменить» → перевод и пример сохраняются → «К карточке», тренировка продолжается', t_card_edit)

    async def t_card_edit_cancel_and_menu():
        mid = await start_cards([('apple', 'яблоко'), ('cat', 'кот')])
        await send(cb_update('study:reveal', mid), 'reveal')
        await send(cb_update('study:edit', mid), 'edit')
        await send(cb_update('we:tr', mid), 'ask translation')
        _, answers = await send(cb_update('we:cancel', session.next_id), 'cancel edit')
        assert 'Оставил' in answers[-1].text
        texts, _ = await send(cb_update('we:back', mid), 'back to card')
        texts, _ = await send(cb_update('study:know', mid), 'continue after cancel')
        assert 'Карточка 2 из 2' in texts[-1].text
        await send(cb_update('study:reveal', mid), 'reveal 2')
        await send(cb_update('study:edit', mid), 'edit 2')
        await send(cb_update('we:tr', mid), 'ask translation 2')
        texts, _ = await send(msg_update('📋 Мои слова'), 'menu during edit')
        assert 'Мои слова' in texts[-1].text, 'кнопка меню не должна стать переводом'
        assert {w.translation for w in database.get_words(UID)} == {'яблоко', 'кот'}
    await case('«Не менять» и кнопки меню во время правки', t_card_edit_cancel_and_menu)

    async def t_card_delete():
        mid = await start_cards([('apple', 'яблоко'), ('cat', 'кот')])
        await send(cb_update('study:reveal', mid), 'reveal')
        texts, answers = await send(cb_update('study:delete', mid), 'delete first')
        assert 'Удалил' in answers[-1].text and 'Карточка 1 из 1' in texts[-1].text
        assert database.count_words(UID) == 1
        await send(cb_update('study:reveal', mid), 'reveal last')
        texts, _ = await send(cb_update('study:delete', mid), 'delete last')
        assert 'завершена' in texts[-1].text and database.count_words(UID) == 0
    await case('Карточка: «Удалить» убирает слово и показывает следующее', t_card_delete)

    from utils import dictionary as dic
    from utils import russian as rus

    def kb_data(msg):
        return [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row]

    def kb_text(msg):
        return [b.text for row in msg.reply_markup.inline_keyboard for b in row]

    LISTED = re.compile(r'^\d+\. (?:🇷🇺 )?(?:<b>)?(?P<a>.+?)(?:</b>)?(?P<ipa> /[^/]*/)? — (?:<b>)?(?P<b>.+?)(?:</b>)?(?P<ipa2> /[^/]*/)?$')

    def listed(msg):
        """Строки списка на экране «Нашёл в тексте» → [(первое, второе, транскрипция)]."""
        result = []
        for line in msg.text.splitlines():
            m = LISTED.match(line)
            if m:
                result.append((m['a'], m['b'], (m['ipa'] or m['ipa2'] or '').strip()))
        return result

    async def t_card_fields():
        database.delete_words(UID)
        database.add_words(UID, [database.Word('apple', 'яблоко', '/ˈæp.əl/', 'An apple a day.')])
        await send(msg_update('🎴 Учить'), 'study menu')
        mid = session.next_id
        texts, _ = await send(cb_update('study:cards', mid), 'card')
        assert '/ˈæp.əl/' in texts[-1].text and 'яблоко' not in texts[-1].text
        texts, _ = await send(cb_update('study:reveal', mid), 'reveal')
        assert 'яблоко' in texts[-1].text and 'An apple a day.' in texts[-1].text
        await send(cb_update('study:stop', mid), 'stop')
    await case('Карточка: транскрипция спереди, перевод и пример — после «Показать»', t_card_fields)

    async def t_migration():
        import sqlite3
        with sqlite3.connect(database.DB) as conn:
            conn.execute("INSERT INTO words (user_id, word, translation) VALUES (?, 'old - старый', '')", (UID,))
        database.init_db()
        assert ('old', 'старый') in [(w.word, w.translation) for w in database.get_words(UID)]
    await case('Старые слова «apple - яблоко» переносятся в новые поля', t_migration)

    if not dic.available():
        print('  ⚠️  dictionary.db нет в проекте — тесты словаря пропущены')
    else:
        async def t_lookup_found():
            database.delete_words(UID)
            texts, _ = await send(msg_update('found'), 'lookup found')
            mid = session.next_id
            labels = kb_text(texts[-1])
            target = 'нашёл' if rus.available() else 'найти'
            assert labels[0].startswith(target) and 'от find' in labels[0], labels
            assert any(l.startswith('основ') or l.startswith('учред') for l in labels), labels
            idx = 0
            texts, answers = await send(cb_update(f'lk:{idx}', mid), f'pick {target}')
            assert 'Добавил' in texts[-1].text and f'✅ {target}' in kb_text(texts[-1])[idx]
            assert ('found', target) in [(w.word, w.translation) for w in database.get_words(UID)]
            other = next(i for i, l in enumerate(labels) if 'от find' not in l)
            texts, _ = await send(cb_update(f'lk:{other}', mid), 'pick own meaning')
            assert len(database.get_words(UID)) == 2, 'второе значение тоже добавляется'
            _, answers = await send(cb_update(f'lk:{idx}', mid), 'pick again')
            assert 'уже добавлено' in answers[-1].text
            await send(cb_update('lk:close', mid), 'close')
            _, answers = await send(cb_update('lk:0', mid), 'after close')
            assert answers[-1].show_alert, 'после закрытия кнопки устаревают'
        await case('Слово «found» → первым «нашёл · от find», есть и своё значение, можно добавить оба', t_lookup_found)

        async def t_inflection():
            if not rus.available():
                print('  ⚠️  pymorphy3 не установлен — проверка русских форм пропущена')
                return
            expect = {'went': 'шёл', 'found': 'нашёл', 'saw': 'видел', 'keys': 'ключи',
                      'goes': 'идёт', 'children': 'дети'}
            got = {w: dic.meanings(w)[0].translation for w in expect}
            assert got == expect, got
            assert dic.meanings('took')[0].translation in ('взял', 'брал')
            assert dic.meanings('put up with')[0].translation.startswith('терпеть')
            labels = [m.translation for m in dic.meanings('went')]
            assert not any(l.startswith('очеред') for l in labels), 'у формы глагола нет существительных'
            found = {i.shown: i.translation for i in dic.extract('I turned the music off and went to bed.')}
            assert found == {'turned off': 'выключил', 'went to bed': 'лёг спать'}, found
        await case('Перевод в форме слова: went → шёл, found → нашёл, keys → ключи, turned off → выключил', t_inflection)

        async def t_no_be_phrases():
            assert dic.extract('I am there for you.') == []
            assert any(i.shown == 'has to' for i in dic.extract('He has to go.'))
        await case('«I am there» не превращается в идиому «быть рядом», «has to» находится', t_no_be_phrases)

        async def t_lookup_phrase():
            texts, _ = await send(msg_update('Looking For'), 'lookup phrase')
            assert any(l.startswith('искать') for l in kb_text(texts[-1])), kb_text(texts[-1])
        await case('Выражение в форме из текста «Looking For» → «искать»', t_lookup_phrase)

        async def t_lookup_own():
            texts, _ = await send(msg_update('qwertyzz'), 'lookup unknown')
            mid = session.next_id
            assert 'Не нашёл' in texts[-1].text and 'lk:own' in kb_data(texts[-1])
            texts, _ = await send(cb_update('lk:own', mid), 'own')
            assert 'Напиши перевод' in texts[-1].text
            texts, _ = await send(msg_update('📋 Мои слова'), 'menu during own')
            assert 'Мои слова' in texts[-1].text, 'кнопка меню не должна стать переводом'
            await send(cb_update('lk:own', mid), 'own again')
            texts, _ = await send(msg_update('моё слово'), 'own translation')
            assert 'Добавил' in texts[-1].text
            assert ('qwertyzz', 'моё слово') in [(w.word, w.translation) for w in database.get_words(UID)]
            texts, _ = await send(msg_update('ещё текст'), 'after own')
            assert 'с русского' in texts[-1].text, 'после своего перевода бот возвращается в обычный режим'
        await case('Нет в словаре → «Свой перевод»; кнопки меню при этом работают', t_lookup_own)

        async def t_own_in_add_mode():
            await send(msg_update('zzqqxx'), 'unknown word')
            await send(cb_update('lk:own', session.next_id), 'own')
            texts, _ = await send(msg_update('перевод'), 'own translation')
            assert kb_data(texts[-1]) == ['study:menu', 'voc:page:0'], 'после своего перевода — «Учить» и «Мои слова»'
        await case('«Свой перевод» → добавил → кнопки «Учить» и «Мои слова»', t_own_in_add_mode)

        async def t_extract():
            database.delete_words(UID)
            database.add_words(UID, [database.Word('couch', 'диван', '', '')])
            text = ("I've been looking for my keys all morning and finally found them under the couch. "
                    "I turned the music off and went to bed.")
            texts, _ = await send(msg_update(text), 'extract')
            mid = session.next_id
            items = {a: (b, ipa) for a, b, ipa in listed(texts[-1])}
            assert items['looking for'][0].startswith('искать'), items
            assert items['looking for'][1] == '/ˈlʊkɪŋ fɔː/', 'транскрипция у выражения'
            assert 'turned off' in items and 'went to bed' in items, items
            assert 'couch' not in items, 'слова из словаря не предлагаются'
            assert any(l.endswith('looking for') for l in kb_text(texts[-1])), kb_text(texts[-1])
            assert 'уже есть в «Моих словах»' in texts[-1].text
            assert f'✅ Добавить ({len(items)})' in kb_text(texts[-1]), 'все отмечены сразу'
            texts, _ = await send(cb_update('tx:all', mid), 'unselect all')
            assert '✅ Добавить (0)' in kb_text(texts[-1])
            _, answers = await send(cb_update('tx:add', mid), 'add nothing')
            assert 'Сначала отметь' in answers[-1].text
            await send(cb_update('tx:0', mid), 'toggle 0')
            texts, _ = await send(cb_update('tx:1', mid), 'toggle 1')
            assert '✅ Добавить (2)' in kb_text(texts[-1])
            texts, _ = await send(cb_update('tx:add', mid), 'add 2')
            assert 'Добавил 2 слова' in texts[-1].text, texts[-1].text
            words = database.get_words(UID)
            looking = next(w for w in words if w.word == 'looking for')
            assert looking.translation.startswith('искать') and 'keys all morning' in looking.example, looking
            _, answers = await send(cb_update('tx:0', mid), 'after add')
            assert answers[-1].show_alert
        await case('Текст → нашёл выражения (и разорванное turned … off), галочки → добавил с примером', t_extract)

        async def t_extract_all_and_simple():
            texts, _ = await send(msg_update('Tomorrow I will figure out the weekend plan, she said.'), 'extract 2')
            mid = session.next_id
            n = sum(1 for d in kb_data(texts[-1]) if d.startswith('tx:') and d[3:].isdigit())
            assert f'✅ Добавить ({n})' in kb_text(texts[-1]) and '☐ Снять все' in kb_text(texts[-1])
            texts, _ = await send(cb_update('tx:all', mid), 'unselect all')
            texts, _ = await send(cb_update('tx:all', mid), 'select all')
            assert f'✅ Добавить ({n})' in kb_text(texts[-1])
            texts, _ = await send(cb_update('tx:cancel', mid), 'cancel')
            assert 'ничего не добавил' in texts[-1].text
            texts, _ = await send(msg_update('We like it here, but the old house is small.'), 'only simple words')
            assert 'Не нашёл в тексте новых слов' in texts[-1].text
        await case('Текст: «Выбрать все», «Отмена», текст из одних простых слов', t_extract_all_and_simple)

        async def t_prepositions():
            past = rus.available()
            first = lambda w: dic.meanings(w)[0].translation
            assert first('wait for') == 'ждать (кого-то, что-то)', first('wait for')
            assert first('afraid of') == 'бояться (чего-то)'
            assert first('look at') == 'смотреть на', 'а не редкое «светить» из Wiktionary'
            assert first('talk about') == 'говорить о', 'а не «вот это»'
            assert first('interested in') == 'интересующийся (чем-то)'
            found = {i.shown: i.translation for i in dic.extract(
                'I waited for him. It depends on you. She is afraid of dogs. He has to go. '
                'There are many reasons for that.')}
            assert found.get('waited for') == ('ждал (кого-то, что-то)' if past else 'ждать (кого-то, что-то)'), found
            assert found.get('depends on') == ('зависит от' if past else 'зависеть от'), found
            assert found.get('afraid of') == 'бояться (чего-то)', found
            assert found.get('has to') == 'должен, приходится (сделать)', found
            assert found.get('reasons for') == ('причины (чего-то)' if past else 'причина (чего-то)'), found
        await case('Сочетания с предлогами: waited for → ждал (кого-то), depends on → зависит от, afraid of', t_prepositions)

        async def t_short_phrase():
            texts, _ = await send(msg_update('waiting for people to'), 'short phrase not in dictionary')
            assert 'Нашёл в тексте' in texts[-1].text, texts[-1].text
            assert any(a == 'waiting for' and b.startswith('ждать') for a, b, _ in listed(texts[-1])), texts[-1].text
            await send(cb_update('tx:cancel', session.next_id), 'cancel')
            texts, _ = await send(msg_update('waiting for'), 'phrase itself')
            assert kb_text(texts[-1])[0].startswith('ждать (кого-то'), kb_text(texts[-1])
            texts, _ = await send(msg_update('qwertyzz zzqq'), 'nothing at all')
            assert 'Не нашёл' in texts[-1].text and 'lk:own' in kb_data(texts[-1])
        await case('Короткая фраза, которой нет целиком, разбирается по частям: «waiting for people to»', t_short_phrase)

        async def t_ai_from_screens():
            text = "I've been looking for my keys all morning and finally found them under the couch."
            texts, _ = await send(msg_update(text), 'extract for AI')
            assert 'tx:ai' in cb_list(texts[-1])
            texts, _ = await send(cb_update('tx:ai', session.next_id), 'AI from text screen')
            assert '<pre>' in texts[-1].text and 'finally found them' in texts[-1].text
            texts, _ = await send(msg_update('serendipityzz'), 'lookup for AI')
            texts, _ = await send(cb_update('lk:ai', session.next_id), 'AI from lookup')
            assert 'serendipityzz' in texts[-1].text
            texts, _ = await send(msg_update('We like it here, but the old house is small.'), 'nothing found')
            assert 'tx:ai' in cb_list(texts[-1]), 'даже если словарь ничего не нашёл, можно через ИИ'
        await case('«🤖 Через ИИ» на экранах «Нашёл в тексте», «Не нашёл» и «Выбери значение»', t_ai_from_screens)

        async def inline_answer(query):
            before = len(session.calls)
            await dp.feed_raw_update(bot, inline_query_update(query))
            answers = [c for c in session.calls[before:] if type(c).__name__ == 'AnswerInlineQuery']
            assert answers, f'на inline-запрос «{query}» нет ответа'
            return answers[-1]

        async def t_inline_word():
            database.delete_words(UID)
            ans = await inline_answer('ubiquitous')
            assert ans.results[0].title == 'ubiquitous — вездесущий', ans.results[0].title
            assert ans.button.start_parameter.startswith('q_')
            card_data = ans.results[0].reply_markup.inline_keyboard[0][0].callback_data
            assert card_data.startswith('sh:') and len(card_data.encode()) <= 64
            assert 'вездесущий' in ans.results[0].input_message_content.message_text
            # друг (новый пользователь) нажимает «➕ В мой словарь»
            _, answers = await send(inline_cb_update(card_data, user_id=777), 'friend adds')
            assert answers[-1].show_alert and 'Добавил' in answers[-1].text and '@Allomisha_bot' in answers[-1].text
            assert [w.word for w in database.get_words(777)] == ['ubiquitous']
            _, answers = await send(inline_cb_update(card_data, user_id=777), 'friend again')
            assert 'уже есть' in answers[-1].text
            _, answers = await send(inline_cb_update(card_data), 'me')
            assert '@Allomisha_bot' not in answers[-1].text, 'знакомому пользователю не подсказываем запуск'
            assert [w.word for w in database.get_words(UID)] == ['ubiquitous']
            _, answers = await send(inline_cb_update('sh:0000000000000000'), 'unknown card')
            assert answers[-1].show_alert
        await case('Inline: «@бот ubiquitous» → перевод; «➕ В мой словарь» добавляет тому, кто нажал', t_inline_word)

        async def t_inline_phrase():
            database.delete_words(UID)
            ans = await inline_answer("I've been looking for my keys all morning")
            titles = [r.title for r in ans.results]
            assert any(t.startswith('looking for — искать') for t in titles), titles
            assert any(t.startswith('keys — ключи') for t in titles) or not rus.available(), titles
            text_id = ans.button.start_parameter
            texts, _ = await send(msg_update(f'/start {text_id}'), 'open bot from inline button')
            assert any('Слова из фразы' in t.text for t in texts) and 'Нашёл в тексте' in texts[-1].text
            ans = await inline_answer('')
            assert ans.button.start_parameter == 'inline_help' and ans.results == []
            texts, _ = await send(msg_update('/start inline_help'), 'inline help')
            assert 'в любом чате' in texts[-1].text.lower()
            ans = await inline_answer('qwertyzzz')
            assert ans.results == [] and 'Не нашёл' in ans.button.text
        await case('Inline: фраза → слова из неё; «📥» открывает бота с галочками; пустой запрос → подсказка', t_inline_phrase)

        async def t_caption():
            uid_counter[0] += 1
            upd = {'update_id': uid_counter[0], 'message': {
                'message_id': uid_counter[0], 'date': int(time.time()), 'chat': {'id': UID, 'type': 'private'},
                'from': user(), 'caption': 'ubiquitous',
                'photo': [{'file_id': 'x', 'file_unique_id': 'y', 'width': 1, 'height': 1}]}}
            texts, _ = await send(upd, 'photo with caption')
            assert any(l.startswith('вездесущий') for l in kb_text(texts[-1]))
        await case('Пост с картинкой: слово берётся из подписи', t_caption)

    async def t_lowercase():
        database.delete_words(UID)
        await send(msg_update('Apple - Яблоко, CAT - кот'), 'pairs with capitals')
        words = {w.word for w in database.get_words(UID)}
        assert words == {'apple', 'cat'}, words
        await send(msg_update('apple - яблоко'), 'same word lowercase')
        assert database.count_words(UID) == 2, 'Apple и apple — одно слово'
        if dic.available():
            await send(msg_update('London - Лондон'), 'proper noun')
            assert 'London' in {w.word for w in database.get_words(UID)}, 'имена собственные — с заглавной'
            texts, _ = await send(msg_update('Tomorrow'), 'lookup capitalized')
            assert '<b>tomorrow</b>' in texts[-1].text
            await send(cb_update('lk:0', session.next_id), 'pick')
            assert 'tomorrow' in {w.word for w in database.get_words(UID)}
    await case('Слова сохраняются маленькими буквами, кроме имён собственных (London)', t_lowercase)

    async def t_lowercase_migration():
        import sqlite3
        database.delete_words(UID)
        with sqlite3.connect(database.DB) as conn:
            conn.executemany("INSERT INTO words (user_id, word, translation) VALUES (?, ?, ?)",
                             [(UID, 'Dog', 'собака'), (UID, 'dog', 'собака'), (UID, 'Bird', 'птица')])
        database.init_db()
        assert sorted((w.word, w.translation) for w in database.get_words(UID)) == \
            [('bird', 'птица'), ('dog', 'собака')], database.get_words(UID)
    await case('Уже добавленные слова переводятся в нижний регистр, дубли склеиваются', t_lowercase_migration)

    async def t_no_archaic():
        if not dic.available():
            return
        assert dic.meanings('art')[0].translation == 'искусство'
        assert all(i.translation != 'быть' for i in dic.extract('I am interested in art.'))
    await case('Старинные формы не мешают: art → искусство, а не «быть» (thou art)', t_no_archaic)

    async def t_quiet_hours():
        from datetime import datetime as dt
        database.delete_words(UID)
        database.add_words(UID, [database.Word('apple', 'яблоко', '', '')])
        saved = sch._now
        try:
            for hour, minute, expect in ((23, 0, False), (3, 0, False), (8, 59, False), (9, 0, True), (21, 59, True)):
                sch._now = lambda: dt(2026, 11, 8, hour, minute, tzinfo=sch.TIMEZONE)
                before = len(session.calls)
                await sch.send_next_word(bot, UID)
                sent = any(type(c).__name__ == 'SendMessage' for c in session.calls[before:])
                assert sent == expect, f'{hour:02}:{minute:02} — ожидалось {"отправить" if expect else "молчать"}'
        finally:
            sch._now = saved
    await case('Тихие часы: с 22:00 до 9:00 по Москве рассылка молчит', t_quiet_hours)

    from utils import ai_prompt

    MESSY_AI_ANSWER = """Вот карточки по твоему тексту:
```text
1. **looking for** | искал (кого-то, что-то) | I've been looking for my keys. | Я искал ключи.
| фраза | перевод | предложение | перевод предложения |
|---|---|---|---|
| put up with | терпеть (кого-то, что-то) | I can't put up with it. | Я не могу это терпеть. |
- Keys | ключи | My keys are here. | Мои ключи здесь.
looking for | дубль | x | y
```
Надеюсь, помогло!"""

    async def t_ai_parse():
        prompt = ai_prompt.build_prompt('I was waiting for you.')
        assert 'I was waiting for you.' in prompt and 'Не больше 40 карточек' in prompt
        assert 'фраза | перевод | предложение | перевод предложения' in prompt
        assert prompt.rstrip().endswith('Ответь только блоком кода с карточками.')
        long_prompt = ai_prompt.build_prompt('word ' * 3000, max_cards=60)
        assert long_prompt.count('word') == 3000 and 'Не больше 60 карточек' in long_prompt, 'текст целиком'
        assert ai_prompt.is_prompt(prompt) and not ai_prompt.is_prompt(MESSY_AI_ANSWER)
        cards = ai_prompt.parse_answer(MESSY_AI_ANSWER)
        assert [c.phrase for c in cards] == ['looking for', 'put up with', 'Keys'], cards
        assert cards[0].sentence_ru == 'Я искал ключи.' and cards[1].translation.startswith('терпеть')
        assert ai_prompt.parse_answer('apple - яблоко, cat - кот') == [], 'обычные пары — не ответ ИИ'
        assert ai_prompt.parse_answer('Просто текст без черт') == []
    await case('Ответ ИИ разбирается даже с нумерацией, **жирным**, таблицей и лишним текстом', t_ai_parse)

    ONE_LINE_ANSWER = (  # реальный ответ ChatGPT, который пришёл одним абзацем
        "passed upon | встретились на | We passed upon the stair | Мы встретились на лестнице. spoke of | "
        "говорили о (чём-то) | We spoke of was and when | Мы говорили о том, что было и когда. came as a surprise | "
        "стало неожиданностью | Which came as a surprise | Что стало неожиданностью. into his eyes | ему в глаза | | "
        "I spoke into his eyes | Я произнёс это, глядя ему в глаза. died alone | умер в одиночестве | | "
        "I thought you died alone | Я думал, ты умер в одиночестве. lost control | потерял контроль | "
        "We never lost control | Мы никогда не теряли контроль. face to face | лицом к лицу | You’re face to face | "
        "Ты лицом к лицу. sold the world | продал мир | With the man who sold the world | "
        "С человеком, который продал мир. shook his hand | пожал ему руку | | I laughed and shook his hand | "
        "Я рассмеялся и пожал ему руку. made my way back | направился обратно | And made my way back home | "
        "И отправился обратно домой. searched for | искал (что-то) | | I searched for form and land | "
        "Я искал форму и землю. roamed | бродил, скитался | For years and years I roamed | Годами я скитался. "
        "gazed | пристально смотрел | | I gazed a gazeless stare | Я смотрел пустым, ничего не выражающим взглядом. "
        "a million hills | миллион холмов | We walked a million hills | Мы прошли через миллион холмов.")

    async def t_ai_one_line():
        cards = ai_prompt.parse_answer(ONE_LINE_ANSWER)
        assert len(cards) == 14, [c.phrase for c in cards]
        by = {c.phrase: c for c in cards}
        assert by['spoke of'] == ai_prompt.Card('spoke of', 'говорили о (чём-то)', 'We spoke of was and when',
                                                'Мы говорили о том, что было и когда.'), by['spoke of']
        assert by['into his eyes'].sentence == 'I spoke into his eyes', 'пустое поле «| |» не сдвигает поля'
        assert by['a million hills'].sentence_ru == 'Мы прошли через миллион холмов.'
        database.delete_words(UID)
        texts, _ = await send(msg_update(ONE_LINE_ANSWER), 'paste one-line answer')
        assert 'Карточки от ИИ: 14' in texts[-1].text, texts[-1].text
        await send(cb_update('tx:cancel', session.next_id), 'cancel')
    await case('Ответ ИИ одним абзацем без переносов (реальный ответ ChatGPT) → все 14 карточек', t_ai_one_line)

    async def t_prompt_pasted_back():
        prompt = ai_prompt.build_prompt('I was waiting for you.')
        texts, _ = await send(msg_update(prompt), 'prompt pasted back')
        assert 'Это сам промпт' in texts[-1].text, texts[-1].text
    await case('Если вставить боту сам промпт — бот подсказывает, что нужен ответ ИИ', t_prompt_pasted_back)

    async def t_ai_cards():
        database.delete_words(UID)
        texts, _ = await send(msg_update(MESSY_AI_ANSWER), 'paste AI answer')
        assert 'Карточки от ИИ: 3' in texts[-1].text, texts[-1].text
        mid = session.next_id
        texts, _ = await send(cb_update('tx:add', mid), 'add AI cards')
        assert 'Добавил 3 слова' in texts[-1].text, texts[-1].text
        words = {w.word: w for w in database.get_words(UID)}
        assert set(words) == {'looking for', 'put up with', 'keys'}, set(words)
        assert words['looking for'].example_ru == 'Я искал ключи.'
        database.delete_words(UID)
        database.add_words(UID, [words['looking for']])
        await send(msg_update('🎴 Учить'), 'study menu')
        m2 = session.next_id
        await send(cb_update('study:cards', m2), 'cards')
        texts, _ = await send(cb_update('study:reveal', m2), 'reveal')
        assert 'Я искал ключи.' in texts[-1].text, 'перевод примера на обороте карточки'
        await send(cb_update('study:stop', m2), 'stop')
    await case('Вставил ответ ИИ → «Карточки от ИИ» → добавил, перевод примера на карточке', t_ai_cards)

    async def t_ai_order():
        prompt = ai_prompt.build_prompt('x')
        assert 'в том порядке, в котором фразы встречаются в тексте' in prompt
        assert 'артикли' in prompt and 'если они стоят отдельно' not in prompt and 'go to the park' in prompt
        source = ("I was walking in the park. Suddenly I came across an old friend. "
                  "We decided to catch up over coffee, and he kept on talking about his trip.")
        C = ai_prompt.Card
        shuffled = [C('kept on', 'продолжал', 'We decided to catch up over coffee, and he kept on talking about his trip.', ''),
                    C('made up', 'выдумал', 'He made it all up.', ''),            # такого в тексте нет
                    C('came across', 'встретил', 'Suddenly I came across an old friend.', ''),
                    C('catch up', 'пообщаться', 'We decided to catch up over coffee, and he kept on talking about his trip.', ''),
                    C('walking in the park', 'гулял в парке', 'I was walking in the park.', '')]
        ordered = [c.phrase for c in ai_prompt.order_by_text(shuffled, source)]
        assert ordered == ['walking in the park', 'came across', 'catch up', 'kept on', 'made up'], ordered

        # весь путь: «🤖 Через ИИ» → текст → ответ ИИ в другом порядке → экран в порядке текста
        database.delete_words(UID)
        await send(msg_update('🤖 Через ИИ'), 'ai menu')
        await send(msg_update(source), 'source text')
        answer = '\n'.join(f'{c.phrase} | {c.translation} | {c.sentence} | перевод' for c in shuffled)
        texts, _ = await send(msg_update(answer), 'shuffled AI answer')
        phrases = [a for a, _, _ in listed(texts[-1])]
        assert phrases == ['walking in the park', 'came across', 'catch up', 'kept on', 'made up'], phrases
        mid = session.next_id
        await send(cb_update('tx:all', mid), 'unselect all')
        await send(cb_update('tx:3', mid), 'pick kept on')
        await send(cb_update('tx:0', mid), 'pick walking')
        await send(cb_update('tx:add', mid), 'add')
        assert [w.word for w in database.get_words(UID)] == ['walking in the park', 'kept on'], 'добавляются тоже по порядку'
    await case('Карточки от ИИ стоят в порядке текста, даже если ИИ их перемешал', t_ai_order)

    async def t_ai_prompt_buttons():
        texts, _ = await send(msg_update('🤖 Через ИИ'), 'ai menu')
        assert 'Карточки через ИИ' in texts[-1].text
        texts, _ = await send(msg_update('I was waiting for you all day.'), 'text for AI')
        assert '<pre>' in texts[-1].text and 'I was waiting for you all day.' in texts[-1].text
        texts, _ = await send(msg_update('dog - собака'), 'normal mode after prompt')
        assert 'Добавил 1 слово' in texts[-1].text, 'после промпта бот снова понимает слова и пары'
        texts, _ = await send(cb_update('ai:start', 5), 'old ai button')
        assert 'Карточки через ИИ' in texts[-1].text
        await send(msg_update('/start'), 'reset')
    await case('«🤖 Через ИИ» в меню → промпт с текстом → дальше обычный режим', t_ai_prompt_buttons)

    async def t_no_dictionary():
        saved = dic.DICT_PATH
        dic.DICT_PATH = 'нет-такого-файла.db'
        try:
            texts, _ = await send(msg_update('serendipity'), 'lookup without dictionary')
            assert 'не подключён' in texts[-1].text and 'lk:own' in kb_data(texts[-1])
            texts, _ = await send(msg_update('This is a long English text about many things.'), 'text without dict')
            assert 'не подключён' in texts[-1].text
        finally:
            dic.DICT_PATH = saved
    await case('Без dictionary.db бот не падает и предлагает ввести перевод сам', t_no_dictionary)

    # ───────── «Только незнакомые» и статистика ─────────
    async def t_unknown_mode():
        database.delete_words(UID)
        database.add_words(UID, [database.Word(w, t, '', '') for w, t in
                                 [('apple', 'яблоко'), ('cat', 'кот'), ('dog', 'собака')]])
        await send(msg_update('🎴 Учить'), 'study')
        mid = session.next_id
        assert 'study:unknown' not in cb_list(session.calls[-1]), 'пока ничего не учили — «▶️ Начать»'
        await send(cb_update('study:cards', mid), 'cards')
        for answer in ('study:know', 'study:dontknow', 'study:know'):
            await send(cb_update('study:reveal', mid), 'reveal')
            await send(cb_update(answer, mid), answer)
        texts, _ = await send(cb_update('study:menu', mid), 'menu after')
        assert '✅ Знаю 2 · 🔁 Не знаю 1 · 🆕 Новых 0' in texts[-1].text, texts[-1].text
        assert kb_first(texts[-1]) == '▶️ Незнакомые (1)', kb_text(texts[-1])
        texts, _ = await send(cb_update('study:unknown', mid), 'unknown only')
        assert 'Карточка 1 из 1' in texts[-1].text and 'cat' in texts[-1].text, texts[-1].text
        await send(cb_update('study:reveal', mid), 'reveal')
        texts, _ = await send(cb_update('study:know', mid), 'know cat')
        assert cb_list(texts[-1])[0] == 'study:unknown', '«Ещё раз» — снова незнакомые'
        texts, _ = await send(cb_update('study:unknown', mid), 'again unknown')
        assert 'Незнакомых не осталось' in texts[-1].text, texts[-1].text
    await case('«Знаю / Не знаю» запоминаются → «▶️ Незнакомые (N)» → «Незнакомых не осталось»', t_unknown_mode)

    async def t_stats():
        database.delete_words(UID)
        database.add_words(UID, [database.Word('look for', 'искать', '', ''), database.Word('cat', 'кот', '', '')])
        words = database.get_words(UID)
        database.record_answer(UID, words[0].id, False)
        database.record_answer(UID, words[1].id, True)
        texts, _ = await send(cb_update('study:stats', 5), 'stats')
        t = texts[-1].text
        assert texts[-1].reply_markup is None, 'без кнопок — удобно переслать'
        assert 'Всего: 2 слова' in t and 'Знаю: 1' in t and 'Не знаю: 1' in t, t
        assert 'Трудные слова' in t and 'look for' in t and 'искать' in t and 'cat' not in t.split('Трудные')[1], t
    await case('📊 Статистика отдельным сообщением: сколько знаю / не знаю, трудные слова', t_stats)

    async def t_transcription_everywhere():
        if not dic.available():
            return
        database.delete_words(UID)
        texts, _ = await send(msg_update('ubiquitous'), 'lookup ipa')
        assert '/juːˈbɪkwɪtəs/' in texts[-1].text, texts[-1].text
        await send(cb_update('lk:0', session.next_id), 'pick')
        database.add_words(UID, [database.Word('looking for', 'искал', '', '')])
        ipas = {w.word: w.transcription for w in database.get_words(UID)}
        assert ipas == {'ubiquitous': '/juːˈbɪkwɪtəs/', 'looking for': '/ˈlʊkɪŋ fɔː/'}, ipas
        texts, _ = await send(msg_update('📋 Мои слова'), 'vocab ipa')
        assert '/ˈlʊkɪŋ fɔː/' in texts[-1].text, texts[-1].text
        sch._positions[UID] = 1
        before = len(session.calls)
        await sch.send_next_word(bot, UID)
        assert '/ˈlʊkɪŋ fɔː/' in session.calls[before:][-1].text
        ans = await inline_answer('ubiquitous')
        assert '/juːˈbɪkwɪtəs/' in ans.results[0].description
        import sqlite3
        with sqlite3.connect(database.DB) as conn:          # старое слово без транскрипции
            conn.execute("UPDATE words SET transcription = '' WHERE user_id = ?", (UID,))
        database.init_db()
        assert all(w.transcription for w in database.get_words(UID)), 'транскрипция дописывается старым словам'
    await case('Транскрипция везде: экран слова, словарь, рассылка, inline, старые слова', t_transcription_everywhere)

    # ───────── С русского на английский ─────────
    async def t_ru_word():
        if not dic.available():
            return
        database.delete_words(UID)
        texts, _ = await send(msg_update('собака'), 'russian word')
        assert 'с русского на английский' in texts[-1].text and 'ru:yes' in cb_list(texts[-1])
        mid = session.next_id
        texts, _ = await send(cb_update('ru:yes', mid), 'ru yes')
        assert '🇷🇺 <b>собака</b>' in texts[-1].text, texts[-1].text
        assert kb_text(texts[-1])[0].startswith('dog /dɒɡ/'), kb_text(texts[-1])
        await send(cb_update('rk:0', mid), 'pick dog')
        w = database.get_words(UID)[0]
        assert (w.word, w.translation, w.direction, w.transcription) == ('dog', 'собака', 'ru', '/dɒɡ/'), w
        database.add_words(UID, [database.Word('dog', 'собака', '', '')])
        assert database.count_words(UID) == 2, 'английская карточка dog → собака — не повтор русской'
        database.delete_words(UID)
        database.add_words(UID, [w])
        await send(msg_update('🎴 Учить'), 'study ru')
        m2 = session.next_id
        texts, _ = await send(cb_update('study:cards', m2), 'ru card')
        assert '🇷🇺 <b>собака</b>' in texts[-1].text and 'dog' not in texts[-1].text, texts[-1].text
        assert 'по-английски' in texts[-1].text
        texts, _ = await send(cb_update('study:reveal', m2), 'ru reveal')
        assert '🇬🇧 <b>dog</b>  /dɒɡ/' in texts[-1].text, texts[-1].text
        texts, _ = await send(cb_update('study:edit', m2), 'ru edit')
        assert texts[-1].text.index('🇷🇺') < texts[-1].text.index('🇬🇧'), 'в редакторе тоже русское первым'
        await send(cb_update('we:word', m2), 'ru edit word')
        texts, _ = await send(msg_update('собачка'), 'ru edit cyrillic')
        assert 'латиницей' in texts[-1].text
        texts, _ = await send(msg_update('hound'), 'ru edit latin')
        assert 'Исправил' in texts[-1].text and 'hound' in texts[-1].text
        w = database.get_words(UID)[0]
        assert (w.word, w.translation) == ('hound', 'собака'), w
        await send(cb_update('study:stop', session.next_id), 'stop')
        texts, _ = await send(msg_update('📋 Мои слова'), 'vocab ru')
        assert '🇷🇺 <b>собака</b> — hound' in texts[-1].text, texts[-1].text
        sch._positions[UID] = 0
        before = len(session.calls)
        await sch.send_next_word(bot, UID)
        sent = session.calls[before:][-1].text
        assert 'по-английски' in sent and '<tg-spoiler>hound' in sent, sent
    await case('Русское слово → «с русского?» → dog → карточка: русское спереди, английское на обороте', t_ru_word)

    async def t_ru_own():
        database.delete_words(UID)
        await send(msg_update('абырвалг'), 'russian unknown')
        mid = session.next_id
        texts, _ = await send(cb_update('ru:yes', mid), 'ru yes')
        assert 'rk:own' in cb_list(texts[-1]) and 'rk:ai' in cb_list(texts[-1])
        texts, _ = await send(cb_update('rk:own', mid), 'own en')
        assert 'как по-английски' in texts[-1].text
        texts, _ = await send(msg_update('по-русски'), 'own cyrillic')
        assert 'латиницей' in texts[-1].text
        texts, _ = await send(msg_update('fish'), 'own latin')
        assert 'Добавил' in texts[-1].text
        w = database.get_words(UID)[0]
        assert (w.word, w.translation, w.direction) == ('fish', 'абырвалг', 'ru'), w
        texts, _ = await send(cb_update('rk:ai', mid), 'ru ai from word')
        assert 'с русского на английский' in texts[0].text and 'английская фраза' in texts[-1].text
    await case('Русское слово, которого нет в словаре → свой английский вариант или ИИ', t_ru_own)

    async def t_ru_text_ai():
        database.delete_words(UID)
        source = 'Я весь день искала ключи. Новость свалилась как снег на голову.'
        texts, _ = await send(msg_update(source), 'russian text')
        mid = session.next_id
        texts, _ = await send(cb_update('ru:yes', mid), 'ru yes text')
        assert 'английская фраза | русская фраза' in texts[-1].text and 'искала ключи' in texts[-1].text
        assert ai_prompt.is_prompt(ai_prompt.build_prompt(source, 'ru'))
        answer = ('out of the blue | как снег на голову | The news came out of the blue. | Новость свалилась как снег на голову.\n'
                  'was looking for | искала | I was looking for my keys all day. | Я весь день искала ключи.')
        texts, _ = await send(msg_update(answer), 'ru AI answer')
        assert 'Карточки от ИИ с русского: 2' in texts[-1].text, texts[-1].text
        assert [(a, b) for a, b, _ in listed(texts[-1])] == [('искала', 'was looking for'),
                                                           ('как снег на голову', 'out of the blue')], texts[-1].text
        assert kb_text(texts[-1])[0].endswith('искала'), 'на кнопке — русская сторона'
        m2 = session.next_id
        await send(cb_update('tx:add', m2), 'add')
        got = [(w.word, w.translation, w.direction, w.example_ru) for w in database.get_words(UID)]
        assert got[0] == ('was looking for', 'искала', 'ru', 'Я весь день искала ключи.'), got
    await case('Русский текст → промпт «с русского» → ответ ИИ → карточки с русского в порядке текста', t_ru_text_ai)

    # ───────── Длинный промпт — файлом, длинный ответ ИИ — склеиваем ─────────
    def calls_of(kind, before):
        return [c for c in session.calls[before:] if type(c).__name__ == kind]

    async def t_prompt_file():
        sentence = 'She was looking forward to the weekend because she wanted to get away from it all. '
        await send(msg_update('🤖 Через ИИ'), 'ai menu')
        before = len(session.calls)
        texts, _ = await send(msg_update(sentence * 60), 'long text')
        docs = calls_of('SendDocument', before)
        assert len(docs) == 1 and docs[0].document.filename == 'prompt.txt', docs
        content = docs[0].document.data.decode('utf-8')
        assert content.count('looking forward to') == 60, 'текст целиком, ничего не потерялось'
        assert 'Не больше 60 карточек' in content and ai_prompt.is_prompt(content)
        assert 'файлом' in texts[-1].text and not any('<pre>' in t.text for t in texts)
        await send(msg_update('🤖 Через ИИ'), 'ai menu 2')
        before = len(session.calls)
        texts, _ = await send(msg_update('I was waiting for you all day.'), 'short text')
        assert '<pre>' in texts[-1].text and not calls_of('SendDocument', before), 'короткий — сообщением'
        session.files['back'] = docs[0].document.data
    await case('Длинный промпт → один файл prompt.txt с текстом целиком, короткий — сообщением', t_prompt_file)

    async def t_glue():
        import handlers.add as add_handlers
        assert ai_prompt.glue(['a | b | c | d', 'e | f | g | h']) == 'a | b | c | d\ne | f | g | h'
        assert ai_prompt.glue(['a | b | I was look', 'ing | d']) == 'a | b | I was look ing | d'
        cards = [f'phrase number{i} | перевод номер {i} | This is a rather long example sentence number {i} '
                 f'with phrase number{i} inside it. | Это довольно длинный пример номер {i} с фразой.' for i in range(50)]
        answer = '\n'.join(cards)
        cut = answer.index(' ', len(answer) // 2)             # режем посреди карточки, как Telegram
        part1, part2 = answer[:cut], answer[cut + 1:]
        assert len(part1) >= add_handlers.GLUE_FROM
        database.delete_words(UID)
        database.save_ai_text(UID, 'x')
        saved, add_handlers.GLUE_DELAY = add_handlers.GLUE_DELAY, 0.3
        try:
            before = len(session.calls)
            await asyncio.gather(dp.feed_raw_update(bot, msg_update(part1)),
                                 dp.feed_raw_update(bot, msg_update(part2)))
        finally:
            add_handlers.GLUE_DELAY = saved
        sent = calls_of('SendMessage', before)
        assert len(sent) == 1 and 'Карточки от ИИ: 50' in sent[0].text, [m.text[:60] for m in sent]
        check_html(sent[0].text)
        assert len(sent[0].text) <= 4096
    await case('Длинный ответ ИИ, разрезанный Telegram на 2 сообщения, склеивается — все 50 карточек', t_glue)

    # ───────── Таблицы и файлы ─────────
    def doc_update(file_name, data):
        uid_counter[0] += 1
        file_id = f'file{uid_counter[0]}'
        session.files[file_id] = data
        return {'update_id': uid_counter[0], 'message': {
            'message_id': uid_counter[0], 'date': int(time.time()), 'chat': {'id': UID, 'type': 'private'},
            'from': user(), 'document': {'file_id': file_id, 'file_unique_id': file_id,
                                         'file_name': file_name, 'file_size': len(data)}}}

    def make_docx(paragraphs, rows):
        import io
        import zipfile
        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        p = lambda t: f'<w:p><w:r><w:t>{t}</w:t></w:r></w:p>'
        table = '<w:tbl>' + ''.join('<w:tr>' + ''.join(f'<w:tc>{p(c)}</w:tc>' for c in r) + '</w:tr>'
                                    for r in rows) + '</w:tbl>'
        xml = f'<w:document xmlns:w="{W}"><w:body>{"".join(map(p, paragraphs))}{table}</w:body></w:document>'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('word/document.xml', xml)
        return buf.getvalue()

    async def t_table_paste():
        database.delete_words(UID)
        texts, _ = await send(msg_update('apple\t[ˈæpl]\tяблоко\nlook for\t/lʊk fɔː/\tискать'), 'tsv paste')
        assert 'Добавил 2 слова' in texts[-1].text, texts[-1].text
        got = {w.word: (w.translation, w.transcription) for w in database.get_words(UID)}
        assert got == {'apple': ('яблоко', '[ˈæpl]'), 'look for': ('искать', '/lʊk fɔː/')}, got
        texts, _ = await send(msg_update('cat | /kæt/ | кот\ndog | /dɒɡ/ | собака'), 'pipe table with ipa')
        assert 'Добавил 2 слова' in texts[-1].text, 'таблица через | с транскрипцией — не ответ ИИ'
    await case('Таблица из Word/Excel сообщением: «слово ⇥ транскрипция ⇥ перевод» → пары', t_table_paste)

    async def t_docx_file():
        database.delete_words(UID)
        data = make_docx(['Homework for Monday'], [['Word', 'Transcription', 'Translation'],
                                                   ['apple', '[ˈæpl]', 'яблоко'],
                                                   ['ubiquitous', '', ''],
                                                   ['qwertyzz', '/kw/', '']])
        texts, _ = await send(doc_update('урок.docx', data), 'docx table')
        added = [t for t in texts if 'Добавил' in t.text][0].text
        assert 'apple' in added and ('ubiquitous' in added or not dic.available()), added
        assert 'Не нашёл перевод' in added and 'qwertyzz' in added, added
        words = {w.word: w for w in database.get_words(UID)}
        if dic.available():
            assert words['ubiquitous'].translation == 'вездесущий', 'пустой перевод — из словаря'
        assert words['apple'].transcription == '[ˈæpl]', 'транскрипция — как у репетитора'
        assert any(k in t.text.lower() for t in texts[1:] for k in ('homework', 'не нашёл', 'не подключён')), \
            'текст вне таблицы тоже разбирается'
    await case('Файл .docx с таблицей репетитора → пары; пустой перевод берётся из словаря', t_docx_file)

    async def t_other_files():
        texts, _ = await send(doc_update('song.txt', 'Я весь день искала ключи.'.encode('cp1251')), 'russian txt')
        assert 'с русского на английский' in texts[-1].text, 'txt в cp1251 тоже читается'
        texts, _ = await send(doc_update('scan.pdf', b'%PDF-1.4 broken'), 'broken pdf')
        assert 'PDF' in texts[-1].text
        texts, _ = await send(doc_update('photo.jpg', b'xx'), 'jpg')
        assert 'Пока понимаю' in texts[-1].text
        srt = b'1\n00:00:01,000 --> 00:00:03,000\nI gave up on my dreams.\n'
        texts, _ = await send(doc_update('film.srt', srt), 'srt')
        assert '00:00' not in texts[-1].text
        await send(msg_update('🤖 Через ИИ'), 'ai menu')
        texts, _ = await send(doc_update('story.txt', b'I was looking for my keys all morning.'), 'file for AI')
        assert '<pre>' in texts[-1].text and 'looking for my keys' in texts[-1].text, 'файл в режиме «Через ИИ» → промпт'
    await case('Файлы: русский .txt → «с русского?», битый PDF и .jpg → понятный ответ, .srt, файл → ИИ', t_other_files)

    async def t_prompt_file_back():
        texts, _ = await send(doc_update('prompt.txt', session.files['back']), 'prompt file back')
        assert 'Это сам промпт' in texts[-1].text, texts[-1].text
    await case('Если прислать боту сам prompt.txt — подсказка, что нужен ответ ИИ', t_prompt_file_back)

    # ───────── Правка карточки до добавления ─────────
    async def t_edit_before_add():
        if not dic.available():
            return
        database.delete_words(UID)
        text = "I've been looking for my keys all morning and finally found them under the couch."
        texts, _ = await send(msg_update(text), 'extract for edit')
        mid = session.next_id
        total = len(listed(texts[-1]))
        texts, _ = await send(cb_update('tx:edit', mid), 'edit mode')
        assert 'Режим правки' in texts[-1].text and kb_data(texts[-1])[0] == 'te:0', kb_data(texts[-1])
        texts, _ = await send(cb_update('te:0', mid), 'open editor')
        assert f'Карточка 1 из {total}' in texts[-1].text and 'looking for' in texts[-1].text
        assert {'tf:word', 'tf:tr', 'tf:ipa', 'tf:ex', 'tf:exru', 'tf:drop', 'tf:back'} <= set(kb_data(texts[-1]))
        await send(cb_update('tf:tr', mid), 'edit translation')
        _, answers = await send(cb_update('tf:cancel', session.next_id), 'cancel field')
        assert 'Оставил' in answers[-1].text
        texts, _ = await send(cb_update('tf:tr', mid), 'edit translation again')
        assert 'Напиши перевод' in texts[-1].text
        texts, _ = await send(msg_update('искала'), 'new translation')
        assert 'Исправил' in texts[-1].text and '🇷🇺 искала' in texts[-1].text, texts[-1].text
        m2 = session.next_id
        _, answers = await send(cb_update('tf:back', mid), 'old editor')
        assert answers[-1].show_alert, 'старый экран закрыт'
        await send(cb_update('tf:word', m2), 'edit word')
        texts, _ = await send(msg_update('привет'), 'cyrillic word')
        assert 'латиницей' in texts[-1].text
        texts, _ = await send(msg_update('Search For'), 'new word')
        m3 = session.next_id
        assert '<b>search for</b>' in texts[-1].text and dic.transcription('search for') in texts[-1].text, texts[-1].text
        await send(cb_update('tf:ipa', m3), 'edit ipa')
        texts, _ = await send(msg_update('sɜːtʃ fə'), 'new ipa')
        m4 = session.next_id
        assert '/sɜːtʃ fə/' in texts[-1].text
        texts, _ = await send(cb_update('tf:back', m4), 'back to list')
        items = listed(texts[-1])
        assert items[0][:2] == ('search for', 'искала') and f'✅ Добавить ({total})' in kb_text(texts[-1]), items
        await send(cb_update('te:1', m4), 'open second')
        texts, answers = await send(cb_update('tf:drop', m4), 'drop second')
        assert len(listed(texts[-1])) == total - 1 and 'Убрал' in answers[-1].text
        await send(cb_update('te:0', m4), 'open first again')
        await send(cb_update('tf:ex', m4), 'edit example')
        texts, _ = await send(msg_update('📋 Мои слова'), 'menu during field edit')
        assert 'Мои слова пока пусты' in texts[-1].text, 'кнопка меню не становится примером'
        texts, _ = await send(cb_update('tf:back', m4), 'back')
        assert f'✅ Добавить ({total - 1})' in kb_text(texts[-1]), 'убранное не добавится'
        await send(cb_update('tx:edit', m4), 'edit mode off')
        await send(cb_update('tx:all', m4), 'unselect all')
        await send(cb_update('tx:0', m4), 'select edited')
        texts, _ = await send(cb_update('tx:add', m4), 'add edited')
        assert 'Добавил 1 слово' in texts[-1].text, texts[-1].text
        w = database.get_words(UID)[0]
        assert (w.word, w.translation, w.transcription) == ('search for', 'искала', '/sɜːtʃ fə/'), w
        assert 'keys all morning' in w.example
    await case('«✏️ Править» до добавления: перевод, слово (с новой транскрипцией), транскрипция, убрать', t_edit_before_add)

    # ───────── Выборочное удаление ─────────
    async def t_delete_selected():
        database.delete_words(UID)
        database.add_words(UID, [database.Word(f'word{i}', f'слово{i}', '', '') for i in range(25)])
        ws = database.get_words(UID)
        database.record_answer(UID, ws[0].id, True)
        database.record_answer(UID, ws[1].id, True)
        texts, _ = await send(msg_update('📋 Мои слова'), 'vocab')
        mid = session.next_id
        assert 'voc:del' in kb_data(texts[-1]) and 'voc:clear' not in kb_data(texts[-1])
        texts, _ = await send(cb_update('voc:del', mid), 'delete screen')
        assert 'Удаление слов' in texts[-1].text and 'стр. 1 / 2' in texts[-1].text and 'Отмечено: 0' in texts[-1].text
        _, answers = await send(cb_update('vd:go', mid), 'nothing marked')
        assert 'Сначала отметь' in answers[-1].text
        await send(cb_update(f'vd:t:{ws[2].id}:0', mid), 'mark 3')
        texts, _ = await send(cb_update(f'vd:t:{ws[3].id}:0', mid), 'mark 4')
        assert any(t.startswith('☑️ 4.') for t in kb_text(texts[-1]))
        await send(cb_update('vd:p:1', mid), 'page 2')
        texts, _ = await send(cb_update('vd:all:1', mid), 'whole page 2')
        assert 'Отмечено: 7' in texts[-1].text, texts[-1].text
        assert '🗑 Выученные (2)' in kb_text(texts[-1]) and 'voc:clear' in kb_data(texts[-1])
        texts, _ = await send(cb_update('vd:go', mid), 'confirm')
        assert 'Удалить 7 слов' in texts[-1].text and 'нельзя отменить' in texts[-1].text
        texts, _ = await send(cb_update('vd:yes', mid), 'delete marked')
        assert 'Удалил: 7 слов' in texts[-1].text and database.count_words(UID) == 18
        left = {w.word for w in database.get_words(UID)}
        assert 'word2' not in left and 'word24' not in left and 'word0' in left
        await send(cb_update('voc:del', mid), 'delete screen again')
        texts, _ = await send(cb_update('vd:known', mid), 'known confirm')
        assert 'выученные' in texts[-1].text and '2 слова' in texts[-1].text, texts[-1].text
        await send(cb_update('vd:known:yes', mid), 'delete known')
        assert database.count_words(UID) == 16 and 'word0' not in {w.word for w in database.get_words(UID)}
    await case('Удаление: отметить слова и целую страницу, удалить отмеченные / выученные, с подтверждением', t_delete_selected)

    # ───────── Текст песни ─────────
    async def t_song():
        from utils import lyrics as lyr
        assert lyr.from_filename('03. David Bowie - The Man Who Sold the World.mp3') == \
            'David Bowie The Man Who Sold the World'
        words = ("We passed upon the stair\nWe spoke of was and when\nAlthough I wasn't there\n"
                 "He said I was his friend\nWhich came as a surprise")
        records = [
            {'artistName': 'David Bowie', 'trackName': 'The Man Who Sold the World', 'plainLyrics': words, 'duration': 240},
            {'artistName': 'Nirvana', 'trackName': 'The Man Who Sold the World', 'plainLyrics': words, 'duration': 260},
            {'artistName': 'David Bowie', 'trackName': 'The Man Who Sold the World', 'plainLyrics': 'дубль', 'duration': 241},
            {'artistName': 'X', 'trackName': 'Instrumental', 'plainLyrics': None, 'instrumental': True},
        ]
        asked, mode = [], {'v': 'ok'}

        async def fake_fetch(params):
            asked.append(params)
            if mode['v'] == 'down':
                raise RuntimeError('нет сети')
            return records if mode['v'] == 'ok' else []

        saved_fetch, lyr._fetch = lyr._fetch, fake_fetch

        def audio_update(**audio):
            uid_counter[0] += 1
            return {'update_id': uid_counter[0], 'message': {
                'message_id': uid_counter[0], 'date': int(time.time()), 'chat': {'id': UID, 'type': 'private'},
                'from': user(), 'audio': {'file_id': 'a', 'file_unique_id': 'a', **audio}}}
        try:
            texts, _ = await send(audio_update(duration=259, performer='Nirvana', title='The Man Who Sold the World',
                                               file_name='track.mp3'), 'mp3 with tags')
            assert asked[0] == {'track_name': 'The Man Who Sold the World', 'artist_name': 'Nirvana'}, asked
            mid = session.next_id
            assert 'Nirvana — The Man Who Sold the World' in texts[-1].text, 'ближе по длительности — первой'
            assert 'Нашёл текст: 5 строк' in texts[-1].text and 'We passed upon the stair' in texts[-1].text
            assert {'sg:words', 'sg:ai', 'sg:other'} <= set(kb_data(texts[-1]))
            texts, _ = await send(cb_update('sg:other', mid), 'other song')
            assert kb_data(texts[-1]) == ['sg:pick:1', 'sg:back'], 'дубль и инструментал отброшены'
            texts, _ = await send(cb_update('sg:pick:1', mid), 'pick bowie')
            assert 'David Bowie — The Man Who Sold the World' in texts[-1].text
            texts, _ = await send(cb_update('sg:words', mid), 'song words')
            assert any(k in texts[-1].text for k in ('Нашёл в тексте', 'Не нашёл', 'не подключён')), texts[-1].text
            texts, _ = await send(cb_update('sg:ai', mid), 'song ai')
            assert '<pre>' in texts[-1].text and 'We passed upon the stair' in texts[-1].text
            texts, _ = await send(msg_update('🎵 Песня'), 'song menu button')
            assert 'Текст песни' in texts[-1].text
            texts, _ = await send(msg_update('bowie man who sold'), 'song name')
            assert asked[-1] == {'q': 'bowie man who sold'} and 'Нашёл текст' in texts[-1].text
            texts, _ = await send(msg_update('dog - собака'), 'normal mode after song')
            assert 'Добавил 1 слово' in texts[-1].text, 'после песни бот снова понимает слова и пары'
            texts, _ = await send(cb_update('sg:start', 5), 'song inline button')
            assert 'Текст песни' in texts[-1].text
            await send(msg_update('/start'), 'reset')
            texts, _ = await send(doc_update('David Bowie - The Man Who Sold the World.mp3', b'ID3'), 'mp3 as file')
            assert asked[-1] == {'q': 'David Bowie The Man Who Sold the World'} and 'Нашёл текст' in texts[-1].text
            mode['v'] = 'empty'
            await send(msg_update('/song'), 'song command')
            texts, _ = await send(msg_update('qwerty song zz'), 'unknown song')
            assert 'Не нашёл текст песни' in texts[-1].text and 'sg:start' in kb_data(texts[-1])
            mode['v'] = 'down'
            await send(msg_update('/song'), 'song command 2')
            texts, _ = await send(msg_update('anything'), 'service down')
            assert 'не отвечает' in texts[-1].text
            texts, _ = await send(audio_update(duration=100), 'mp3 without name')
            assert 'нет названия' in texts[-1].text
            uid_counter[0] += 1
            texts, _ = await send({'update_id': uid_counter[0], 'message': {
                'message_id': uid_counter[0], 'date': int(time.time()), 'chat': {'id': UID, 'type': 'private'},
                'from': user(), 'voice': {'file_id': 'v', 'file_unique_id': 'v', 'duration': 3}}}, 'voice')
            assert 'не распознаю' in texts[-1].text
        finally:
            lyr._fetch = saved_fetch
    await case('🎵 Песня: mp3 (теги или имя файла) или название → текст из LRCLIB → слова / ИИ; не нашёл, сервис лёг', t_song)

    # ───────── «Мои слова»: значки, изменить, удалить; варианты «Учить» ─────────
    async def t_words_screen():
        await send(msg_update('/start'), 'reset')
        database.delete_words(UID)
        database.add_words(UID, [database.Word(w, t, '', '') for w, t in
                                 [('apple', 'яблоко'), ('cat', 'кот'), ('dog', 'собака')]])
        ws = database.get_words(UID)
        database.record_answer(UID, ws[0].id, True)
        database.record_answer(UID, ws[1].id, False)
        texts, _ = await send(msg_update('📋 Мои слова'), 'my words')
        mid = session.next_id
        t = texts[-1].text
        assert '✅ 1 знаю · 🔁 1 не знаю · 🆕 1 новых' in t, t
        assert '1. ✅ <b>apple</b>' in t and '2. 🔁 <b>cat</b>' in t and '3. <b>dog</b>' in t, t
        assert kb_data(texts[-1]) == ['ve:p:0', 'voc:del', 'study:stats'], kb_data(texts[-1])
        texts, _ = await send(cb_update('ve:p:0', mid), 'choose word')
        assert 'Какое слово изменить' in texts[-1].text and kb_data(texts[-1])[1] == f've:w:{ws[1].id}:0'
        texts, _ = await send(cb_update(f've:w:{ws[1].id}:0', mid), 'open cat')
        assert 'Изменить карточку' in texts[-1].text and 'we:del' in kb_data(texts[-1])
        await send(cb_update('we:tr', mid), 'ask translation')
        texts, _ = await send(msg_update('кошка'), 'new translation')
        m2 = session.next_id
        assert 'Исправил' in texts[-1].text and 'кошка' in texts[-1].text
        _, answers = await send(cb_update('we:tr', mid), 'old editor')
        assert answers[-1].show_alert, 'старый экран редактора устарел'
        texts, _ = await send(cb_update('we:back', m2), 'back to list')
        assert re.search(r'<b>cat</b>( /[^/]*/)? — кошка', texts[-1].text), texts[-1].text
        await send(cb_update(f've:w:{ws[2].id}:0', m2), 'open dog')
        texts, _ = await send(cb_update('we:del', m2), 'ask delete')
        assert 'Удалить «<b>dog</b> — собака»' in texts[-1].text
        texts, _ = await send(cb_update('we:del:no', m2), 'no')
        assert 'Изменить карточку' in texts[-1].text
        await send(cb_update('we:del', m2), 'ask delete again')
        texts, answers = await send(cb_update('we:del:yes', m2), 'delete dog')
        assert 'Удалил «dog»' in texts[-1].text and database.count_words(UID) == 2
        # «Учить»: все знаю → «Повторить все»
        database.record_answer(UID, ws[1].id, True)
        texts, _ = await send(msg_update('🎴 Учить'), 'study all known')
        assert kb_first(texts[-1]) == '▶️ Повторить все', kb_first(texts[-1])
    await case('«Мои слова»: значки знаю/не знаю, «✏️ Изменить» и удаление слова; «Учить» → «Повторить все»', t_words_screen)

    sch.scheduler.shutdown(wait=False)
    print(f'\nИТОГ: {passed[0]} прошло, {len(failures)} упало')
    sys.exit(1 if failures else 0)


asyncio.run(main())
