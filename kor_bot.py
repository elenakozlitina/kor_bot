from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import gspread
from google.oauth2.service_account import Credentials
import random
import asyncio
import logging
import json 
import os
import asyncpg
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential


load_dotenv()

# Инициализация базы данных

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(os.getenv("DB_URL"))

    async def get_user(self, user_id: int):
        return await self.pool.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

    async def create_user(self, user_id: int):
        await self.pool.execute("""
            INSERT INTO users (user_id) VALUES ($1) 
            ON CONFLICT (user_id) DO NOTHING
        """, user_id)

    async def update_progress(self, user_id: int, score: int, current_letter_index: int):
        await self.pool.execute("""
            UPDATE users 
            SET score = score + $1, current_letter_index = $2 
            WHERE user_id = $3
        """, score, current_letter_index, user_id)

    async def add_learned_word(self, user_id: int, word: str, translation: str, level: int, image_url: str = None):
        await self.pool.execute("""
            INSERT INTO learned_words (user_id, word, translation, level, image_url)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, word) DO NOTHING
        """, user_id, word, translation, level, image_url)

    async def get_learned_words(self, user_id: int, level: int = None):
        query = "SELECT * FROM learned_words WHERE user_id = $1"
        params = [user_id]
        if level is not None:
            query += " AND level = $2"
            params.append(level)
        return await self.pool.fetch(query, *params)

    async def delete_subscriber(self, user_id: int):
        await self.pool.execute("DELETE FROM subscriptions WHERE user_id = $1", user_id)

    async def add_subscriber(self, user_id: int):
        try:
            # Добавляем пользователя в таблицу users (если его ещё нет)
            await self.pool.execute("""
                INSERT INTO users (user_id) 
                VALUES ($1) 
                ON CONFLICT (user_id) DO NOTHING
            """, user_id)

            # Добавляем пользователя в таблицу subscriptions
            await self.pool.execute("""
                INSERT INTO subscriptions (user_id) 
                VALUES ($1) 
                ON CONFLICT (user_id) DO NOTHING
            """, user_id)
        except Exception as e:
            logging.error(f"Ошибка при добавлении подписчика: {e}")

    async def get_subscribers(self):
        return await self.pool.fetch("SELECT user_id FROM subscriptions")
    async def close(self):
        if self.pool:
            await self.pool.close()
db = Database()



async def handle_channel_post(update: Update, context: CallbackContext):
    try:
        # Проверяем username канала (без @)
        if update.channel_post.chat.username.lower() != "topik2prep":
            return
            
        subscribers = await db.get_subscribers()  # Асинхронный вызов
        
        for user_id in subscribers:
            try:
                await context.bot.forward_message(
                    chat_id=user_id['user_id'],  # Исправлено: user_id из базы данных
                    from_chat_id=update.channel_post.chat.id,
                    message_id=update.channel_post.message_id
                )
            except Exception as e:
                print(f"Ошибка для {user_id}: {e}")
                await db.delete_subscriber(user_id['user_id'])  # Асинхронный вызов
        
    except Exception as e:
        print(f"Общая ошибка: {e}")
        
# Авторизация Google Sheets
scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
         "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]

# Используем service_account.json для авторизации
creds = Credentials.from_service_account_file('credentials.json', scopes=scope)
client = gspread.authorize(creds)
spreadsheet = client.open("Корейский Алфавит")
sheet = spreadsheet.get_worksheet(0)  # Первый лист таблицы

# Словарь для хранения прогресса пользователя
user_progress = {}

# Глобальные переменные
grammar_data = []
phrases_data = []


# Настройка
SOURCE_CHANNEL_ID = "@topik2prep"  # Канал-источник

