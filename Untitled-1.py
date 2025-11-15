user_id = 12345
user_words = {user_id:['apple - яблоко', 'fly - летать']}

next_message = "car - mashina, say - govorit"
next_words = [w.strip() for w in next_message.split(',')] # удаляет проблемы и разделяет слова
# print(next_words)
user_words[user_id].extend(next_words)

print(user_words.get(user_id)) # Вернет None, а не ошибку, если ключа нет


