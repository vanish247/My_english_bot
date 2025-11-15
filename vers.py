import asyncio
import ccxt.pro as ccxtpro
import sys
import winsound
import aiohttp
import tkinter as tk
from tkinter import ttk
from threading import Thread
import subprocess

# Настройка событийного цикла для Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Глобальный флаг для контроля выполнения цикла
running = False

async def fetch_mexc_contract_details():
    """
    Запрашивает список контрактов с MEXC и возвращает словарь вида:
    {
        "BTC_USDT": contractSize,
        "VVV_USDT": contractSize,
         ...
    }
    """
    url = "https://contract.mexc.com/api/v1/contract/detail"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            if data.get("success"):
                contracts = {}
                for item in data.get("data", []):
                    symbol = item.get("symbol")  # Например, "BTC_USDT"
                    contract_size = item.get("contractSize")
                    contracts[symbol] = contract_size
                return contracts
            else:
                print("Ошибка при получении данных контрактов с MEXC.")
                return {}

async def fetch_order_books(spot_exchange, futures_exchange, coin, order_size_usdt, entry_threshold, exit_threshold, mode):
    """
    Основная функция для получения ордербуков и расчета арбитражных возможностей.
    """
    global running
    running = True

    # Создаем экземпляры бирж
    spot_api = getattr(ccxtpro, spot_exchange)()
    futures_api = getattr(ccxtpro, futures_exchange)()

    # Выбираем рынки в зависимости от режима
    if mode == "spot_futures":
        spot_market = f"{coin}/USDT"
        futures_market = f"{coin}/USDT:USDT"
    elif mode == "futures_futures":
        spot_market = f"{coin}/USDT:USDT"  # В режиме фьючерс-фьючерс это тоже фьючерс
        futures_market = f"{coin}/USDT:USDT"

    # Получаем детали контрактов с MEXC
    mexc_contracts = await fetch_mexc_contract_details()
    futures_contract_symbol = futures_market.split(':')[0].replace('/', '_')
    contract_size = mexc_contracts.get(futures_contract_symbol)
    if contract_size is None:
        print(f"Детали контракта для {futures_contract_symbol} не найдены. Будет использовано преобразование по умолчанию (raw_volume/10).")
    else:
        print(f"Contract size для {futures_contract_symbol}: {contract_size}")

    def calculate_avg_price(orderbook, order_size, is_bid, is_mexc, contract_size=None):
        """
        Рассчитывает среднюю цену для покупки/продажи заданного размера ордера.
        """
        total_cost = 0
        total_size = 0
        orderbook_entries = orderbook['bids'] if is_bid else orderbook['asks']
        for entry in orderbook_entries:
            if len(entry) < 2:
                continue
            price = float(entry[0])
            if is_mexc:
                raw_volume = float(entry[1])
                if contract_size is not None:
                    coin_amount = raw_volume * contract_size if contract_size > 1 else raw_volume / contract_size
                else:
                    coin_amount = raw_volume
            else:
                coin_amount = float(entry[1])
            usdt_available = coin_amount * price
            trade_amount = min(usdt_available, order_size - total_cost)
            total_cost += trade_amount
            total_size += trade_amount / price
            if total_cost >= order_size:
                break
        avg_price = total_cost / total_size if total_size > 0 else None
        return avg_price

    def play_signal(frequency_base, diff_percent, threshold):
        """
        Воспроизводит звуковой сигнал с частотой, зависящей от превышения порога.
        """
        if diff_percent > threshold:
            frequency = frequency_base + int((diff_percent - threshold) * 100)
            duration = 500  # длительность в мс
            winsound.Beep(frequency, duration)

    try:
        # Подключаемся к WebSocket и получаем ордербуки
        while running:
            tasks = [
                spot_api.fetch_order_book(spot_market),
                futures_api.watch_order_book(futures_market)
            ]
            order_books = await asyncio.gather(*tasks)
            spot_order_book, futures_order_book = order_books

            # Рассчитываем средние цены для входа и выхода
            avg_buy_price_spot = calculate_avg_price(spot_order_book, order_size_usdt, is_bid=False, is_mexc=False)
            avg_sell_price_spot = calculate_avg_price(spot_order_book, order_size_usdt, is_bid=True, is_mexc=False)
            avg_buy_price_futures = calculate_avg_price(futures_order_book, order_size_usdt, is_bid=False, is_mexc=True, contract_size=contract_size)
            avg_sell_price_futures = calculate_avg_price(futures_order_book, order_size_usdt, is_bid=True, is_mexc=True, contract_size=contract_size)

            # Вычисляем разницу в процентах для входа и выхода
            entry_diff_percent = ((avg_sell_price_futures - avg_buy_price_spot) / avg_buy_price_spot) * 100 if avg_buy_price_spot and avg_sell_price_futures else None
            exit_diff_percent = ((avg_sell_price_spot - avg_buy_price_futures) / avg_buy_price_futures) * 100 if avg_sell_price_spot and avg_buy_price_futures else None

            # Проверка порогов и воспроизведение звукового сигнала
            if entry_diff_percent is not None and entry_diff_percent >= entry_threshold:
                play_signal(1000, entry_diff_percent, entry_threshold)
                print("\nПорог входа достигнут!")

            if exit_diff_percent is not None and exit_diff_percent >= exit_threshold:
                play_signal(1500, exit_diff_percent, exit_threshold)
                print("\nПорог выхода достигнут!")

            # Обновление вывода
            sys.stdout.write(
                f"\r{spot_exchange} Spot - Avg Buy Price: {avg_buy_price_spot if avg_buy_price_spot else 'N/A'}, Avg Sell Price: {avg_sell_price_spot if avg_sell_price_spot else 'N/A'}\n"
                f"{futures_exchange} Futures - Avg Buy Price: {avg_buy_price_futures if avg_buy_price_futures else 'N/A'}, Avg Sell Price: {avg_sell_price_futures if avg_sell_price_futures else 'N/A'}\n"
                f"Entry Diff: {f'{entry_diff_percent:.2f}%' if entry_diff_percent is not None else 'N/A'}\n"
                f"Exit Diff: {f'{exit_diff_percent:.2f}%' if exit_diff_percent is not None else 'N/A'}"
            )
            sys.stdout.flush()

    except Exception as e:
        print(f"\nОшибка: {e}")

    finally:
        # Закрываем соединения
        await spot_api.close()
        await futures_api.close()
        running = False

