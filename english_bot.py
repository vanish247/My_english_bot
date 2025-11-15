from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

# Вместо BOT TOKEN HERE нужно вставить токен вашего бота,
# полученный у @BotFather
BOT_TOKEN = '7922133770:AAGm-NOxPnTz40ZAD0CiePmINbVY4CcAEEA'

# my_user_id == 134355
# @dp.message((F.text | F.photo) & F.from_user.id == 173901673)

# Хэндлер должен получить апдейт с типом контента текст, заканчивающийся на 'bot' от пользователя с ID = 173901673.

# from aiogram.filters import and_f

# @dp.message(and_f(F.text.endswith('bot'), F.from_user.id == 173901673))cvcdcd   serferbfsvwsdgwefvdfgttjtjtj      

# Создаем объекты бота и диспетчера
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# Словарь для хранения слов пользователей (ключ - user_id, значение - список слов)
user_words = {}

# Словарь для отслеживания состояний пользователей (изучение или добавление слов)
user_states = {}

# Словарь для отслеживания текущего индекса слова в словаре
current_index = {}

# Cостояния пользователей
add_words = 'add_words'
study_words = 'study_words'
waiting_add = 'waiting_add'       # Ожидание ответа после добавления
waiting_study = 'waiting_study'   # Ожидание ответа при изучении

# Инициализация данных пользователя
def init_user(user_id):
    if user_id not in user_words:
        user_words[user_id] = []
    if user_id not in current_index:
        current_index[user_id] = 0


# Этот хэндлер будет срабатывать на команду "/start"
@dp.message(CommandStart())
async def process_start_command(message: Message):

    user_id = message.from_user.id
    init_user(user_id)

    await message.answer(
        'Привет!\n Я буду помогать тебе запоминать и учить английские слова\n\n' 
        'Бот работает оч просто)\n'
        'С помощью команды /add добавляй слова в свой словарик\n'
        'Когда захочешь начать изучать добавленные слова,\n' 
        'вводи команду /study\n'
        'Если захочешь увидеть список изученных слов, \n'
        'то нажми /vocabulary,\n'
        'а очистить его - /delete_vocabulary\n'
        '/help даст полный список команд и их краткое описание'
    )

# список команд
@dp.message(Command(commands='help'))
async def process_start_command(message: Message):

    user_id = message.from_user.id
    init_user(user_id)

    await message.answer(
        '/start - полное описание бота\n' 
        '/add - добавление новых слов\n'
        '/study - изучение добавленных слов\n' 
        '/vocabulary - словарь изученных слов\n'
        '/delete_vocabulary - удаление словаря\n'
        '/help - полный список команд и их краткое описание'
    )


# Этот хэндлер будет срабатывать на готовность играть
@dp.message(Command(commands='add'))
async def add_words(message: Message):

    user_id = message.from_user.id
    init_user(user_id)

    user_states[user_id] = add_words
    await message.answer(
        'Введи слова с переводом через запятую в следующем формате\n'
        'apple - яблоко, fly - летать, go - ходить'
    )

# режим изучения слов
@dp.message(Command(commands='study'))
async def study_words(message: Message):
    
    user_id = message.from_user.id
    init_user(user_id)

    if not user_words[user_id]:
        await message.answer('Ваш список слов пуст.\n' 
                             'Пожалуйста, добавьте слова с помощью /add')
        return 
# return сразу завершает работу функции, чтобы 
# не пытаться работать с несуществующими/пустыми данными
# не отправлять пользователю противоречивые/лишние сообщения
# не вызывать потенциальные ошибки при обращении к пустому списку слов
 
    user_states[user_id] = study_words
    current_index[user_id] = 0

    await show_word(message, user_id)
    user_states[user_id] = waiting_study

# Функция, показывающая слово
async def show_word(message: Message, user_id: int):

    word = user_words[user_id][current_index[user_id]]
    # idx = current_index[user_id]

    await message.answer(f"{word}")
    await message.answer('Продожить? Да/Нет')

# выдаёт спмсок добавленных слов
@dp.message(Command(commands='vocabulary'))
async def print_vocabulary(message: Message):
    user_id = message.from_user.id
    init_user(user_id)

    if not user_words[user_id]:
        await message.answer('Ваш список слов пуст.\n' 
                             'Пожалуйста, добавьте слова с помощью /add')
        return
    
    word_list = "\n".join(f"{i+1}. {w}" for i, w in enumerate(user_words[user_id]))
    await message.answer(f"Твой словарь:\n{word_list}")


# очистка словаря
@dp.message(Command(commands='delete_vocabulary'))
async def print_vocabulary(message: Message):
    user_id = message.from_user.id
    init_user(user_id)
    user_words[user_id] = []
    await message.answer('Ваш словарь очищен')


@dp.message(F.text.contains('-'))
async def words_with_defiz(message: Message):

    user_id = message.from_user.id
    init_user(user_id)

    if user_states[user_id] == add_words:
        next_words = [w.strip() for w in message.text.split(',')] # удаляет проблемы и разделяет слова
        user_words[user_id].extend(next_words)
        await message.answer('Хотите добавить ещё?')
        user_states[user_id] = waiting_add


@dp.message(F.text.lower().in_(['да', 'нет']))
async def words_with_yes_no(message: Message):

    user_id = message.from_user.id
    current_state = user_states[user_id]
    
    if current_state not in (waiting_add, waiting_study):
        return

    if message.text.lower() == 'да':
        if current_state == waiting_add:
            user_states[user_id] = add_words
            await message.answer('Введите следующее слово')

        elif current_state == waiting_study:
            if current_index[user_id] + 1 < len(user_words[user_id]):
            # Условие не проверяет существование текущего слова (оно уже было показано).
            # Оно отвечает на вопрос: "Можно ли увеличить индекс и показать следующее слово?"
                current_index[user_id] += 1
                await show_word(message, user_id)
            else:
                await message.answer('Все слова изучены! Вы молодец))')
                # Сброс состояния
                user_states[user_id] == None

        
    else:
        if message.text.lower() == 'нет':
             if current_state == waiting_add:
                await message.answer('Хорошо! Нажми /study,\nесли хотите начать изучать добавленные слова')
             elif current_state == waiting_add:
                await message.answer('Хорошая работа! Нажмите /study,\nесли хотите начать изучать добавленные слова')
        # Сброс состояния
        user_states[user_id] == None

@dp.message(F.text)
async def other_messages(message: Message):
    """Обработчик прочих сообщений"""
    user_id = message.from_user.id
    init_user(user_id)
    
    if user_states.get(user_id) == add_words:  # .get вернет None, а не ошибку, если ключа нет
        await message.answer('Используй формат "слово - перевод"')
    else:
        await message.answer('Используй команды:\n/start\n/add\n/study\n/vocabulary')

# запуск бота
if __name__ == '__main__':
    dp.run_polling(bot)





