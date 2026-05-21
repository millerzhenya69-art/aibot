import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

OWNER_ID = 0


def init_db():
    # ── Основные таблицы ──────────────────────────────────────────────────
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

    # ── Новые таблицы ─────────────────────────────────────────────────────

    # Чаты Mini App (хранение между сессиями)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mini_app_chats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            chat_id     TEXT,
            title       TEXT DEFAULT 'New chat',
            model       TEXT DEFAULT 'gpt',
            messages    TEXT DEFAULT '[]',
            created_at  TEXT,
            updated_at  TEXT
        )
    """)

    # Сессии использования (бот + мини апп)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            source      TEXT,
            started_at  TEXT,
            last_active TEXT,
            messages_count INTEGER DEFAULT 0
        )
    """)

    # История покупок (расширенная)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchase_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            username    TEXT,
            item_type   TEXT,
            item_label  TEXT,
            method      TEXT,
            amount      TEXT,
            status      TEXT DEFAULT 'completed',
            created_at  TEXT
        )
    """)

    # Выданные роли и подписки от owner
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_grants (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            target_user_id INTEGER,
            grant_type  TEXT,
            grant_value TEXT,
            expires_at  TEXT DEFAULT 'none',
            granted_by  INTEGER,
            granted_at  TEXT
        )
    """)

    # Дефолтные настройки
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', '0')")

    # Миграция старых БД — добавляем отсутствующие колонки
    for col, defval in [("balance", "0"), ("referred_by", "NULL")]:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT {defval}")
        except:
            pass

    conn.commit()


# ── Настройки ─────────────────────────────────────────────────────────────

def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else None


