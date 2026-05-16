import sqlite3
from datetime import datetime

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

OWNER_ID = 0  # перезаписывается из bot.py


def init_db():
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            joined_at   TEXT,
            role        TEXT DEFAULT 'default user',
            ai_model    TEXT DEFAULT 'none',
            sub_type    TEXT DEFAULT 'none',
            sub_until   TEXT DEFAULT 'none',
            balance     INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            role        TEXT,
            content     TEXT,
            created_at  TEXT
        )
    """)
    # Добавляем новые колонки если БД старая
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER DEFAULT NULL")
    except:
        pass
    conn.commit()


def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()


def register_user(user_id, username, referred_by=None):
    if not get_user(user_id):
        role = "owner" if user_id == OWNER_ID else "default user"
        cursor.execute(
            "INSERT INTO users (user_id, username, joined_at, role, referred_by) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, datetime.now().strftime("%d.%m.%Y %H:%M"), role, referred_by)
        )
        conn.commit()
        # Начисляем реферальное вознаграждение
        if referred_by and referred_by != user_id:
            add_balance(referred_by, 10)
        return True
    return False


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
    sub_type  = user[5]
    sub_until = user[6]
    if sub_type == "forever":
        return True
    if sub_until == "none":
        return False
    expiry = datetime.strptime(sub_until, "%d.%m.%Y %H:%M")
    return datetime.now() < expiry


def get_balance(user_id):
    user = get_user(user_id)
    if not user:
        return 0
    return user[7] if len(user) > 7 else 0


def add_balance(user_id, amount):
    cursor.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id = ?",
        (amount, user_id)
    )
    conn.commit()


def spend_balance(user_id, amount):
    """Списать виртуальные рубли. Возвращает True если успешно."""
    balance = get_balance(user_id)
    if balance < amount:
        return False
    cursor.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id = ?",
        (amount, user_id)
    )
    conn.commit()
    return True


def get_referral_count(user_id):
    cursor.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,)
    )
    return cursor.fetchone()[0]


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
    rows = cursor.fetchall()
    rows.reverse()
    return [{"role": r[0], "content": r[1]} for r in rows]


def clear_history(user_id):
    cursor.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
    conn.commit()