async def unsubscribe(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    await db.delete_subscriber(user_id)  # Асинхронный вызов
    await update.message.reply_text("Вы отписались от рассылки 😢")


async def send_daily_post(context: CallbackContext):
    try:
        # Получаем последний пост из канала
        channel_id = "@topik2prep"
        posts = await context.bot.get_chat(chat_id=channel_id, limit=1)
        
        # Получаем список подписчиков
        subscribers = await db.get_subscribers()  # Асинхронный вызов
        
        # Пересылаем пост всем подписчикам
        for user_id in subscribers:
            await context.bot.forward_message(
                chat_id=user_id['user_id'],  # Исправлено: user_id из базы данных
                from_chat_id=channel_id,
                message_id=posts[0].message_id
            )
    except Exception as e:
        print(f"Ошибка: {e}")

# Словарь для подписчиков
subscribers = set()

async def return_to_menu(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    # Убираем удаление current_word_index
    if user_id in user_progress:
        if "current_word" in user_progress[user_id]:
            del user_progress[user_id]["current_word"]

    menu_text = "Вы вернулись в главное меню. Выберите, что вам интересно: 👇"
    
    # Создаем клавиатуру с кнопками
    keyboard = [
        ["Хангыль", "Подготовка к ТОПИКу"],
        ["Мой словарь", "Учить новые слова"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Отправляем сообщение с меню
    await update.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode="HTML")
    if "mode" in context.user_data:
        del context.user_data["mode"]


async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    await db.add_subscriber(user_id)
    
    
    welcome_text0 = """ <b>Привет! 👋 </b> """
    welcome_text00 = """
<b>Добро пожаловать в ProMol — твоего личного помощника в изучении корейского языка!</b>  🇰🇷🎉
Здесь ты сможешь не только учить корейский, но и погрузиться в культуру, язык и традиции Кореи. 
Вот что тебя ждет:
    """
    welcome_text1 = """
🌟 <b>Хангыль</b>   🅰️
Изучай корейский алфавит с нуля! Мы поможем тебе разобраться в каждой букве, научим правильно произносить звуки и покажем примеры слов.

🌟 <b>Разговорные фразы</b>   💬
Каждый день ты будешь получать новую полезную фразу, которую можно использовать в реальной жизни. Это поможет тебе быстрее заговорить на корейском!

🌟 <b>Грамматика</b>  📚
Разбирайся в сложных грамматических конструкциях с простыми объяснениями и примерами. Мы сделаем грамматику понятной и доступной.

🌟 <b>Подготовка к TOPIK</b>   🎓
Готовься к экзамену TOPIK с нами! Мы предоставим тебе материалы, тесты и советы для успешной сдачи.

🌟 <b>Учить новые слова</b>  🌱
Пополняй свой словарный запас каждый день! Новые слова сохраняются в твой личный словарь, чтобы ты мог повторять их в любое время.

🌟 <b>Мой словарь</b>  📖
Все выученные слова сохраняются в твоем личном словаре. Ты можешь просматривать их, повторять и отслеживать свой прогресс.

🌟 <b>Ежедневные уведомления</b>  ⏰
Каждый день ты будешь получать новую разговорную фразу, грамматическую конструкцию или полезное слово. Это поможет тебе учить корейский регулярно и без усилий!
    """
    welcome_text2 = """ 
<b>С чего начнем?👇
Выбери категорию, и мы начнем твое путешествие в мир корейского языка!</b> """
    
    # Создаем клавиатуру с кнопками
    keyboard = [
        ["Хангыль","Подготовка к ТОПИКу"],
        ["Мой словарь", "Учить новые слова"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Отправляем приветственное сообщение
    await update.message.reply_text(welcome_text0, reply_markup=reply_markup, parse_mode="HTML")
    await asyncio.sleep(0.5)
    await update.message.reply_text(welcome_text00, reply_markup=reply_markup, parse_mode="HTML")
    await asyncio.sleep(1)
    await update.message.reply_text(welcome_text1, reply_markup=reply_markup, parse_mode="HTML")
    await asyncio.sleep(2)
    await update.message.reply_text(welcome_text2, reply_markup=reply_markup, parse_mode="HTML")


async def handle_spelling_input(update: Update, context: CallbackContext):
    user_input = update.message.text.strip()
    check_data = context.user_data.get("spelling_check")
    
    if not check_data:
        # Полностью очищаем состояние перед возвратом
        context.user_data.pop("awaiting_spelling", None)
        context.user_data.pop("spelling_check", None)
        context.user_data["mode"] = "learn"
        await update.message.reply_text("Проверка завершена, продолжаем обучение!")
        return

    if user_input.lower() == "выйти":
        # Очищаем состояние перед выходом
        context.user_data.pop("awaiting_spelling", None)
        context.user_data.pop("spelling_check", None)
        context.user_data["mode"] = None
        await return_to_menu(update, context)
        return

    if user_input == check_data['word']:
        await update.message.reply_text("✅ Верно! Молодец!")
    else:
        await update.message.reply_text(f"❌ Неверно. Правильный ответ: {check_data['word']}")

    # Полностью сбрасываем состояние проверки и переходим в режим изучения
    context.user_data.pop("awaiting_spelling", None)
    context.user_data.pop("spelling_check", None)
    context.user_data["mode"] = "learn"

    await send_word(update, context)


async def handle_message(update: Update, context: CallbackContext):
    user_input = update.message.text.strip()
    user_id = update.message.from_user.id

    # Обработка режима проверки написания в первую очередь
    if context.user_data.get("mode") == "spelling_check":
        await handle_spelling_input(update, context)
        return

    async def clear_user_state(context):
        """Очищает состояние пользователя."""
        keys_to_delete = [
            "current_words", "current_word_index", "correct_translation",
            "current_options", "awaiting_retry", "awaiting_letter_input",
            "awaiting_dictionary_level", "awaiting_input"
        ]
        for key in keys_to_delete:
            if key in context.user_data:
                del context.user_data[key]

    try:
        # Проверяем команду "выйти" на самом начале
        if user_input.lower() == "выйти":
            await return_to_menu(update, context)
            await clear_user_state(context)  # Очищаем все данные пользователя
            return  # Завершаем выполнение, чтобы не продолжать обработку ввода
            # Проверяем режимы
        processed = False
        if context.user_data.get("mode") == "learn":
            processed = await check_word_translation(update, context)
        elif context.user_data.get("mode") == "game":
            processed = await check_game_translation(update, context)
        elif context.user_data.get("mode") == "spelling_check":
            if await handle_spelling_input(update, context):  # Проверка написания слов
                return

        if processed:
            return  # Сообщение обработано, выходим

        # Инициализируем прогресс пользователя, если его нет
        user = await db.get_user(user_id)
        if not user:
            await db.create_user(user_id)
            user = await db.get_user(user_id)

        # Проверяем состояние ввода буквы/слова
        if "awaiting_input" in context.user_data:  
            await check_user_response(update, context)
            return

        # Если пользователь выбрал категорию
        if user_input in ["Хангыль", "Разговорные фразы", "Грамматика", "Подготовка к ТОПИКу"]:
            await clear_user_state(context)
            await handle_choice(update, context)

        elif user_input == "Мой словарь":
            await clear_user_state(context)
            await handle_my_dictionary(update, context)

        elif user_input == "Учить новые слова":
            await clear_user_state(context)
            await handle_learn_new_words(update, context)

        elif user_input == "Что за буква?":
            await clear_user_state(context)
            await handle_what_is_letter(update, context)

        elif user_input == "Изучать буквы":
            # Не перезаписываем существующий прогресс
            if user_id not in user_progress:
                user_progress[user_id] = {
                    "current_letter_index": 0,
                    "learned_words": [],
                    "score": 0
                }
            elif "current_letter_index" not in user_progress[user_id]:
                user_progress[user_id]["current_letter_index"] = 0

            await send_letters_and_words(update, context, user_id)
        elif user_input == "Играть еще раз 🔄":
            await send_next_game_word(update, context)  # Перезапускаем игру
            return

        elif "awaiting_letter_input" in context.user_data and context.user_data["awaiting_letter_input"]:
            await handle_letter_input(update, context)

        elif user_input.lower() == "играть":
            await handle_learn_from_dictionary(update, context)
        elif user_input.lower() == "очистить словарь":
            await handle_clear_dictionary(update, context)
        elif "awaiting_spelling" in context.user_data:
            await handle_spelling_input(update, context)  # Исправлено название

        elif user_input.isdigit() and 1 <= int(user_input) <= 6:
            if "current_words" in context.user_data:
                await check_word_translation(update, context)
            else:
                # Если пользователь в разделе "Мой словарь", обрабатываем выбор уровня
                if "awaiting_dictionary_level" in context.user_data:
                    await handle_my_dictionary_level(update, context)
                    if "awaiting_dictionary_level" in context.user_data:
                        del context.user_data["awaiting_dictionary_level"]  # Очищаем состояние
                else:
                    await handle_learn_new_words_level(update, context)

        elif "awaiting_retry" in context.user_data and context.user_data["awaiting_retry"]:
            # Обработка повторного ввода после подсказки
            await check_word_translation(update, context)

        else:
            # Если пользователь ввёл что-то неожиданное
            await update.message.reply_text("Пожалуйста, выбери номер варианта или нажми 'Выйти'.")

    except Exception as e:
        print(f"Unexpected error: {e}")
        await update.message.reply_text(f"Произошла ошибка: {e}")



async def handle_letter_input(update: Update, context: CallbackContext): # Что за буква логика 
    user_input = update.message.text.strip()

    # Проверяем, хочет ли пользователь выйти в меню
    if user_input.lower() == "выйти":
        await return_to_menu(update, context)
        return

    letters_data = await get_letters_data()
    if not letters_data:
        await update.message.reply_text("Ошибка при получении данных. Попробуйте позже.")
        return

    # Ищем букву в данных
    for letter_data in letters_data:
        if letter_data['Буква'] == user_input:
            letter = letter_data['Буква']
            example_word = letter_data['Пример'].strip()
            transliteration = letter_data['Транслит']
            translation = letter_data['Перевод']
            sound = letter_data['Звук']
            features = letter_data['Особенности']

            # Отправляем информацию о букве
            await update.message.reply_text(
                f"<b>Буква:</b> {letter} {sound}\n"
                f"<b>Описание:</b> {features}\n"
                f"<b>Пример слова:</b> {example_word} ({transliteration}) — {translation}",
                parse_mode="HTML"
            )
            return

    # Если буква не найдена
    await update.message.reply_text("Буква не найдена. Попробуй ещё раз.")


async def handle_what_is_letter(update: Update, context: CallbackContext): # Что за буква логика 2
    user_input = update.message.text.strip()
    
    # Проверяем выход в меню
    if user_input.lower() == "выйти":
        await return_to_menu(update, context)
        return

    user_id = update.message.from_user.id

    # Очищаем только временные состояния пользователя
    if user_id in user_progress:
        # Удаляем текущее слово, но сохраняем прогресс букв
        if "current_word" in user_progress[user_id]:
            del user_progress[user_id]["current_word"]
        
    # Очищаем контекстные данные
    keys_to_remove = [
        "current_words", 
        "current_word_index", 
        "correct_translation",
        "current_options", 
        "awaiting_retry"
    ]
    
    for key in keys_to_remove:
        if key in context.user_data:
            del context.user_data[key]

    # Устанавливаем состояние ожидания ввода буквы
    context.user_data["awaiting_letter_input"] = True

    await update.message.reply_text(
        "Введи название буквы, и я покажу всю информацию о ней:"
    )



async def send_letters_and_words(update: Update, context: CallbackContext, user_id: int):
    letters_data = sheet.get_all_records()
    
    # Получаем данные пользователя из базы данных
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id)
        user = await db.get_user(user_id)
    
    current_index = user['current_letter_index']
    
    if current_index >= len(letters_data):
        await update.message.reply_text("Вы изучили все буквы! 🎉")
        return

    # Определяем категорию букв
    if current_index < 10:  # Обычные гласные (2-11 строки)
        if current_index == 0:
            category_text0 = """<b>📚 Прежде чем начать изучение Хангыля, важно запомнить несколько правил:</b>"""
            category_text1 = """
    1. Буквы формируются слева-направо и сверху-вниз. Например, ㄴ (н) + ㅕ (ё/йо) + ㄴ (н) = 년 (нйон) — год.
    2. Не пугайтесь, если видите на письме, что перед гласной стоит кружочек. Это норма для написания гласных в начале слова. Никак не влияет на произношение. Например, в слове 아버님 — «отец».
    3. Помните, что произношение согласной зависит от положения в слове/слоге, «соседства» с другими буквами.
            """
            category_text2 ="""<b>Теперь давай начнем с первых букв:</b>"""
            await update.message.reply_text(category_text0, parse_mode="HTML")
            await asyncio.sleep(1)
            await update.message.reply_text(category_text1, parse_mode="HTML")
            await asyncio.sleep(12)
            await update.message.reply_text(category_text2, parse_mode="HTML")
            await asyncio.sleep(1)

    elif current_index < 20:  # Обычные согласные (12-20 строки)
        if current_index == 10:
            category_text = """
            <b>Обычные согласные — это основные согласные звуки, которые часто используются в словах.</b>
            """
            await update.message.reply_text(category_text, parse_mode="HTML")
            await asyncio.sleep(1)
    elif current_index < 25:  # Придыхательные гласные (21-25 строки)
        if current_index == 20:
            category_text = """
            <b>Придыхательные гласные произносятся мягче обычных, но в целом они образуют уникальные звуки.</b>
            """
            await update.message.reply_text(category_text, parse_mode="HTML")
            await asyncio.sleep(1)
    elif current_index < 35:  # Дифтонги (26-36 строки)
        if current_index == 25:
            category_text = """
            <b>Дифтонги — сложные гласные, которые формируются из двух букв и произносятся как один звук.</b>
            """
            await update.message.reply_text(category_text, parse_mode="HTML")
            await asyncio.sleep(1)
    elif current_index < 41:  # Сдвоенные согласные (37-41 строки)
        if current_index == 35:
            category_text = """
            <b>Сдвоенные согласные — это буквы, которые произносятся в два раза сильнее обычных.</b>
            """
            await update.message.reply_text(category_text, parse_mode="HTML")
            await asyncio.sleep(1)

       # Получаем данные текущей буквы
    current_letter_data = letters_data[current_index]
    letter = current_letter_data['Буква']
    example_word = current_letter_data['Пример'].strip()
    transliteration = current_letter_data['Транслит']
    translation = current_letter_data['Перевод']
    features = current_letter_data['Особенности']
    sound = current_letter_data['Звук']
    image_url = current_letter_data['Изображение'].strip()  # URL изображения

    # Сохраняем данные в контекст
    context.user_data.update({
        "current_letter": letter,
        "current_word": example_word,
        "awaiting_input": "AWAITING_LETTER"  # Устанавливаем состояние ожидания буквы
    })

    try:
        # Отправляем изображение с описанием буквы
        if image_url:
            await update.message.reply_photo(
                photo=image_url,
                caption=f"<b>Изучи букву: {letter}</b> {sound}\n{features}",
                parse_mode="HTML"
            )
            # Отправляем пример слова
            await update.message.reply_text(
                f"<b>Пример слова:</b> {example_word} ({transliteration}) — {translation}\n\n",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"<b>Изучи букву: {letter}</b>{sound} \n{features}", parse_mode="HTML")
            # Отправляем пример слова
            await update.message.reply_text(
                f"<b>Пример слова:</b> {example_word} ({transliteration}) — {translation}\n\n",
                parse_mode="HTML"
            )

        # Запрашиваем ввод буквы
        await update.message.reply_text("➡️ Напиши эту букву:", parse_mode="HTML")

        # Обновляем прогресс в БД
        await db.update_progress(
            user_id=user_id,
            score=0,  # или добавляем очки если нужно
            current_letter_index=current_index + 1
        )

    except Exception as e:
        print(f"Ошибка отправки: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при загрузке материалов")




async def check_user_response(update: Update, context: CallbackContext):
    user_input = update.message.text.strip()  # Убираем лишние пробелы
    user_id = update.message.from_user.id

    # Проверяем, хочет ли пользователь выйти
    if user_input == "выйти":
        await return_to_menu(update, context)
        return

    # Проверяем, хочет ли пользователь узнать о букве
    if user_input == "что за буква?":
        await handle_what_is_letter(update, context)
        return

    # Проверяем, есть ли текущее состояние для буквы и слова
    if user_id not in user_progress or "current_letter" not in context.user_data:
        await update.message.reply_text("Ошибка: текущее состояние не найдено. Попробуйте ещё раз.")
        return

    correct_letter = context.user_data["current_letter"]
    correct_word =context.user_data["current_word"]

    # Проверяем, правильно ли пользователь ввел букву
    if "awaiting_input" in context.user_data and context.user_data["awaiting_input"] == "AWAITING_LETTER":
        if user_input == correct_letter:  # Сравниваем без приведения к нижнему регистру
            await update.message.reply_text("✅ Правильно! Напиши пример слова.")

            # Переходим к следующему состоянию для ввода слова
            context.user_data["awaiting_input"] = "AWAITING_WORD"
            await update.message.reply_text(f"➡️ Напиши слово с буквой {correct_letter}: {correct_word}")
        else:
            await update.message.reply_text("❌ Неверно. Попробуй еще раз. Напиши букву:")

    # Если буква была введена правильно, проверяем слово
    elif "awaiting_input" in context.user_data and context.user_data["awaiting_input"] == "AWAITING_WORD":
        if user_input == correct_word:  # Сравниваем без приведения к нижнему регистру
            await update.message.reply_text("✅ Правильно! 🎉")
            user = await db.get_user(user_id)
            if not user:
                await db.create_user(user_id)
                user = await db.get_user(user_id)

            await db.update_progress(
                user_id=user_id,
                score=0,  # или добавляем очки, если нужно
                current_letter_index=user['current_letter_index'] + 1
            )
            await send_letters_and_words(update, context, user_id)
        else:
            await update.message.reply_text(f"❌ Неправильно. Попробуй ещё раз: Напиши слово: {correct_word}")





# Модифицированные функции
async def handle_my_dictionary(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    async with db.pool.acquire() as conn:
        # Получаем уникальные уровни изученных слов
        levels = await conn.fetch(
            "SELECT DISTINCT wt.level FROM words_table wt JOIN users u ON wt.id::TEXT = ANY(u.learned_words) WHERE u.user_id = $1",
            user_id
        )

    if not levels:
        await update.message.reply_text("Вы пока не изучили ни одного слова. 😢")
        return

    keyboard = [[str(level['level'])] for level in sorted(levels, key=lambda x: x['level'])]
    keyboard.append(["Выйти"])
    
    await update.message.reply_text(
        "Выберите уровень слов:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    context.user_data["awaiting_dictionary_level"] = True

async def handle_my_dictionary_level(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    level = int(update.message.text)

    async with db.pool.acquire() as conn:
        # Получаем все слова уровня из БД
        words = await conn.fetch(
            "SELECT * FROM words_table WHERE level = $1", 
            level
        )
        
        # Получаем изученные слова пользователя для этого уровня
        learned_words = await conn.fetch(
            """
            SELECT wt.* 
            FROM words_table wt
            JOIN users u ON wt.id::TEXT = ANY(u.learned_words) 
            WHERE u.user_id = $1 AND wt.level = $2
            """,
            user_id, level
        )

    if not learned_words:
        await update.message.reply_text(f"На уровне {level} пока нет изученных слов.")
        return

    # Рассчитываем статистику
    total_learned = len(learned_words)
    offset = max(0, total_learned - 20)
    last_20_words = learned_words[-20:]

    # Формируем сообщение
    word_list = "\n".join(
        [f"{idx+1+offset}. {w['word']} — {w['translation']}" 
         for idx, w in enumerate(last_20_words)]
    )
    
    stats_message = (
        f"📚 Уровень {level}\n"
        f"📊 Изучено слов: {total_learned}\n\n"
        f"📖 Последние 20 изученных слов:\n{word_list}"
    )

    await update.message.reply_text(
        stats_message,
        reply_markup=ReplyKeyboardMarkup(
            [["Играть", "Очистить словарь", "Выйти"]], 
            resize_keyboard=True
        )
    )

    # Сохраняем контекст для возможной игры
    context.user_data.update({
        "current_level": level,
        "current_words": learned_words
    })

async def handle_clear_dictionary(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    await clear_learned_words(user_id)
    
    await update.message.reply_text(
        "🗑 Ваш словарь успешно очищен!",
        reply_markup=ReplyKeyboardRemove()
    )
    await return_to_menu(update, context)



async def handle_learn_new_words(update: Update, context: CallbackContext):
    # Создаем клавиатуру с уровнями и кнопкой "Выйти"
    keyboard = [["1", "2", "3"], ["4", "5", "6"], ["Выйти"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Выберите уровень слов или нажмите 'Выйти':", reply_markup=reply_markup)

async def handle_learn_new_words_level(update: Update, context: CallbackContext):
    user_input = update.message.text.strip()
    
    try:
        level = int(user_input)
        if level < 1 or level > 6:
            await update.message.reply_text("Пожалуйста, выберите уровень от 1 до 6.")
            return
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите число от 1 до 6.")
        return

    user_id = update.message.from_user.id

    try:
        async with db.pool.acquire() as conn:
            # Получаем слова уровня
            words = await conn.fetch(
                "SELECT * FROM words_table WHERE level = $1 ORDER BY st_imp DESC, random()",
                level
            )

            if not words:
                await update.message.reply_text(f"На уровне {level} пока нет слов. 😢")
                return

            # Получаем данные пользователя
            user_data = await conn.fetchrow(
                """SELECT learned_words, 
                COALESCE(learning_progress, '{}'::JSONB) as learning_progress 
                FROM users WHERE user_id = $1""",
                user_id
            )
            
            # Инициализируем прогресс
            learning_progress = user_data['learning_progress'] if user_data else {}
            if isinstance(learning_progress, str):  # Защита от строкового представления
                try:
                    learning_progress = json.loads(learning_progress)
                except:
                    learning_progress = {}

            level_progress = learning_progress.get(str(level), {'index': 0, 'words': []})
            
            learned_words = user_data['learned_words'] if user_data else []
            learned_set = set(learned_words)

            # Фильтруем новые слова
            filtered_words = [
                word for word in words 
                if str(word['id']) not in learned_set
            ]

            if not filtered_words:
                await update.message.reply_text(f"Вы уже изучили все слова уровня {level}! 🎉")
                return

            # Начинаем с сохраненной позиции
            start_index = level_progress.get('index', 0)
            if start_index >= len(filtered_words):
                start_index = 0

            # Сохраняем контекст
            context.user_data.update({
                "current_words": filtered_words,
                "current_word_index": start_index,
                "current_level": level
            })

            # Отправляем первое слово
            await send_word(update, context)
            context.user_data["mode"] = "learn"

    except Exception as e:
        logging.error(f"Ошибка при работе с базой данных: {e}")
        await update.message.reply_text("⚠ Произошла ошибка. Попробуйте позже.")

async def send_word(update, context):
    user_id = update.message.from_user.id
    index = context.user_data.get("current_word_index", 0)
    words = context.user_data.get("current_words", [])
    level = context.user_data.get("current_level")

    if index >= len(words):
        await update.message.reply_text("Вы изучили все слова! 🎉")
        return

    # Сохраняем прогресс
    async with db.pool.acquire() as conn:
        try:
            await conn.execute("""
                UPDATE users 
                SET learning_progress = 
                    COALESCE(learning_progress, '{}'::JSONB) || 
                    jsonb_build_object($1::TEXT, 
                        jsonb_build_object(
                            'index', $2::INTEGER,
                            'words', $3::JSONB
                        )
                    )
                WHERE user_id = $4
            """, 
            str(level),
            index,
            json.dumps([str(w['id']) for w in words[:index]]),
            user_id)
        except Exception as e:
            logging.error(f"Ошибка сохранения прогресса: {e}")

    
    word = words[index]
    correct_translation = word['translation']
    image_url = (word.get('image') or '').strip()
    
    try:
        if image_url:
            await update.message.reply_photo(photo=image_url, caption=f"<b>Изучим слово:</b> {word['word']}", parse_mode="HTML")
        else:
            await update.message.reply_text(f"<b>Изучим слово:</b> {word['word']}", parse_mode="HTML")
        
        all_translations = [w['translation'] for w in words if w['translation'] != correct_translation]
        random.shuffle(all_translations)
        options = [correct_translation] + all_translations[:2]
        random.shuffle(options)
        
        context.user_data.update({
            "correct_translation": correct_translation,
            "current_options": options
        })
        
        keyboard = [[str(i + 1) for i in range(len(options))], ["Выйти"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        options_text = "\n".join(f"{i+1}. {option}" for i, option in enumerate(options))
        await update.message.reply_text(
            f"<b>Слово:</b> {word['word']}\n\n"
            f"<b>Варианты:</b>\n{options_text}\n\n"
            f"Выбери правильный перевод (введи номер) или нажми 'Выйти':\n\n"
            f"Прогресс: {index + 1} из {len(words)} слов 🚀",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
        # Обновление индекса в контексте вместо БД
        context.user_data["current_word_index"] = index + 1

    except Exception as e:
        print(f"Ошибка отправки: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при загрузке слова")



# Добавляем в начало константы
INTERACTIVE_CHECK_INTERVAL = 3  # Проверка каждые 3 слова

async def check_word_translation(update: Update, context: CallbackContext):
    if context.user_data.get("mode") != "learn":
        return False

    user_input = update.message.text.strip()
    user_id = update.message.from_user.id

    if user_input.lower() == "выйти":
        context.user_data["mode"] = None
        await return_to_menu(update, context)
        return True

    if "current_options" not in context.user_data or "correct_translation" not in context.user_data:
        await update.message.reply_text("Ошибка: данные не найдены. Попробуй ещё раз.")
        return True

    correct_translation = context.user_data["correct_translation"]
    options = context.user_data["current_options"]

    try:
        selected_option = int(user_input) - 1
        if selected_option < 0 or selected_option >= len(options):
            await update.message.reply_text("Пожалуйста, выбери номер правильного варианта.")
            return

        selected_translation = options[selected_option]

        async with db.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            if not user:
                await conn.execute("INSERT INTO users(user_id) VALUES($1)", user_id)
                user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

            if selected_translation == correct_translation:
                current_word = context.user_data["current_words"][context.user_data["current_word_index"] - 1]
                
                # Обновляем прогресс в базе данных
                await conn.execute(
                    """
                    UPDATE users 
                    SET 
                        score = COALESCE(score, 0) + 10,
                        learned_words = array_append(learned_words, $1)
                    WHERE user_id = $2
                    """,
                    str(current_word['id']),  # Добавляем ID слова в learned_words
                    user_id
                )

                await update.message.reply_text(
                    f"✅ Правильно! Слово добавлено в твой словарь!\n"
                    f"💯 Твой счёт: {user.get('score', 0) + 10} баллов."
                )

                if context.user_data["current_word_index"] % 3 == 0:
                    await start_spelling_check(update, context)
                else:
                    await send_word(update, context)

            else:
                await conn.execute(
                    "UPDATE users SET score = COALESCE(score, 0) - 5 WHERE user_id = $1",
                    user_id
                )
                hint = f"Подсказка: первая буква — '{correct_translation[0]}'."
                await update.message.reply_text(
                    f"❌ Неправильно. {hint}\nПопробуй ещё раз:"
                )

    except (ValueError, IndexError):
        await update.message.reply_text("Пожалуйста, выбери номер правильного варианта.")
    except Exception as e:
        logging.error(f"Ошибка в check_word_translation: {e}")
        await update.message.reply_text("⚠ Произошла ошибка. Попробуйте позже.")

    return True


async def start_spelling_check(update: Update, context: CallbackContext): # Проверка каждого 3-4 слова изученного подряд
    # Получаем последние 3-4 изученных слова
    user_id = update.message.from_user.id
    
    async with db.pool.acquire() as conn:
        words = await conn.fetch(
            """
            SELECT wt.* 
            FROM words_table wt
            JOIN users u ON wt.id::TEXT = ANY(u.learned_words)
            WHERE u.user_id = $1
            ORDER BY random()
            LIMIT $2
            """,
            user_id, INTERACTIVE_CHECK_INTERVAL
        )

    if not words:
        return

    check_word = random.choice(words)

    context.user_data["spelling_check"] = {
        "word": check_word['word'],
        "translation": check_word['translation'],
        "image": check_word.get('image', '')
    }

    # Отправляем картинку
    if check_word.get('image'):
        await update.message.reply_photo(
            photo=check_word['image'],
            caption="📝 Напиши это слово на корейском:",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"📝 Слово: {check_word['translation']}\n"
            "Напиши его на корейском:",
            parse_mode="HTML"
        )

    # Устанавливаем состояние проверки
    context.user_data["awaiting_spelling"] = True
    context.user_data["mode"] = "spelling_check" 

# Новая функция для обработки ввода при проверке написания


async def get_learned_words(user_id: int, level: int = None):
    async with db.pool.acquire() as conn:
        query = """
            SELECT wt.* 
            FROM words_table wt
            JOIN users u ON wt.id::TEXT = ANY(u.learned_words)
            WHERE u.user_id = $1
        """
        params = [user_id]
        
        if level:
            query += " AND wt.level = $2"
            params.append(level)
            
        return await conn.fetch(query, *params)

async def clear_learned_words(user_id: int):
    async with db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET learned_words = '{}' WHERE user_id = $1",
            user_id
        )


async def handle_learn_from_dictionary(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    # Загружаем слова из базы данных (то, что пользователь уже учил)
    learned_words = await get_learned_words_from_db(user_id)

    # Загружаем текущий список слов из Google Sheets или других источников
    sheet_words = context.user_data.get("current_words", [])

    # Объединяем оба списка, исключая дубликаты
    all_words = {word["word"]: word for word in (sheet_words + learned_words)}.values()
    all_words = list(all_words)  # Преобразуем обратно в список

    if not all_words:
        await update.message.reply_text("❌ Нет слов для игры. Выберите другой уровень.")
        return

    # Перемешиваем слова для случайного порядка
    random.shuffle(all_words)

    # Сохраняем обновленный список слов
    context.user_data.update({
        "game_words": all_words,
        "current_game_index": 0,
        "correct_answers": 0,
        "in_game": True
    })

    # Начало игры
    await update.message.reply_text(
        "🎮 Начинаем игру 'Переводчик'!\n\n"
        "❓ Как играть:\n"
        "1. Я покажу слово на русском\n"
        "2. Ты пишешь перевод на корейском\n"
        "3. Сразу проверяем результат!\n\n"
        "🏆 Постарайся набрать как можно больше правильных ответов подряд!\n"
        "Для выхода нажми 'Стоп 🛑'",
        reply_markup=ReplyKeyboardMarkup([["Стоп 🛑"]], resize_keyboard=True)
    )

    await send_next_game_word(update, context)
    context.user_data["mode"] = "game"



async def send_next_game_word(update: Update, context: CallbackContext):
    words = context.user_data.get("game_words", [])
    index = context.user_data.get("current_game_index", 0)

    if index >= len(words):
        await finish_game(update, context)
        return
    
    word = words[index]
    
    # Сохраняем текущие данные в context
    context.user_data.update({
        "current_word": word,
        "current_correct": word["word"],
        "current_game_index": index + 1
    })
    
    await update.message.reply_text(
        f"Слово: {word['translation']}\n"
        f"📝 Уровень: {word.get('level', 'Неизвестно')}\n\n"
        "✏️ Напиши перевод на корейском:",
        parse_mode="HTML"
    )


async def check_game_translation(update: Update, context: CallbackContext):
    if context.user_data.get("mode") != "game":
        return False  

    user_input = update.message.text.strip() if update.message.text else ""

    # Проверяем, не нажал ли пользователь кнопку "Стоп"
    if user_input == "Стоп 🛑":
        context.user_data["mode"] = None  
        await finish_game(update, context)
        return True

    # Проверяем, есть ли текущее слово
    current_word = context.user_data.get("current_word")
    if not current_word:
        await update.message.reply_text("⚠ Ошибка! Нет текущего слова.")
        return True
    
    correct = current_word["word"]

    # Проверяем, что ввод - не число
    if user_input.isdigit():
        await update.message.reply_text("✏️ Напиши перевод на корейском: ")
        return True

    # Проверка ответа (без нормализации)
    if user_input == correct:
        context.user_data["correct_answers"] += 1
        example_list = current_word.get("examples") or ["(Нет примера)"]
        example = random.choice(example_list)
        msg = (
            f"✅ Правильно! Твой счет: {context.user_data['correct_answers']}\n"
            f"🇰🇷 Ответ: {correct}\n"
            f"💡 Пример: {example}"
        )
    else:
        romanization = current_word.get("romanization", "Нет транскрипции") or "Нет транскрипции"
        msg = (
            f"❌ Ошибка. Правильный ответ: {correct}\n"
            f"📌 Запомни: {correct} ({romanization})"
        )
    
    await update.message.reply_text(msg)
    await asyncio.sleep(1.5)  
    await send_next_game_word(update, context)
    return True


async def finish_game(update: Update, context: CallbackContext):
    correct = context.user_data.get("correct_answers", 0)
    total = len(context.user_data.get("game_words", []))
    
    # Генерация мотивирующего сообщения
    if correct == total:
        emoji = "🏆"
        comment = "Идеальный результат! Ты настоящий полиглот!"
    elif correct >= total * 0.8:
        emoji = "🎉"
        comment = "Отличный результат! Почти идеально!"
    else:
        emoji = "💪"
        comment = "Хорошая попытка! Продолжай практиковаться!"
    
    await update.message.reply_text(
        f"{emoji} Игра завершена!\n\n"
        f"📊 Результат: {correct} из {total}\n"
        f"{comment}\n\n"
        "Выбери следующее действие:",
        reply_markup=ReplyKeyboardMarkup(
            [["Играть еще раз 🔄", "Выйти"]], 
            resize_keyboard=True
        )
    )

    # Сброс состояния игры
    context.user_data.pop("mode", None)
    for key in ["game_words", "current_game_index", "correct_answers", "in_game"]:
        context.user_data.pop(key, None)


async def handle_choice(update: Update, context: CallbackContext):
    user_choice = update.message.text
    user_id = update.message.from_user.id

    if user_choice == "Хангыль":
        keyboard = [
            ["Изучать буквы", "Что за буква?"],
            ["Выйти"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        # Сохраняем прогресс, если он уже есть
        user = await db.get_user(user_id)
        if not user:
            await db.create_user(user_id)
            user = await db.get_user(user_id)

        await update.message.reply_text( 
            """
    🌟 <b>Добро пожаловать в раздел "Хангыль"!</b> 🎓
Здесь ты сможешь изучить корейский алфавит от А до Я (или, точнее, от ㄱ до ㅎ)! 🎉

<b> В этом разделе ты можешь:</b>

1️⃣ <b>Изучать буквы</b> — пройти полный путь от первой буквы до последней. Мы покажем тебе, как пишется и произносится каждая буква, дадим примеры слов и объясним особенности.
2️⃣ <b>Узнать, что за буква</b> — введи любую букву, и мы расскажем о ней всё: как она звучит, как пишется и в каких словах используется.
    """,
            reply_markup=reply_markup, 
             parse_mode="HTML"
        )

    elif user_choice == "Разговорные фразы":
        await update.message.reply_text("Разговорные фразы — это круто! Вот несколько примеров: 👇     Пока тут ничего нет ")
        # добавить примеры разговорных фраз
    elif user_choice == "Грамматика":
        await update.message.reply_text("Грамматика — это фундамент! 📘   Пока тут ничего нет ")
        # добавить примеры грамматических тем
    elif user_choice == "Подготовка к ТОПИКу":
        await update.message.reply_text("Подготовка к ТОПИКу! Удачи в изучении корейского языка! 📚💪  Пока тут ничего нет")
        # добавить материалы для подготовки к ТОПИКу



async def clear_user_state(context: CallbackContext):
    keys_to_remove = [
        "current_words", "current_word_index", "correct_translation",
        "current_options", "awaiting_retry", "awaiting_letter_input",
        "awaiting_dictionary_level"
    ]
    for key in keys_to_remove:
        if key in context.user_data:
            del context.user_data[key]

async def get_letters_data():
    try:
        return await db.pool.fetch("SELECT * FROM korean_alphabet ORDER BY id")
    except Exception as e:
        logging.error(f"Ошибка при получении данных из БД: {e}")
        await asyncio.sleep(1)  # Пауза перед повторной попыткой
        return None



# Добавляем команду для обнуления счёта
async def reset_score(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    # Проверяем, есть ли прогресс у пользователя
    if user_id in user_progress:
        user_progress[user_id]["score"] = 0  # Обнуляем счёт
        await update.message.reply_text("Ваш счёт успешно обнулён! 🎉")
    else:
        await update.message.reply_text("У вас пока нет счёта для обнуления. 😢")

# Основная функция
async def main():
    try:
        # Инициализация базы данных
        await db.connect()

        # Создание приложения
        app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

        # Регистрация обработчиков
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))  # Добавьте эту строку

        # Запуск бота
        await app.initialize()  # Инициализация приложения
        await app.start()       # Запуск приложения
        await app.updater.start_polling()  # Запуск polling

        # Бесконечный цикл для поддержания работы бота
        await asyncio.Event().wait()

    except Exception as e:
        logging.error(f"Произошла ошибка: {e}")
    finally:
        # Корректное завершение работы
        if 'app' in locals():
            await app.updater.stop()  # Остановка polling
            await app.stop()          # Остановка приложения
            await app.shutdown()     # Завершение работы приложения
        await db.close()  # Закрытие соединения с базой данных

# Запуск программы
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
    except Exception as e:
        logging.error(f"Произошла ошибка: {e}")