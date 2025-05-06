import telebot
import time
import requests
import json

# Здесь нужно вставить токен вашего бота
TOKEN = '8091479275:AAHR3kilZ08A8rO_dVMsvIXMGh0oiDCJ7nE'
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

# Функция для получения цены с MEXC
def get_mexc_price(symbol='BTCUSDT'):
    try:
        url = f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol}"
        response = requests.get(url)
        data = response.json()
        return float(data['price'])
    except Exception as e:
        print(f"Ошибка при получении цены с MEXC: {e}")
        return None

# Функция для получения цены с Gate.io
def get_gateio_price(symbol='BTC_USDT'):
    try:
        url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={symbol}"
        response = requests.get(url)
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            return float(data[0]['last'])
        return None
    except Exception as e:
        print(f"Ошибка при получении цены с Gate.io: {e}")
        return None

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start_command(message):
    welcome_text = """
Привет! Я бот для отслеживания курсов криптовалют.

Доступные команды:
/price <монета> - Узнать текущую цену (например: /price BTC)
/exchanges - Список поддерживаемых бирж
"""
    bot.send_message(message.chat.id, welcome_text)

# Обработчик команды /exchanges
@bot.message_handler(commands=['exchanges'])
def exchanges_command(message):
    exchanges_text = """
Я поддерживаю следующие биржи:
- MEXC (https://www.mexc.com)
- Gate.io (https://www.gate.io)
"""
    bot.send_message(message.chat.id, exchanges_text)

# Обработчик команды /price
@bot.message_handler(commands=['price'])
def price_command(message):
    try:
        # Получаем аргумент команды (название монеты)
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "Пожалуйста, укажите монету. Например: /price BTC")
            return
        
        coin = args[1].upper()
        
        # Получаем цены с бирж
        mexc_price = get_mexc_price(f"{coin}USDT")
        gateio_price = get_gateio_price(f"{coin}_USDT")
        
        # Формируем ответ
        response_text = f"Текущий курс {coin}:\n"
        
        if mexc_price is not None:
            response_text += f"🔹 MEXC: {mexc_price:.8f} USDT\n"
        else:
            response_text += "🔹 MEXC: данные недоступны\n"
            
        if gateio_price is not None:
            response_text += f"🔹 Gate.io: {gateio_price:.8f} USDT\n"
        else:
            response_text += "🔹 Gate.io: данные недоступны\n"
        
        # Добавляем разницу между биржами
        if mexc_price is not None and gateio_price is not None:
            difference = abs(mexc_price - gateio_price)
            percentage_diff = (difference / min(mexc_price, gateio_price)) * 100
            response_text += f"\nРазница: {difference:.8f} USDT ({percentage_diff:.4f}%)"
        
        bot.reply_to(message, response_text)
    
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка: {str(e)}")

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