def set_setting(key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()


def is_maintenance():
    return get_setting("maintenance") == "1"


# ── Пользователи ──────────────────────────────────────────────────────────

def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()


def get_all_users():
    cursor.execute("""
        SELECT user_id, username, joined_at, role, ai_model, sub_type, sub_until, balance
        FROM users ORDER BY joined_at DESC
    """)
    return cursor.fetchall()


def get_user_by_username(username):
    """Поиск пользователя по @username (без @)."""
    clean = username.lstrip("@").lower()
    cursor.execute("SELECT * FROM users WHERE LOWER(username) = ?", (clean,))
    return cursor.fetchone()


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


def remove_subscription(user_id):
    """Снять подписку с пользователя."""
    cursor.execute(
        "UPDATE users SET sub_type = 'none', sub_until = 'none', ai_model = 'gpt' WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()


def set_role(user_id, role):
    """Установить роль пользователю."""
    cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
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
    try:
        expiry = datetime.strptime(sub_until, "%d.%m.%Y %H:%M")
        return datetime.now() < expiry
    except:
        return False


# ── Баланс ────────────────────────────────────────────────────────────────

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


# ── Рефералы ──────────────────────────────────────────────────────────────

def get_referral_count(user_id):
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    return cursor.fetchone()[0]


# ── История сообщений (бот) ───────────────────────────────────────────────

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


# ── Платежи ───────────────────────────────────────────────────────────────

def log_payment(user_id, username, plan, method, amount):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    cursor.execute(
        "INSERT INTO payments (user_id, username, plan, method, amount, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, plan, method, amount, now)
    )
    cursor.execute(
        "INSERT INTO purchase_history (user_id, username, item_type, item_label, method, amount, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'completed', ?)",
        (user_id, username, "subscription", plan, method, amount, now)
    )
    conn.commit()


def get_payments(limit=20):
    cursor.execute(
        "SELECT username, plan, method, amount, created_at FROM payments ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    return cursor.fetchall()


def get_user_purchase_history(user_id):
    cursor.execute(
        "SELECT item_type, item_label, method, amount, status, created_at FROM purchase_history WHERE user_id = ? ORDER BY id DESC LIMIT 50",
        (user_id,)
    )
    return cursor.fetchall()


# ── Статистика ────────────────────────────────────────────────────────────

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

    # Активные подписчики (с не истёкшей подпиской)
    cursor.execute(
        "SELECT COUNT(*) FROM users WHERE sub_type = 'forever' OR (sub_until != 'none' AND sub_until > ?)",
        (datetime.now().strftime("%d.%m.%Y %H:%M"),)
    )
    active_subs = cursor.fetchone()[0]

    # Сегодняшние регистрации
    today = datetime.now().strftime("%d.%m.%Y")
    cursor.execute("SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (f"{today}%",))
    new_today = cursor.fetchone()[0]

    # Сессии Mini App
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM mini_app_chats")
    miniapp_users = cursor.fetchone()[0]

    # Сообщений за последние 7 дней
    cursor.execute("SELECT COUNT(*) FROM history WHERE created_at >= ?",
                   ((datetime.now().replace(hour=0, minute=0) ).strftime("%d.%m.%Y %H:%M"),))

    # Роли
    cursor.execute("SELECT role, COUNT(*) FROM users GROUP BY role")
    roles_raw = cursor.fetchall()
    roles = {r[0]: r[1] for r in roles_raw}

    return {
        "total_users":    total,
        "subscribers":    subs,
        "active_subs":    active_subs,
        "free_users":     free_users,
        "pro_users":      pro_users,
        "total_payments": total_payments,
        "total_messages": total_messages,
        "new_today":      new_today,
        "miniapp_users":  miniapp_users,
        "roles":          roles,
    }


# ── Mini App — чаты ───────────────────────────────────────────────────────

def get_mini_app_chats(user_id):
    """Получить все чаты пользователя из Mini App."""
    cursor.execute(
        "SELECT chat_id, title, model, messages, created_at, updated_at FROM mini_app_chats WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    result = []
    for r in rows:
        try:
            messages = json.loads(r[3])
        except:
            messages = []
        result.append({
            "id":         r[0],
            "title":      r[1],
            "model":      r[2],
            "messages":   messages,
            "createdAt":  r[4],
            "updatedAt":  r[5],
        })
    return result


def save_mini_app_chat(user_id, chat_id, title, model, messages):
    """Сохранить / обновить чат Mini App."""
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    messages_json = json.dumps(messages, ensure_ascii=False)

    cursor.execute(
        "SELECT id FROM mini_app_chats WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id)
    )
    exists = cursor.fetchone()

    if exists:
        cursor.execute(
            "UPDATE mini_app_chats SET title = ?, model = ?, messages = ?, updated_at = ? WHERE user_id = ? AND chat_id = ?",
            (title, model, messages_json, now, user_id, chat_id)
        )
    else:
        cursor.execute(
            "INSERT INTO mini_app_chats (user_id, chat_id, title, model, messages, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, title, model, messages_json, now, now)
        )
    conn.commit()


def delete_mini_app_chat(user_id, chat_id):
    """Удалить чат Mini App."""
    cursor.execute(
        "DELETE FROM mini_app_chats WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id)
    )
    conn.commit()


# ── Сессии ────────────────────────────────────────────────────────────────

def log_session_activity(user_id, source="bot"):
    """Обновить или создать сессию пользователя."""
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    cursor.execute(
        "SELECT id FROM sessions WHERE user_id = ? AND source = ? ORDER BY id DESC LIMIT 1",
        (user_id, source)
    )
    row = cursor.fetchone()
    if row:
        cursor.execute(
            "UPDATE sessions SET last_active = ?, messages_count = messages_count + 1 WHERE id = ?",
            (now, row[0])
        )
    else:
        cursor.execute(
            "INSERT INTO sessions (user_id, source, started_at, last_active, messages_count) VALUES (?, ?, ?, ?, 1)",
            (user_id, source, now, now)
        )
    conn.commit()


# ── Admin grants ──────────────────────────────────────────────────────────

def log_admin_grant(target_user_id, grant_type, grant_value, expires_at, granted_by):
    cursor.execute(
        "INSERT INTO admin_grants (target_user_id, grant_type, grant_value, expires_at, granted_by, granted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (target_user_id, grant_type, grant_value, expires_at,
         granted_by, datetime.now().strftime("%d.%m.%Y %H:%M"))
    )
    conn.commit()
