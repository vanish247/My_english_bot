import telebot
import time
import requests

# Здесь нужно вставить токен вашего бота
TOKEN = 'ВАШ_ТОКЕН_БОТА'

# Сначала сбрасываем все накопленные обновления
def reset_updates():
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    params = {"offset": -1}
    try:
        response = requests.get(url, params=params)
        print("Сброс обновлений:", response.json())
        return True
    except Exception as e:
        print(f"Ошибка при сбросе обновлений: {e}")
        return False

# Создаем бота только после сброса обновлений
print("Сбрасываем предыдущие обновления...")
reset_success = reset_updates()
if reset_success:
    print("Обновления сброшены, создаем бота...")
else:
    print("Не удалось сбросить обновления, но продолжаем...")

# Создаем экземпляр бота
bot = telebot.TeleBot(TOKEN)

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start_command(message):
    bot.send_message(message.chat.id, 'Привет')

# Запускаем бота с обработкой ошибок
if __name__ == "__main__":
    print("Запуск бота...")
    while True:
        try:
            # Используем минимальные значения для таймаутов
            bot.polling(none_stop=False, interval=0, timeout=1)
        except Exception as e:
            print(f"Произошла ошибка: {e}")
            print("Ждем 5 секунд перед повторной попыткой...")
            time.sleep(5)