import asyncpg
import gspread
import pandas as pd
import psycopg2
from google.oauth2.service_account import Credentials

# 🔹 Данные для подключения к PostgreSQL
DB_CONFIG = {
    "dbname": "korean_bot",
    "user": "bot_user",
    "password": "ofmine",
    "host": "localhost"
}

# 🔹 Данные для подключения к Google Sheets
GOOGLE_SHEETS_CREDENTIALS = "credentials.json"  # Путь к JSON-файлу с API-ключамие
SPREADSHEET_NAME = "Корейский Алфавит"  # Имя таблицы в Google Sheets
SHEET_NAME = "Корейский Алфавит"  # Название листа в таблице


TABLE_NAME = "alphabet_table"  # Имя таблицы в БД (можно менять)


# 🔹 Авторизация Google Sheets
def authorize_google_sheets(credentials_path):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(credentials_path, scopes=scope)
    return gspread.authorize(creds)


# 🔹 Получение данных из Google Sheets
def get_google_sheets_data(client, spreadsheet_name, sheet_name):
    spreadsheet = client.open(spreadsheet_name)
    sheet = spreadsheet.worksheet(sheet_name)
    data = sheet.get_all_records()
    return pd.DataFrame(data)


# 🔹 Подключение к базе данных
def connect_db():
    return psycopg2.connect(**DB_CONFIG)


# 🔹 Создание таблицы (автоматически определяет типы данных)
def create_table(df, table_name):
    with connect_db() as conn:
        with conn.cursor() as cur:
            columns = []
            for col in df.columns:
                columns.append(f'"{col}" TEXT')  # Все поля текстовые (можно улучшить)
            
            columns_sql = ", ".join(columns)
            query = f"""
            CREATE TABLE IF NOT EXISTS "{table_name}" (
                id SERIAL PRIMARY KEY,
                {columns_sql}
            );
            """
            cur.execute(query)
            conn.commit()


# 🔹 Получение существующих записей (по первому столбцу как уникальному ключу)
def get_existing_entries(table_name, key_column):
    query = f'SELECT "{key_column}" FROM "{table_name}";'
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            return {row[0] for row in cur.fetchall()}



# 🔹 Вставка новых данных в базу
def insert_new_data(df, table_name):
    key_column = df.columns[0]  # Используем первый столбец как уникальный ключ
    existing_entries = get_existing_entries(table_name, key_column)
    new_data = df[~df[key_column].isin(existing_entries)]  # Оставляем только новые записи

    if new_data.empty:
        print("✅ Новых данных нет, всё уже в базе.")
        return

    placeholders = ", ".join(["%s"] * len(new_data.columns))
    query = f'INSERT INTO "{table_name}" ({", ".join([f'"{col}"' for col in new_data.columns])}) VALUES ({placeholders})'

    with connect_db() as conn:
        with conn.cursor() as cur:
            for _, row in new_data.iterrows():
                cur.execute(query, tuple(row))
            conn.commit()

    print(f"✅ Добавлено {len(new_data)} новых записей!")



# 🔹 Основная функция
def main():
    client = authorize_google_sheets(GOOGLE_SHEETS_CREDENTIALS)
    df = get_google_sheets_data(client, SPREADSHEET_NAME, SHEET_NAME)

    if df.empty:
        print("⚠️ Внимание: Google Sheets пуст!")
        return

    create_table(df, TABLE_NAME)  # Создаем таблицу, если её нет
    insert_new_data(df, TABLE_NAME)  # Добавляем только новые данные


# 🔹 Запуск скрипта
if __name__ == "__main__":
    main()
