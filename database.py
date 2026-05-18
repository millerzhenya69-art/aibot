import sqlite3
from datetime import datetime

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

OWNER_ID = 0


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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            username    TEXT,
            plan        TEXT,
            method      TEXT,
            amount      TEXT,
            created_at  TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT
        )
    """)
    # Дефолтные настройки
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', '0')")
    # Новые колонки для старых БД
    for col, defval in [("balance", "0"), ("referred_by", "NULL")]:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT {defval}")
        except:
            pass
    conn.commit()


def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else None


def set_setting(key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()


def is_maintenance():
    return get_setting("maintenance") == "1"


def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()


def get_all_users():
    cursor.execute("SELECT user_id, username, joined_at, role, ai_model, sub_type, sub_until, balance FROM users ORDER BY joined_at DESC")
    return cursor.fetchall()


def register_user(user_id, username, referred_by=None):
    if not get_user(user_id):
        role = "owner" if user_id == OWNER_ID else "default user"
        cursor.execute(
            "INSERT INTO users (user_id, username, joined_at, role, referred_by) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, datetime.now().strftime("%d.%m.%Y %H:%M"), role, referred_by)
        )
        conn.commit()
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
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()


def spend_balance(user_id, amount):
    balance = get_balance(user_id)
    if balance < amount:
        return False
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    return True


def get_referral_count(user_id):
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    return cursor.fetchone()[0]


def log_payment(user_id, username, plan, method, amount):
    cursor.execute(
        "INSERT INTO payments (user_id, username, plan, method, amount, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, plan, method, amount, datetime.now().strftime("%d.%m.%Y %H:%M"))
    )
    conn.commit()


def get_payments(limit=20):
    cursor.execute("SELECT username, plan, method, amount, created_at FROM payments ORDER BY id DESC LIMIT ?", (limit,))
    return cursor.fetchall()


def get_stats():
    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE sub_type != 'none'")
    subs = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE ai_model = 'gpt'")
    free_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE ai_model = 'gemini'")
    pro_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM payments")
    total_payments = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM history")
    total_messages = cursor.fetchone()[0]
    return {
        "total_users": total,
        "subscribers": subs,
        "free_users": free_users,
        "pro_users": pro_users,
        "total_payments": total_payments,
        "total_messages": total_messages,
    }


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