def start_script():
    """
    Функция, запускаемая при нажатии кнопки "Запустить".
    """
    global running
    if not running:
        spot_exchange = spot_exchange_combobox.get()
        futures_exchange = futures_exchange_combobox.get()
        coin = coin_entry.get()
        order_size_usdt = float(order_size_entry.get())
        entry_threshold = float(entry_threshold_entry.get())
        exit_threshold = float(exit_threshold_entry.get())
        mode = mode_combobox.get()

        # Запуск асинхронной задачи в отдельном потоке
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(fetch_order_books(spot_exchange, futures_exchange, coin, order_size_usdt, entry_threshold, exit_threshold, mode))

        Thread(target=run_async, daemon=True).start()

def stop_script():
    """
    Функция, запускаемая при нажатии кнопки "Остановить".
    """
    global running
    running = False

def open_coin_in_new_window():
    """
    Открывает новое окно командной строки с выбранными биржами и монетой.
    """
    spot_exchange = spot_exchange_combobox.get()
    futures_exchange = futures_exchange_combobox.get()
    coin = coin_entry.get()
    mode = mode_combobox.get()

    # Определяем, какие биржи использовать в зависимости от режима
    if mode == "spot_futures":
        first_exchange = spot_exchange
        second_exchange = futures_exchange
    elif mode == "futures_futures":
        first_exchange = futures_exchange
        second_exchange = futures_exchange

    # Запуск нового процесса Python
    command = [
        sys.executable,  # Исполняемый файл Python
        __file__,  # Текущий скрипт
        "--spot_exchange", first_exchange,
        "--futures_exchange", second_exchange,
        "--coin", coin,
        "--mode", mode,
    ]
    subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE)

# Создание графического интерфейса
root = tk.Tk()
root.title("Arbitrage Bot")

# Выбор бирж
ttk.Label(root, text="Выберите биржу для спота:").grid(column=0, row=0)
spot_exchange_combobox = ttk.Combobox(root, values=["mexc", "bitget", "gateio", "lbank", "bybit"])
spot_exchange_combobox.grid(column=1, row=0)
spot_exchange_combobox.current(0)

ttk.Label(root, text="Выберите биржу для фьючерсов:").grid(column=0, row=1)
futures_exchange_combobox = ttk.Combobox(root, values=["mexc", "bitget", "gateio", "lbank", "bybit"])
futures_exchange_combobox.grid(column=1, row=1)
futures_exchange_combobox.current(0)

# Ввод монеты
ttk.Label(root, text="Введите монету (например, BTC):").grid(column=0, row=2)
coin_entry = ttk.Entry(root)
coin_entry.grid(column=1, row=2)

# Ввод размера ордера
ttk.Label(root, text="Размер ордера в USDT:").grid(column=0, row=3)
order_size_entry = ttk.Entry(root)
order_size_entry.grid(column=1, row=3)

# Ввод порогов
ttk.Label(root, text="Порог входа (%):").grid(column=0, row=4)
entry_threshold_entry = ttk.Entry(root)
entry_threshold_entry.grid(column=1, row=4)

ttk.Label(root, text="Порог выхода (%):").grid(column=0, row=5)
exit_threshold_entry = ttk.Entry(root)
exit_threshold_entry.grid(column=1, row=5)

# Выбор режима (спот-фьючерс или фьючерс-фьючерс)
ttk.Label(root, text="Режим:").grid(column=0, row=6)
mode_combobox = ttk.Combobox(root, values=["spot_futures", "futures_futures"])
mode_combobox.grid(column=1, row=6)
mode_combobox.current(0)

# Кнопки запуска и остановки
start_button = ttk.Button(root, text="Запустить", command=start_script)
start_button.grid(column=0, row=7)

stop_button = ttk.Button(root, text="Остановить", command=stop_script)
stop_button.grid(column=1, row=7)

# Кнопка для открытия монеты в новом окне
open_coin_button = ttk.Button(root, text="Открыть монету в новом окне", command=open_coin_in_new_window)
open_coin_button.grid(column=0, row=8, columnspan=2)

root.mainloop()