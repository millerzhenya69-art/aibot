import sqlite3
from datetime import datetime

# Подключаемся к базе данных (создаётся автоматически если нет)
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()


def init_db():
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            joined_at   TEXT,
            role        TEXT DEFAULT 'default user',
            ai_model    TEXT DEFAULT 'none',
            sub_type    TEXT DEFAULT 'none',
            sub_until   TEXT DEFAULT 'none'
        )
    """)
    # Таблица истории сообщений (память для каждого пользователя)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            role        TEXT,
            content     TEXT,
            created_at  TEXT
        )
    """)
    conn.commit()


def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()


# В начале database.py добавь:
OWNER_ID = 0  # будет перезаписан из bot.py

def register_user(user_id, username):
    if not get_user(user_id):
        role = "owner" if user_id == OWNER_ID else "default user"
        cursor.execute(
            "INSERT INTO users (user_id, username, joined_at, role) VALUES (?, ?, ?, ?)",
            (user_id, username, datetime.now().strftime("%d.%m.%Y %H:%M"), role)
        )
        conn.commit()

def set_ai_model(user_id, model):
    cursor.execute("UPDATE users SET ai_model = ? WHERE user_id = ?", (model, user_id))
    conn.commit()


def set_subscription(user_id, sub_type, sub_until):
    cursor.execute(
        "UPDATE users SET sub_type = ?, sub_until = ? WHERE user_id = ?",
        (sub_type, sub_until, user_id)
    )
    conn.commit()


def has_active_sub(user_id):
    user = get_user(user_id)
    if not user:
        return False
    sub_type = user[5]   # sub_type
    sub_until = user[6]  # sub_until
    if sub_type == "forever":
        return True
    if sub_until == "none":
        return False
    # Проверяем не истекла ли подписка
    expiry = datetime.strptime(sub_until, "%d.%m.%Y %H:%M")
    return datetime.now() < expiry


def add_message(user_id, role, content):
    cursor.execute(
        "INSERT INTO history (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, content, datetime.now().strftime("%d.%m.%Y %H:%M"))
    )
    conn.commit()


def get_history(user_id, limit=20):
    cursor.execute(
        "SELECT role, content FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    # Разворачиваем — нужен хронологический порядок
    rows = cursor.fetchall()
    rows.reverse()
    return [{"role": r[0], "content": r[1]} for r in rows]


def clear_history(user_id):
    cursor.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
    conn.commit()