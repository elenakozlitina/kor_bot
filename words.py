import re
import pandas as pd
import psycopg2
from googletrans import Translator  # Синхронный переводчик
import time
from tqdm import tqdm  # Прогресс-бар

# 🔹 Данные для подключения к PostgreSQL
DB_CONFIG = {
    "dbname": "korean_bot",
    "user": "bot_user",
    "password": "ofmine",
    "host": "localhost"
}

# 🔹 Авторизация в базе данных PostgreSQL
def connect_to_db(config):
    try:
        conn = psycopg2.connect(**config)
        return conn
    except Exception as e:
        print(f"Ошибка подключения к базе данных: {e}")
        return None

# 🔹 Создание таблицы в PostgreSQL (если она ещё не существует)
def create_table():
    conn = connect_to_db(DB_CONFIG)
    if conn:
        cursor = conn.cursor()
        
        # SQL запрос для создания таблицы, если она еще не существует
        create_table_query = """
        CREATE TABLE IF NOT EXISTS words_table (
            id SERIAL PRIMARY KEY,
            word VARCHAR(255) NOT NULL,
            translation TEXT NOT NULL,
            level INTEGER NOT NULL,
            UNIQUE (word)
        );
        """
        
        try:
            cursor.execute(create_table_query)
            conn.commit()
            print("Таблица успешно создана или уже существует.")
        except Exception as e:
            print(f"Ошибка при создании таблицы: {e}")
        finally:
            cursor.close()
            conn.close()

# 🔹 Чтение данных с компьютера (Excel)
file_path = 'all_words.xlsx'  # Путь к вашему Excel файлу
df = pd.read_excel(file_path)

# 🔹 Инициализация переводчика
translator = Translator()

# 🔹 Функция для извлечения числового значения из строки уровня (например, "1급" => 1)
def extract_level(level_str):
    # Используем регулярное выражение для извлечения цифры перед корейским символом "급"
    match = re.match(r"(\d+)", level_str)
    if match:
        return int(match.group(1))  # Возвращаем целое число
    return None  # Если не найдено, возвращаем None

# 🔹 Функция для перевода и обработки данных
def process_data(row):
    word = row['어휘']
    level_str = row['등급']
    
    # Извлекаем числовое значение уровня
    level = extract_level(level_str)
    
    if level is None:
        print(f"Ошибка уровня для слова: {word}. Пропускаем это слово.")
        return None
    
    try:
        # Перевод слова на русский
        translation_result = translator.translate(word, src='ko', dest='ru')
        
        # Проверка на успешность перевода
        if translation_result and translation_result.text:
            translation = translation_result.text
        else:
            translation = "Не удалось перевести"
            print(f"Ошибка перевода для слова: {word}")
    except Exception as e:
        translation = "Ошибка перевода"
        print(f"Ошибка при попытке перевести слово {word}: {e}")
    
    return (word, translation, level)

# 🔹 Функция для загрузки данных в PostgreSQL
def upload_to_postgresql(batch_data):
    conn = connect_to_db(DB_CONFIG)
    if conn:
        cursor = conn.cursor()
        
        # Создаем запрос для вставки данных в таблицу
        insert_query = """
        INSERT INTO words_table (word, translation, level)
        VALUES (%s, %s, %s)
        ON CONFLICT (word) DO UPDATE
        SET level = EXCLUDED.level, translation = EXCLUDED.translation;
        """
        
        # Перебор всех строк и вставка в базу данных
        for word, translation, level in batch_data:
            cursor.execute(insert_query, (word, translation, level))
        
        # Сохраняем изменения и закрываем соединение
        conn.commit()
        cursor.close()
        conn.close()

# 🔹 Основной процесс обработки данных
batch_size = 10  # Разделяем на пачки по 10 слов
batch_data = []  # Список для хранения пачки данных для отправки в базу

# Создаем таблицу перед загрузкой данных
create_table()

# Применяем функцию ко всем строкам с использованием tqdm для прогресса
for index, row in tqdm(df.iterrows(), total=df.shape[0], desc="Обработка данных"):
    result = process_data(row)
    
    if result:
        batch_data.append(result)
    
    # Если накопилось 10 слов, отправляем в базу данных
    if len(batch_data) == batch_size:
        upload_to_postgresql(batch_data)
        batch_data.clear()  # Очищаем список для следующей пачки
        time.sleep(1)  # Добавляем задержку, чтобы избежать превышения лимитов API

# Загружаем оставшиеся данные, если они есть
if batch_data:
    upload_to_postgresql(batch_data)
