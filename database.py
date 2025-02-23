from config import POSTGRES_CONFIG
import psycopg2

class Database:
    def __init__(self):
        self.conn = psycopg2.connect(**POSTGRES_CONFIG)
    
    def add_subscriber(self, user_id: int):
        """Добавляет подписчика в базу"""
        with self.conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO subscribers (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
                (user_id,)
            )
            self.conn.commit()
    
    def get_subscribers(self):
        """Возвращает список всех подписчиков"""
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT user_id FROM subscribers")
            return [row[0] for row in cursor.fetchall()]
    
    def delete_subscriber(self, user_id: int):
        """Удаляет подписчика"""
        with self.conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM subscribers WHERE user_id = %s",
                (user_id,)
            )
            self.conn.commit()
    
    def close(self):
        """Закрывает соединение"""
        self.conn.close()