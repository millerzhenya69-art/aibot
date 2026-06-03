import sys
import io
import os
import tempfile
import telebot
from telebot import types
from datetime import datetime, timedelta
import logging
import requests
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS

from database import (init_db, get_user, get_user_by_username, register_user,
                      set_ai_model, set_subscription, remove_subscription,
                      set_role, has_active_sub, add_message, get_history,
                      clear_history, get_balance, spend_balance, add_balance,
                      get_referral_count, get_all_users, get_stats,
                      get_payments, log_payment, is_maintenance,
                      set_setting, get_setting, log_admin_grant,
                      get_mini_app_chats, save_mini_app_chat,
                      delete_mini_app_chat, log_session_activity,
                      get_user_purchase_history,
                      check_daily_limit, increment_daily_count,
                      DAILY_LIMIT_FREE, DAILY_LIMIT_PRO)
from ai_clients import (ask_gpt, ask_gemini, ask_nova, ask_pro,
                        ask_absolution, ask_with_file, check_keys_status)

logging.basicConfig(level=logging.CRITICAL)

TOKEN        = os.environ.get("BOT_TOKEN", "")
OWNER_ID     = int(os.environ.get("OWNER_ID", "7113603197"))
CRYPTO_TOKEN = os.environ.get("CRYPTO_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Elyon_by_unkony_bot")


import database
database.OWNER_ID = OWNER_ID

bot = telebot.TeleBot(TOKEN, threaded=False)
init_db()

user_states = {}

# ── Тест-режим для owner ──────────────────────────────────────────────────
# Когда включён — owner проходит все проверки как обычный пользователь
owner_test_mode = False

def is_privileged(user_id):
    """Owner без тест-режима — обходит все проверки."""
    return user_id == OWNER_ID and not owner_test_mode

# ── Обязательные каналы для подписки ─────────────────────────────────────

REQUIRED_CHANNELS = [
    {"id": "@unkonyy",   "title": "Owner channel",    "url": "https://t.me/unkonyy"},
    {"id": "@AI_Elyon",  "title": "Elyon AI channel", "url": "https://t.me/AI_Elyon"},
]

def check_subscriptions(user_id):
    """Проверяет подписку на все обязательные каналы. Возвращает список каналов где НЕ подписан."""
    not_subscribed = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = bot.get_chat_member(ch["id"], user_id)
            if member.status in ("left", "kicked", "banned"):
                not_subscribed.append(ch)
        except Exception as e:
            # Если бот не является членом канала — пропускаем проверку
            print(f"Sub check error for {ch['id']}: {e}")
    return not_subscribed

def send_subscribe_prompt(chat_id):
    """Отправляет сообщение с кнопками подписки."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    for ch in REQUIRED_CHANNELS:
        markup.add(types.InlineKeyboardButton(
            f"📢 {ch['title']}", url=ch["url"]
        ))
    markup.add(types.InlineKeyboardButton(
        "✅ Я подписался — проверить", callback_data="check_subs"
    ))
    bot.send_message(
        chat_id,
        "👋 Добро пожаловать в Elyon AI!\n\n"
        "Для использования бота необходимо подписаться на наши каналы:\n\n"
        "📢 Owner channel — @unkonyy\n"
        "📢 Elyon AI channel — @AI_Elyon\n\n"
        "После подписки нажми кнопку ниже 👇",
        reply_markup=markup
    )

def is_subscribed(user_id):
    """True если пользователь подписан на все каналы."""
    return len(check_subscriptions(user_id)) == 0

# ── Цены ──────────────────────────────────────────────────────────────────

PRICES = {
    "nova":       {"stars": 50,  "label": "Elyon Nova — 50 ⭐",       "days": 30, "rub": 91},
    "pro":        {"stars": 100, "label": "Elyon PRO — 100 ⭐",        "days": 30, "rub": 182},
    "absolution": {"stars": 150, "label": "Elyon Absolution — 150 ⭐", "days": 30, "rub": 265},
    # Обратная совместимость
    "month":    {"stars": 50,  "label": "Elyon Nova — 50 ⭐",       "days": 30,  "rub": 91},
    "halfyear": {"stars": 100, "label": "Elyon PRO — 100 ⭐",        "days": 180, "rub": 182},
    "forever":  {"stars": 150, "label": "Elyon Absolution — 150 ⭐", "days": 0,   "rub": 265},
}
CRYPTO_PRICES = {
    "nova":       {"amount": "1.00", "label": "Elyon Nova — 91 ₽"},
    "pro":        {"amount": "2.00", "label": "Elyon PRO — 182 ₽"},
    "absolution": {"amount": "2.90", "label": "Elyon Absolution — 265 ₽"},
    "month":    {"amount": "1.00", "label": "Elyon Nova — 91 ₽"},
    "halfyear": {"amount": "2.00", "label": "Elyon PRO — 182 ₽"},
    "forever":  {"amount": "2.90", "label": "Elyon Absolution — 265 ₽"},
}
VIRTUAL_PRICES = {
    "nova":       {"rub": 91,  "label": "Elyon Nova — 91 монета"},
    "pro":        {"rub": 182, "label": "Elyon PRO — 182 монеты"},
    "absolution": {"rub": 265, "label": "Elyon Absolution — 265 монет"},
    "month":    {"rub": 91,  "label": "Elyon Nova — 91 монета"},
    "halfyear": {"rub": 182, "label": "Elyon PRO — 182 монеты"},
    "forever":  {"rub": 265, "label": "Elyon Absolution — 265 монет"},
}
STARS_PER_RUB = 1 / 1.82
RUB_PER_DAY   = 50 / 30

BETA_TESTER_STARS = 50

# ── Поддерживаемые типы файлов ────────────────────────────────────────────

GEMINI_SUPPORTED = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "video/mp4", "video/mpeg", "video/mov", "video/avi", "video/webm",
    "audio/mp3", "audio/mpeg", "audio/wav", "audio/ogg", "audio/flac",
    "application/pdf",
    "text/plain", "text/html", "text/css", "text/javascript",
    "text/x-python", "text/x-c", "text/x-c++", "text/x-java",
    "application/json", "text/csv", "text/xml",
}

TEXT_EXTENSIONS = {
    ".py": "text/x-python", ".js": "text/javascript", ".ts": "text/javascript",
    ".cpp": "text/x-c++", ".c": "text/x-c", ".h": "text/x-c",
    ".java": "text/x-java", ".cs": "text/plain", ".go": "text/plain",
    ".rs": "text/plain", ".php": "text/plain", ".rb": "text/plain",
    ".swift": "text/plain", ".kt": "text/plain", ".sh": "text/plain",
    ".txt": "text/plain", ".md": "text/plain", ".csv": "text/csv",
    ".json": "application/json", ".xml": "text/xml", ".html": "text/html",
    ".css": "text/css", ".sql": "text/plain", ".yaml": "text/plain",
    ".yml": "text/plain", ".toml": "text/plain", ".env": "text/plain",
}

def get_mime_for_extension(filename):
    ext = os.path.splitext(filename.lower())[1]
    return TEXT_EXTENSIONS.get(ext, None)


# ── Клавиатуры ────────────────────────────────────────────────────────────

def main_menu_keyboard(user_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("💬 Chat with AI"),
        types.KeyboardButton("👤 Personal account"),
        types.KeyboardButton("🆓 Elyon Core"),
        types.KeyboardButton("⭐ Elyon Nova"),
    )
    if user_id and user_id == OWNER_ID:
        markup.add(types.KeyboardButton("🛠 Control Panel"))
    return markup


def show_start(chat_id, user_id=None):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🆓 Elyon Core", callback_data="choose_free"),
        types.InlineKeyboardButton("⭐ Elyon Nova",  callback_data="choose_pro")
    )
    bot.send_message(
        chat_id,
        "👋 Добро пожаловать в Elyon AI!\n\n"
        "🆓 Elyon Core — бесплатно, быстрые ответы\n"
        "⭐ Elyon Nova — про, глубокое мышление\n\n"
        "Выбери версию:",
        reply_markup=markup
    )


def show_payment_options(chat_id, user_id):
    balance = get_balance(user_id)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⭐ 30 дней — 30 звёзд",    callback_data="pay_stars_month"),
        types.InlineKeyboardButton("💳 30 дней — 50 ₽",         callback_data="pay_crypto_month"),
        types.InlineKeyboardButton("⭐ 6 месяцев — 60 звёзд",  callback_data="pay_stars_halfyear"),
        types.InlineKeyboardButton("💳 6 месяцев — 182 ₽",      callback_data="pay_crypto_halfyear"),
        types.InlineKeyboardButton("⭐ Навсегда — 120 звёзд",  callback_data="pay_stars_forever"),
        types.InlineKeyboardButton("💳 Навсегда — 429 ₽",       callback_data="pay_crypto_forever"),
        types.InlineKeyboardButton("📅 Произвольный срок",      callback_data="pay_custom"),
        types.InlineKeyboardButton("🎁 Подарком [TEST]",        callback_data="pay_gift_menu"),
    )
    if balance >= 50:
        markup.add(types.InlineKeyboardButton(
            f"🪙 Монеты (баланс: {balance})", callback_data="pay_virtual_menu"
        ))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_start"))
    bot.send_message(
        chat_id,
        "⭐ Elyon Nova — Подписка\n\n"
        "⭐ Telegram Stars\n"
        "💳 CryptoBot (₽/USDT)\n"
        "🎁 Подарком (тест)\n"
        "📅 Произвольное количество дней\n"
        + (f"🪙 Монеты (баланс: {balance})\n" if balance >= 50 else "") +
        "\nВыбери тариф и способ оплаты:",
        reply_markup=markup
    )


def show_virtual_payment(chat_id, user_id):
    balance = get_balance(user_id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for plan, data in VIRTUAL_PRICES.items():
        if balance >= data["rub"]:
            markup.add(types.InlineKeyboardButton(
                f"🪙 {data['label']}", callback_data=f"pay_virtual_{plan}"
            ))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="choose_pro"))
    bot.send_message(
        chat_id,
        f"🪙 Оплата виртуальными монетами\n\nТвой баланс: {balance} монет\n\nВыбери тариф:",
        reply_markup=markup
    )


def calc_custom_price(days):
    rub   = round(RUB_PER_DAY * days, 2)
    stars = max(1, round(rub * STARS_PER_RUB))
    usdt  = round(rub * 0.011, 2)
    return rub, stars, usdt


def activate_subscription(user_id, plan, chat_id, days=None, label=None):
    if plan == "forever":
        set_subscription(user_id, "forever", "none")
        price_label = label or PRICES["forever"]["label"]
    elif plan == "custom" and days:
        until = datetime.now() + timedelta(days=days)
        set_subscription(user_id, "custom", until.strftime("%d.%m.%Y %H:%M"))
        price_label = label or f"{days} дней"
    else:
        price_label = label or PRICES[plan]["label"]
        until = datetime.now() + timedelta(days=PRICES[plan]["days"])
        set_subscription(user_id, plan, until.strftime("%d.%m.%Y %H:%M"))
    set_ai_model(user_id, "gemini")
    clear_history(user_id)
    bot.send_message(
        chat_id,
        f"✅ Подписка активирована!\nТариф: {price_label}\n\nТеперь ты используешь Elyon Nova 🌟",
        reply_markup=main_menu_keyboard(user_id)
    )
    try:
        uname = bot.get_chat(user_id).username or "без username"
        bot.send_message(OWNER_ID, f"💰 Новая подписка!\nПользователь: @{uname}\nID: {user_id}\nТариф: {price_label}")
    except:
        pass


def create_crypto_invoice(amount, user_id, plan, label=None):
    try:
        response = requests.post(
            "https://pay.crypt.bot/api/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN},
            json={
                "asset": "USDT",
                "amount": str(amount),
                "description": f"Elyon Nova — {label or plan}",
                "payload": f"{user_id}_{plan}",
                "expires_in": 3600
            }
        )
        data = response.json()
        if data["ok"]:
            return data["result"]
    except Exception as e:
        print("CryptoBot error:", e)
    return None


# ── Обработка файлов от пользователя ─────────────────────────────────────

def download_telegram_file(file_id):
    file_info = bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
    response = requests.get(file_url, timeout=30)
    return response.content


def handle_file_message(message, user_id, ai_model):
    user = get_user(user_id)
    if not user:
        return

    if is_maintenance() and user_id != OWNER_ID:
        bot.send_message(message.chat.id, "🔧 Технические работы")
        return

    if ai_model == "gemini" and not has_active_sub(user_id):
        bot.send_message(message.chat.id, "⚠️ Подписка истекла.")
        show_payment_options(message.chat.id, user_id)
        return

    file_id   = None
    file_name = "file"
    mime_type = None
    caption   = message.caption or "Проанализируй этот файл и опиши его содержимое."

    if message.content_type == "photo":
        file_id = message.photo[-1].file_id
        mime_type = "image/jpeg"
        file_name = "image.jpg"
    elif message.content_type == "document":
        doc = message.document
        file_id = doc.file_id
        file_name = doc.file_name or "document"
        mime_type = doc.mime_type
        if not mime_type or mime_type == "application/octet-stream":
            mime_type = get_mime_for_extension(file_name)
    elif message.content_type == "video":
        file_id = message.video.file_id
        mime_type = "video/mp4"
        file_name = "video.mp4"
    elif message.content_type == "audio":
        file_id = message.audio.file_id
        mime_type = "audio/mpeg"
        file_name = "audio.mp3"
    elif message.content_type == "voice":
        file_id = message.voice.file_id
        mime_type = "audio/ogg"
        file_name = "voice.ogg"

    if not file_id:
        bot.send_message(message.chat.id, "❌ Неподдерживаемый тип файла.")
        return

    if mime_type not in GEMINI_SUPPORTED:
        ext = os.path.splitext(file_name.lower())[1]
        if ext in [".zip", ".rar", ".7z", ".tar"]:
            bot.send_message(message.chat.id,
                "Архивы (ZIP, RAR) нельзя анализировать напрямую. "
                "Пожалуйста, распакуй файлы и отправь их по отдельности.")
            return
        if ext in [".docx", ".xlsx", ".xls", ".doc"]:
            bot.send_message(message.chat.id,
                "Файлы Word/Excel имеют ограниченную поддержку. "
                "Для лучших результатов сохрани как PDF или TXT.")
            mime_type = "application/octet-stream"

    bot.send_chat_action(message.chat.id, "typing")
    log_session_activity(user_id, "bot")

    try:
        file_bytes = download_telegram_file(file_id)
        history    = get_history(user_id)
        add_message(user_id, "user", f"[Файл: {file_name}] {caption}")

        if ai_model == "gpt":
            reply = ask_with_file(file_bytes, mime_type, file_name, caption, history, use_pro=False)
        else:
            reply = ask_with_file(file_bytes, mime_type, file_name, caption, history, use_pro=True)

        add_message(user_id, "assistant", reply)
        bot.send_message(message.chat.id, reply)

    except Exception as e:
        error_text = str(e)
        print("File AI error:", e)
        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            bot.send_message(message.chat.id, "Слишком много запросов. Попробуй через минуту.")
        else:
            bot.send_message(message.chat.id, f"Ошибка анализа файла: {error_text[:150]}")


# ── /start ────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    args    = message.text.split()
    param   = args[1] if len(args) > 1 else None

    # Deep link с оплатой с сайта: /start pay_nova, pay_pro, pay_abs
    if param and param.startswith("pay_"):
        tier_map = {"pay_nova": "nova", "pay_pro": "pro", "pay_abs": "absolution"}
        tier = tier_map.get(param)
        register_user(user_id, message.from_user.username or "")
        if tier:
            _show_single_tier_payment(message.chat.id, user_id, tier)
        else:
            _show_all_tiers_payment(message.chat.id, user_id)
        return

    # Deep link авторизации с сайта
    if param == "auth":
        register_user(user_id, message.from_user.username or "")
        bot.send_message(
            message.chat.id,
            "Напиши /auth чтобы получить ссылку для входа на сайт Elyon AI."
        )
        return

    # Обычный /start с рефералом
    referred_by = None
    if param:
        try:
            referred_by = int(param)
        except:
            pass

    is_new = register_user(user_id, message.from_user.username or "", referred_by)
    if is_new and referred_by:
        try:
            bot.send_message(referred_by, "Кто-то перешёл по твоей реферальной ссылке! +10 монет зачислено.")
        except:
            pass
    bot.send_message(message.chat.id, "Загрузка...", reply_markup=main_menu_keyboard(user_id))
    show_start(message.chat.id, user_id)


# ── /auth — авторизация на сайте ──────────────────────────────────────────

import secrets as _secrets

# Хранилище токенов {token: {user_id, username, first_name, expires}}
_auth_tokens = {}

@bot.message_handler(commands=["auth"])
def cmd_auth(message):
    user_id    = message.from_user.id
    username   = message.from_user.username or ""
    first_name = message.from_user.first_name or ""

    token   = _secrets.token_urlsafe(24)
    expires = datetime.now().timestamp() + 300  # 5 минут

    _auth_tokens[token] = {
        "user_id":    user_id,
        "username":   username,
        "first_name": first_name,
        "expires":    expires,
    }

    user     = get_user(user_id)
    sub_type = user[5] if user else "none"
    sub_label = sub_type if sub_type != "none" else "нет подписки"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "✅ Войти на сайт Elyon AI",
        url=f"https://elyon-ai-web.vercel.app/auth.html?tg_token={token}"
    ))
    bot.send_message(
        message.chat.id,
        f"🔐 Авторизация на сайте\n\n"
        f"Нажми кнопку — она действует 5 минут.\n\n"
        f"Подписка: {sub_label}",
        reply_markup=markup
    )


# ── /give — выдача подписки / роли ────────────────────────────────────────

@bot.message_handler(commands=["give"])
def cmd_give(message):
    if message.from_user.id != OWNER_ID:
        return

    # Форматы:
    #   /give @username nova 30d
    #   /give @username nova forever
    #   /give @username role sponsor
    #   /give @username role sponsor 30d
    #   /give @username remove sub
    #   /give @username remove role

    parts = message.text.strip().split()
    # parts[0] = /give, parts[1] = @username, parts[2] = тип, ...

    if len(parts) < 3:
        bot.send_message(message.chat.id,
            "Использование:\n"
            "/give @username nova 30d\n"
            "/give @username nova forever\n"
            "/give @username role sponsor\n"
            "/give @username role sponsor 30d\n"
            "/give @username remove sub\n"
            "/give @username remove role"
        )
        return

    raw_username = parts[1].lstrip("@")
    target = get_user_by_username(raw_username)

    if not target:
        bot.send_message(message.chat.id,
            f"Пользователь @{raw_username} не найден в базе.\n"
            "Он должен хотя бы раз написать боту.")
        return

    target_id = target[0]
    action    = parts[2].lower()

    # ── Выдача подписки ──
    if action == "nova":
        duration = parts[3].lower() if len(parts) > 3 else "30d"

        if duration == "forever":
            set_subscription(target_id, "forever", "none")
            set_ai_model(target_id, "gemini")
            label = "навсегда"
            expires_str = "none"
        else:
            try:
                days = int(duration.replace("d", "").replace("д", ""))
            except:
                bot.send_message(message.chat.id, "Неверный формат срока. Пример: 30d или forever")
                return
            until = datetime.now() + timedelta(days=days)
            until_str = until.strftime("%d.%m.%Y %H:%M")
            set_subscription(target_id, "custom", until_str)
            set_ai_model(target_id, "gemini")
            label = f"{days} дней (до {until_str})"
            expires_str = until_str

        log_admin_grant(target_id, "subscription", "nova", expires_str, OWNER_ID)
        bot.send_message(message.chat.id,
            f"Подписка Elyon Nova выдана @{raw_username} на {label}.")
        try:
            bot.send_message(target_id,
                f"Тебе выдана подписка Elyon Nova на {label}!\n"
                "Теперь ты можешь использовать расширенную модель.")
        except:
            pass
        return

    # ── Выдача роли ──
    if action == "role":
        if len(parts) < 4:
            bot.send_message(message.chat.id, "Укажи роль. Пример: /give @user role sponsor")
            return
        role_name    = parts[3]
        duration_str = parts[4].lower() if len(parts) > 4 else None
        expires_str  = "none"

        if duration_str and duration_str != "forever":
            try:
                days = int(duration_str.replace("d", "").replace("д", ""))
                until = datetime.now() + timedelta(days=days)
                expires_str = until.strftime("%d.%m.%Y %H:%M")
            except:
                pass

        set_role(target_id, role_name)
        log_admin_grant(target_id, "role", role_name, expires_str, OWNER_ID)
        exp_label = f"до {expires_str}" if expires_str != "none" else "бессрочно"
        bot.send_message(message.chat.id,
            f"Роль '{role_name}' выдана @{raw_username} ({exp_label}).")
        try:
            bot.send_message(target_id,
                f"Тебе присвоена роль: {role_name}!")
        except:
            pass
        return

    # ── Снять подписку / роль ──
    if action == "remove":
        what = parts[3].lower() if len(parts) > 3 else ""
        if what == "sub":
            remove_subscription(target_id)
            bot.send_message(message.chat.id, f"Подписка @{raw_username} удалена.")
            try:
                bot.send_message(target_id, "Твоя подписка Elyon Nova была деактивирована.")
            except:
                pass
        elif what == "role":
            set_role(target_id, "default user")
            bot.send_message(message.chat.id, f"Роль @{raw_username} сброшена до 'default user'.")
            try:
                bot.send_message(target_id, "Твоя роль была сброшена.")
            except:
                pass
        else:
            bot.send_message(message.chat.id, "Укажи что снять: sub или role")
        return

    bot.send_message(message.chat.id,
        "Неизвестное действие. Используй: nova / role / remove")


# ── /pay — быстрая оплата подписки (вызывается с сайта) ───────────────────

@bot.message_handler(commands=["pay"])
def cmd_pay_subscription(message):
    """
    /pay        — показывает все тарифы
    /pay nova   — сразу показывает оплату Nova
    /pay pro    — сразу показывает оплату PRO
    /pay abs    — сразу показывает оплату Absolution
    """
    parts = message.text.strip().split()
    tier  = parts[1].lower() if len(parts) > 1 else None

    # Если команда от owner для выдачи монет — обрабатываем отдельно
    if message.from_user.id == OWNER_ID and tier and tier.startswith("@"):
        # Это старая команда /pay @username сумма — пропускаем в cmd_pay_coins
        return

    tier_map = {
        "nova": "nova", "n": "nova",
        "pro": "pro", "p": "pro",
        "abs": "absolution", "absolution": "absolution", "a": "absolution",
    }
    target_tier = tier_map.get(tier) if tier else None

    if target_tier:
        _show_single_tier_payment(message.chat.id, message.from_user.id, target_tier)
    else:
        _show_all_tiers_payment(message.chat.id, message.from_user.id)


def _show_single_tier_payment(chat_id, user_id, tier):
    """Показывает оплату для конкретного тарифа."""
    tier_info = {
        "nova":       {"label": "Elyon Nova",       "stars": 50,  "rub": 91,  "desc": "25 сообщений/день"},
        "pro":        {"label": "Elyon PRO",         "stars": 100, "rub": 182, "desc": "40 сообщений/день"},
        "absolution": {"label": "Elyon Absolution",  "stars": 150, "rub": 265, "desc": "50 сообщений/день"},
    }
    info = tier_info.get(tier)
    if not info:
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            f"⭐ Оплатить {info['stars']} звёздами",
            callback_data=f"pay_stars_{tier}"
        ),
        types.InlineKeyboardButton(
            f"💳 Оплатить {info['rub']} ₽ через CryptoBot",
            callback_data=f"pay_crypto_{tier}"
        ),
    )
    bot.send_message(
        chat_id,
        f"⭐ {info['label']}\n\n"
        f"{info['desc']} · 30 дней\n\n"
        f"Выбери способ оплаты:",
        reply_markup=markup
    )


def _show_all_tiers_payment(chat_id, user_id):
    """Показывает все тарифы."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⭐ Nova — 50 звёзд",        callback_data="pay_stars_nova"),
        types.InlineKeyboardButton("💳 Nova — 91 ₽",             callback_data="pay_crypto_nova"),
        types.InlineKeyboardButton("⭐ PRO — 100 звёзд",         callback_data="pay_stars_pro"),
        types.InlineKeyboardButton("💳 PRO — 182 ₽",             callback_data="pay_crypto_pro"),
        types.InlineKeyboardButton("⭐ Absolution — 150 звёзд",  callback_data="pay_stars_absolution"),
        types.InlineKeyboardButton("💳 Absolution — 265 ₽",      callback_data="pay_crypto_absolution"),
    )
    bot.send_message(
        chat_id,
        "⭐ Elyon AI — Подписка\n\n"
        "Nova — 25 сообщений/день\n"
        "PRO — 40 сообщений/день\n"
        "Absolution — 50 сообщений/день\n\n"
        "Выбери тариф и способ оплаты:",
        reply_markup=markup
    )


# ── /pay @username сумма — выдача монет (owner) ───────────────────────────



@bot.message_handler(commands=["pay"])
def cmd_pay(message):
    if message.from_user.id != OWNER_ID:
        return

    # /pay @username 100
    parts = message.text.strip().split()
    if len(parts) < 3:
        bot.send_message(message.chat.id, "Использование: /pay @username сумма")
        return

    raw_username = parts[1].lstrip("@")
    try:
        amount = int(parts[2])
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, "Укажи корректную сумму монет (целое положительное число).")
        return

    target = get_user_by_username(raw_username)
    if not target:
        bot.send_message(message.chat.id,
            f"Пользователь @{raw_username} не найден в базе.")
        return

    target_id = target[0]
    add_balance(target_id, amount)
    new_balance = get_balance(target_id)

    bot.send_message(message.chat.id,
        f"Начислено {amount} монет пользователю @{raw_username}.\n"
        f"Новый баланс: {new_balance} монет.")
    try:
        bot.send_message(target_id,
            f"Тебе начислено {amount} монет от администратора!\n"
            f"Твой баланс: {new_balance} монет.\n"
            "Монетами можно оплатить подписку в разделе оплаты.")
    except:
        pass


# ── /testmode — переключение тест-режима для owner ────────────────────────

@bot.message_handler(commands=["testmode"])
def cmd_testmode(message):
    global owner_test_mode
    if message.from_user.id != OWNER_ID:
        return
    owner_test_mode = not owner_test_mode
    status = "ВКЛ 🧪" if owner_test_mode else "ВЫКЛ 👑"
    bot.send_message(
        message.chat.id,
        f"Тест-режим: {status}\n\n"
        + ("Теперь ты проходишь все проверки как обычный пользователь.\n"
           "Лимиты, подписка на каналы — всё активно.\n"
           "/testmode — чтобы вернуться в режим owner."
           if owner_test_mode else
           "Вернулся в режим owner. Все проверки отключены.")
    )



@bot.message_handler(func=lambda m: m.text == "🛠 Control Panel" and m.from_user.id == OWNER_ID)
def control_panel(message):
    send_control_panel(message.chat.id)


def send_control_panel(chat_id):
    maintenance = is_maintenance()
    stats = get_stats()

    roles_text = ""
    for r, cnt in stats.get("roles", {}).items():
        roles_text += f"  {r}: {cnt}\n"

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📋 Список пользователей", callback_data="admin_users"),
        types.InlineKeyboardButton("💰 Последние платежи",    callback_data="admin_payments"),
        types.InlineKeyboardButton("🔑 Статус API ключей",    callback_data="admin_check_keys"),
        types.InlineKeyboardButton("🎫 Управление подписками", callback_data="admin_subs_help"),
        types.InlineKeyboardButton(
            "🔴 Включить тех.работы" if not maintenance else "🟢 Выключить тех.работы",
            callback_data="admin_toggle_maintenance"
        )
    )

    bot.send_message(
        chat_id,
        f"🛠 Панель управления\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"🆕 Новых сегодня: {stats['new_today']}\n"
        f"💎 Подписчиков всего: {stats['subscribers']}\n"
        f"✅ Активных подписок: {stats['active_subs']}\n"
        f"🆓 Используют Core: {stats['free_users']}\n"
        f"⭐ Используют Nova: {stats['pro_users']}\n"
        f"📱 Пользователей Mini App: {stats['miniapp_users']}\n"
        f"💰 Всего платежей: {stats['total_payments']}\n"
        f"💬 Всего сообщений: {stats['total_messages']}\n\n"
        f"Роли:\n{roles_text}"
        f"🔧 Тех.работы: {'ВКЛ 🔴' if maintenance else 'ВЫКЛ 🟢'}",
        reply_markup=markup
    )


# ── Обработчики файлов ────────────────────────────────────────────────────

@bot.message_handler(content_types=["photo", "video", "audio", "voice"])
def handle_media(message):
    ensure_registered(message)
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user or user[4] == "none":
        show_start(message.chat.id, user_id)
        return
    handle_file_message(message, user_id, user[4])


@bot.message_handler(content_types=["document"])
def handle_document(message):
    ensure_registered(message)
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user or user[4] == "none":
        show_start(message.chat.id, user_id)
        return
    handle_file_message(message, user_id, user[4])


# ── Обработка подарков (тест) ─────────────────────────────────────────────

@bot.message_handler(content_types=["gift"])
def handle_gift(message):
    """Принимаем подарок как оплату подписки (тестовая функция)."""
    ensure_registered(message)
    user_id = message.from_user.id
    bot.send_message(
        message.chat.id,
        "Подарок получен! Администратор рассмотрит твой платёж и вручную активирует подписку.\n"
        "Обратись в поддержку если подписка не появилась в течение 24 часов."
    )
    try:
        bot.send_message(
            OWNER_ID,
            f"Пользователь @{message.from_user.username or '?'} (ID: {user_id}) "
            f"отправил подарок как оплату подписки."
        )
    except:
        pass


# ── Inline кнопки ─────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass

    # ── Проверка подписки на каналы ──
    if call.data == "check_subs":
        not_subbed = check_subscriptions(user_id)
        if not_subbed:
            # Ещё не подписан — показываем какие каналы остались
            markup = types.InlineKeyboardMarkup(row_width=1)
            for ch in not_subbed:
                markup.add(types.InlineKeyboardButton(
                    f"📢 {ch['title']}", url=ch["url"]
                ))
            markup.add(types.InlineKeyboardButton(
                "✅ Проверить снова", callback_data="check_subs"
            ))
            bot.send_message(
                chat_id,
                "❌ Ты ещё не подписан на все каналы.\n\n"
                + "\n".join(f"— {ch['title']}" for ch in not_subbed) +
                "\n\nПодпишись и нажми проверить снова 👇",
                reply_markup=markup
            )
        else:
            # Подписан — регистрируем и запускаем бота
            referred_by = None
            if user_id in user_states and "pending_ref" in user_states[user_id]:
                try:
                    referred_by = int(user_states[user_id]["pending_ref"])
                except:
                    pass
                del user_states[user_id]
            is_new = register_user(user_id, call.from_user.username or "", referred_by)
            if is_new and referred_by:
                try:
                    bot.send_message(referred_by, "Кто-то перешёл по твоей реферальной ссылке! +10 монет зачислено.")
                except:
                    pass
            bot.send_message(
                chat_id,
                "✅ Отлично! Подписка подтверждена.",
                reply_markup=main_menu_keyboard(user_id)
            )
            show_start(chat_id, user_id)
        return

    
    if call.data == "admin_users" and user_id == OWNER_ID:
        users = get_all_users()
        if not users:
            bot.send_message(chat_id, "Пользователей пока нет.")
            return
        lines = [f"👥 Всего пользователей: {len(users)}\n"]
        for u in users:
            uname   = f"@{u[1]}" if u[1] else f"ID:{u[0]}"
            role    = u[3] or "—"
            sub     = u[5] if u[5] != "none" else "—"
            balance = u[7] if len(u) > 7 else 0
            lines.append(f"{uname} | {role} | {sub} | 🪙{balance}")
        # Разбиваем на части по 3500 символов (лимит TG — 4096)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 3500:
                bot.send_message(chat_id, chunk)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk:
            bot.send_message(chat_id, chunk)
        return

    # ── Последние платежи ──
    if call.data == "admin_payments" and user_id == OWNER_ID:
        payments = get_payments(20)
        if not payments:
            bot.send_message(chat_id, "Платежей пока нет.")
            return
        text = "💰 Последние платежи:\n\n"
        for p in payments:
            text += f"@{p[0] or '?'} — {p[1]} через {p[2]} ({p[3]}₽) — {p[4]}\n"
        bot.send_message(chat_id, text)
        return

    # ── Статус API ключей ──
    if call.data == "admin_check_keys" and user_id == OWNER_ID:
        bot.send_message(chat_id, "Проверяю ключи, подожди...")
        try:
            status = check_keys_status()
            text = "🔑 Статус API ключей:\n\n"
            labels = {"core": "Core (бесплатная)", "nova": "Nova", "pro": "PRO", "absolution": "Absolution"}
            for tier, keys in status.items():
                text += f"{labels.get(tier, tier)}:\n"
                for k in keys:
                    emoji = "✅" if k["status"] == "ok" else ("🔴" if k["status"] in ("exhausted","invalid") else "⚠️")
                    text += f"  {emoji} {k['key']} — {k['status']}\n"
                text += "\n"
        except Exception as e:
            text = f"Ошибка проверки ключей: {e}"
        bot.send_message(chat_id, text)
        return

    # ── Помощь по управлению подписками ──
    if call.data == "admin_subs_help" and user_id == OWNER_ID:
        bot.send_message(
            chat_id,
            "🎫 Управление подписками и ролями:\n\n"
            "Выдать подписку:\n"
            "/give @username nova 30d\n"
            "/give @username nova forever\n\n"
            "Снять подписку:\n"
            "/give @username remove sub\n\n"
            "Выдать роль:\n"
            "/give @username role sponsor\n"
            "/give @username role beta-tester 30d\n\n"
            "Сбросить роль:\n"
            "/give @username remove role\n\n"
            "Выдать монеты:\n"
            "/pay @username 100"
        )
        return

    # ── Тех.работы — ТОЛЬКО owner может переключить ──
    if call.data == "admin_toggle_maintenance" and user_id == OWNER_ID:
        new_val = "0" if is_maintenance() else "1"
        set_setting("maintenance", new_val)
        status = "ВКЛ 🔴" if new_val == "1" else "ВЫКЛ 🟢"
        markup = types.InlineKeyboardMarkup()
        if new_val == "1":
            markup.add(types.InlineKeyboardButton(
                "🟢 Выключить тех.работы",
                callback_data="admin_toggle_maintenance"
            ))
        bot.send_message(chat_id, f"Тех.работы: {status}", reply_markup=markup if new_val == "1" else None)
        return

    # ── Подарок (тест) ──
    if call.data == "pay_gift_menu":
        markup = types.InlineKeyboardMarkup(row_width=1)
        for plan_key, plan_data in PRICES.items():
            markup.add(types.InlineKeyboardButton(
                f"🎁 {plan_key.upper()} — {plan_data['stars']} звёзд",
                callback_data=f"pay_gift_info_{plan_key}"
            ))
        markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="choose_pro"))
        bot.send_message(
            chat_id,
            "🎁 Оплата подарком [TEST]\n\n"
            "Это тестовая функция. Отправь боту подарок на сумму звёзд соответствующую тарифу.\n"
            "После отправки подарка администратор вручную активирует подписку.\n\n"
            "Выбери тариф:",
            reply_markup=markup
        )
        return

    if call.data.startswith("pay_gift_info_"):
        plan = call.data.replace("pay_gift_info_", "")
        price = PRICES.get(plan)
        if price:
            bot.send_message(
                chat_id,
                f"🎁 Тариф: {price['label']}\n\n"
                f"Отправь боту подарок на {price['stars']} звёзд.\n"
                "После отправки подарка напиши /start и обратись к администратору."
            )
        return

    # ── Beta-tester за 50 звёзд ──
    if call.data == "buy_beta_tester":
        bot.send_invoice(
            chat_id,
            title="Роль Beta-Tester",
            description="Получи роль Beta-Tester в Elyon AI",
            invoice_payload=f"beta_tester_{user_id}",
            provider_token="",
            currency="XTR",
            prices=[types.LabeledPrice("Beta-Tester роль", BETA_TESTER_STARS)]
        )
        return

    # ── Custom days ──
    if call.data == "pay_custom":
        user_states[user_id] = {"state": "waiting_custom_days"}
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="choose_pro"))
        bot.send_message(chat_id, "Произвольная подписка\n\nВведи количество дней (1–365):",
                         reply_markup=markup)
        return

    if call.data == "back_start":
        show_start(chat_id, user_id)
    elif call.data == "choose_free":
        set_ai_model(user_id, "gpt")
        clear_history(user_id)
        bot.send_message(chat_id, "Elyon Core активирован!\n\nБыстрый бесплатный AI. Начинай общение!",
                         reply_markup=main_menu_keyboard(user_id))
    elif call.data == "choose_pro":
        if has_active_sub(user_id):
            set_ai_model(user_id, "gemini")
            clear_history(user_id)
            bot.send_message(chat_id, "Elyon Nova активирован!\n\nRежим глубокого мышления.",
                             reply_markup=main_menu_keyboard(user_id))
        else:
            show_payment_options(chat_id, user_id)
    elif call.data == "pay_virtual_menu":
        show_virtual_payment(chat_id, user_id)
    elif call.data.startswith("pay_virtual_"):
        plan = call.data.replace("pay_virtual_", "")
        cost = VIRTUAL_PRICES[plan]["rub"]
        if spend_balance(user_id, cost):
            activate_subscription(user_id, plan, chat_id)
            log_payment(user_id, call.from_user.username or "", plan, "coins", str(cost))
        else:
            bot.send_message(chat_id, "Недостаточно монет.")
    elif call.data.startswith("pay_stars_custom_"):
        days = int(call.data.replace("pay_stars_custom_", ""))
        rub, stars, _ = calc_custom_price(days)
        label = f"{days} дней — {stars} ⭐"
        bot.send_invoice(chat_id, title=f"Elyon Nova — {days} дней",
                         description=f"Доступ к Elyon Nova на {days} дней",
                         invoice_payload=f"custom_{days}", provider_token="",
                         currency="XTR", prices=[types.LabeledPrice(label, stars)])
    elif call.data.startswith("pay_crypto_custom_"):
        days = int(call.data.replace("pay_crypto_custom_", ""))
        rub, _, usdt = calc_custom_price(days)
        label = f"{days} дней — {rub} ₽"
        invoice = create_crypto_invoice(usdt, user_id, f"custom_{days}", label)
        if invoice:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(f"Оплатить {label}", url=invoice["pay_url"]))
            markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_start"))
            bot.send_message(chat_id, f"Оплата через CryptoBot\n{label}\n\nПосле оплаты нажми /check",
                             reply_markup=markup)
        else:
            bot.send_message(chat_id, "Ошибка создания платежа. Попробуй позже.")
    elif call.data.startswith("pay_stars_"):
        plan = call.data.replace("pay_stars_", "")
        price = PRICES[plan]
        bot.send_invoice(chat_id, title=f"Elyon Nova — {price['label']}",
                         description="Доступ к Elyon Nova (AI с глубоким мышлением)",
                         invoice_payload=f"pro_{plan}", provider_token="",
                         currency="XTR", prices=[types.LabeledPrice(price["label"], price["stars"])])
    elif call.data.startswith("pay_crypto_"):
        plan = call.data.replace("pay_crypto_", "")
        price = CRYPTO_PRICES[plan]
        invoice = create_crypto_invoice(price["amount"], user_id, plan)
        if invoice:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(f"Оплатить {price['label']}", url=invoice["pay_url"]))
            markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_start"))
            bot.send_message(chat_id, f"Оплата через CryptoBot\n{price['label']}\n\nПосле оплаты нажми /check",
                             reply_markup=markup)
        else:
            bot.send_message(chat_id, "Ошибка создания платежа. Попробуй позже.")


# ── Оплата ────────────────────────────────────────────────────────────────

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(query):
    bot.answer_pre_checkout_query(query.id, ok=True)


@bot.message_handler(content_types=["successful_payment"])
def payment_success(message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload

    # Beta-tester
    if payload.startswith("beta_tester_"):
        set_role(user_id, "beta-tester")
        log_payment(user_id, message.from_user.username or "", "beta-tester", "stars",
                    str(BETA_TESTER_STARS))
        bot.send_message(
            message.chat.id,
            f"Поздравляем! Роль Beta-Tester получена!\n"
            "Теперь в твоём профиле отображается специальная роль.",
            reply_markup=main_menu_keyboard(user_id)
        )
        return

    # Stars custom
    if payload.startswith("custom_"):
        days = int(payload.replace("custom_", ""))
        rub, stars, _ = calc_custom_price(days)
        activate_subscription(user_id, "custom", message.chat.id, days=days, label=f"{days} дней")
        log_payment(user_id, message.from_user.username or "", f"custom_{days}d", "stars", str(stars))
        return

    # Stars plan
    plan = payload.replace("pro_", "")
    activate_subscription(user_id, plan, message.chat.id)
    log_payment(user_id, message.from_user.username or "", plan, "stars",
                str(PRICES.get(plan, {}).get("stars", "?")))


@bot.message_handler(commands=["check"])
def check_crypto_payment(message):
    user_id = message.from_user.id
    try:
        response = requests.get(
            "https://pay.crypt.bot/api/getInvoices",
            headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN},
            params={"status": "paid"}
        )
        data = response.json()
        if data["ok"]:
            for invoice in data["result"]["items"]:
                payload = invoice.get("payload", "")
                if payload.startswith(str(user_id) + "_"):
                    plan_part = payload.split("_", 1)[1]
                    if plan_part.startswith("custom_"):
                        days = int(plan_part.replace("custom_", ""))
                        rub, _, usdt = calc_custom_price(days)
                        activate_subscription(user_id, "custom", message.chat.id, days=days, label=f"{days} дней")
                        log_payment(user_id, message.from_user.username or "", f"custom_{days}d", "crypto", str(usdt))
                    else:
                        activate_subscription(user_id, plan_part, message.chat.id)
                        log_payment(user_id, message.from_user.username or "", plan_part, "crypto",
                                    CRYPTO_PRICES.get(plan_part, {}).get("amount", "?"))
                    return
        bot.send_message(message.chat.id, "Платёж не найден. Попробуй через минуту.")
    except Exception as e:
        print("Check error:", e)
        bot.send_message(message.chat.id, "Ошибка проверки платежа.")


# ── Нижнее меню ───────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "💬 Chat with AI")
def menu_chat(message):
    user = get_user(message.from_user.id)
    if not user or user[4] == "none":
        show_start(message.chat.id, message.from_user.id)
        return
    model = "🆓 Elyon Core" if user[4] == "gpt" else "⭐ Elyon Nova"
    bot.send_message(message.chat.id,
                     f"Текущая модель: {model}\n\nНапиши сообщение или отправь файл!")


@bot.message_handler(func=lambda m: m.text == "👤 Personal account")
def menu_profile(message):
    register_user(message.from_user.id, message.from_user.username or "")
    user = get_user(message.from_user.id)
    if not user:
        return
    user_id   = user[0]
    sub_type  = user[5]
    sub_until = user[6]
    balance   = get_balance(user_id)
    ref_count = get_referral_count(user_id)
    ref_link  = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    if sub_type == "none":
        sub_info = "Нет подписки"
    elif sub_type == "forever":
        sub_info = "Навсегда"
    else:
        labels = {"month": "30 дней", "halfyear": "6 месяцев", "custom": "Custom"}
        sub_info = f"{labels.get(sub_type, sub_type)} (до {sub_until})"

    role_emoji = "👑" if user[3] == "owner" else ("🔬" if user[3] == "beta-tester" else "👤")

    # Кнопка beta-tester если ещё нет
    markup = None
    if user[3] not in ("owner", "beta-tester"):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "🔬 Купить роль Beta-Tester за 50 звёзд",
            callback_data="buy_beta_tester"
        ))

    bot.send_message(
        message.chat.id,
        f"Личный кабинет\n\n"
        f"Пользователь: @{user[1] or 'не указан'}\n"
        f"Зарегистрирован: {user[2]}\n"
        f"Роль: {role_emoji} {user[3]}\n"
        f"Подписка: {sub_info}\n\n"
        f"Баланс монет: {balance}\n"
        f"Рефералов: {ref_count}\n\n"
        f"Реферальная ссылка:\n{ref_link}\n\n"
        f"За каждого приглашённого друга +10 монет",
        reply_markup=markup
    )


@bot.message_handler(func=lambda m: m.text == "🆓 Elyon Core")
def switch_free(message):
    register_user(message.from_user.id, message.from_user.username or "")
    set_ai_model(message.from_user.id, "gpt")
    clear_history(message.from_user.id)
    bot.send_message(message.chat.id, "Elyon Core активирован!\n\nБыстрый бесплатный AI. Начинай общение!")


@bot.message_handler(func=lambda m: m.text == "⭐ Elyon Nova")
def switch_pro(message):
    register_user(message.from_user.id, message.from_user.username or "")
    user_id = message.from_user.id
    if has_active_sub(user_id):
        set_ai_model(user_id, "gemini")
        clear_history(user_id)
        bot.send_message(message.chat.id, "Elyon Nova активирован!\n\nAI с глубоким мышлением.")
    else:
        show_payment_options(message.chat.id, user_id)


# ── AI текстовые сообщения ────────────────────────────────────────────────

MENU_TEXTS = {"💬 Chat with AI", "👤 Personal account", "🆓 Elyon Core", "⭐ Elyon Nova", "🛠 Control Panel"}

def ensure_registered(message):
    register_user(message.from_user.id, message.from_user.username or "")


@bot.message_handler(func=lambda m: True)
def handle_message(message):
    ensure_registered(message)
    user_id = message.from_user.id

    # Проверка подписки на каналы
    if not is_privileged(user_id) and not is_subscribed(user_id):
        send_subscribe_prompt(message.chat.id)
        return

    # Ожидание ввода кастомных дней
    if user_id in user_states and user_states[user_id].get("state") == "waiting_custom_days":
        try:
            days = int(message.text.strip())
            if days < 1 or days > 365:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "Введи число от 1 до 365.")
            return
        del user_states[user_id]
        rub, stars, usdt = calc_custom_price(days)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"Оплатить {stars} звёздами", callback_data=f"pay_stars_custom_{days}"),
            types.InlineKeyboardButton(f"Оплатить {rub} ₽ через CryptoBot", callback_data=f"pay_crypto_custom_{days}"),
            types.InlineKeyboardButton("◀️ Назад", callback_data="back_start"),
        )
        bot.send_message(
            message.chat.id,
            f"{days} дней подписки\n\n"
            f"Звёзды: {stars}\n"
            f"Рубли: {rub} ₽\n\n"
            f"Выбери способ оплаты:",
            reply_markup=markup
        )
        return

    if message.text and message.text in MENU_TEXTS:
        return

    if is_maintenance() and not is_privileged(user_id):
        bot.send_message(message.chat.id,
                         "Технические работы\n\nElyon AI временно недоступен.")
        return

    user = get_user(user_id)
    if not user:
        show_start(message.chat.id, user_id)
        return

    ai_model = user[4]
    if ai_model == "none":
        show_start(message.chat.id, user_id)
        return

    if ai_model == "gemini" and not has_active_sub(user_id):
        bot.send_message(message.chat.id, "Подписка истекла.")
        show_payment_options(message.chat.id, user_id)
        return

    # Проверка дневного лимита (в тест-режиме owner тоже проверяется)
    is_pro = (ai_model == "gemini")
    allowed, current, limit = check_daily_limit(user_id, is_pro)
    if not allowed and not (user_id == OWNER_ID and not owner_test_mode):
        model_name = "Elyon Nova" if is_pro else "Elyon Core"
        bot.send_message(
            message.chat.id,
            f"Вы достигли дневного лимита сообщений ({limit}/{limit}).\n\n"
            f"Лимит для {model_name}: {limit} сообщений в день.\n"
            f"Лимит обновится в 00:00 по московскому времени."
        )
        return

    bot.send_chat_action(message.chat.id, "typing")
    add_message(user_id, "user", message.text)
    log_session_activity(user_id, "bot")
    history = get_history(user_id)

    try:
        reply = ask_gpt(history) if ai_model == "gpt" else ask_gemini(history)
        increment_daily_count(user_id)
        add_message(user_id, "assistant", reply)
        bot.send_message(message.chat.id, reply)

    except Exception as e:
        error_text = str(e)
        print("AI error:", e)
        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            bot.send_message(message.chat.id, "Слишком много запросов. Попробуй через минуту.")
        elif "404" in error_text or "NOT_FOUND" in error_text:
            bot.send_message(message.chat.id, "Модель недоступна. Обратитесь к администратору.")
        else:
            bot.send_message(message.chat.id, f"Ошибка: {error_text[:200]}")


# ── Flask API ─────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

@app.after_request
def after_request(response):
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
    response.headers.add("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
    return response


@app.route("/api/chat", methods=["POST", "OPTIONS"])
def api_chat():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data     = request.json
        user_id  = data.get("user_id")
        model    = data.get("model", "gpt")
        messages = data.get("messages", [])
        chat_id  = data.get("chat_id")
        if not user_id or not messages:
            return jsonify({"error": "Missing user_id or messages"}), 400
        if is_maintenance() and not (user_id == OWNER_ID and not owner_test_mode):
            return jsonify({"error": "Maintenance in progress"}), 503
        if model in ("gemini", "nova"):
            if not has_active_sub(user_id):
                return jsonify({"error": "No active subscription"}), 403
            reply = ask_nova(messages)
        elif model == "pro":
            if not has_active_sub(user_id):
                return jsonify({"error": "No active subscription"}), 403
            reply = ask_pro(messages)
        elif model == "absolution":
            if not has_active_sub(user_id):
                return jsonify({"error": "No active subscription"}), 403
            reply = ask_absolution(messages)
        else:
            # Core — DeepSeek
            reply = ask_gpt(messages)

        # Проверка дневного лимита (owner в обычном режиме — без лимита)
        if not (user_id == OWNER_ID and not owner_test_mode):
            is_pro = (model == "gemini")
            allowed, current, limit = check_daily_limit(user_id, is_pro)
            if not allowed:
                return jsonify({"error": "daily_limit", "limit": limit}), 429

        increment_daily_count(user_id)
        log_session_activity(user_id, "miniapp")
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<int:user_id>", methods=["GET", "OPTIONS"])
def api_user(user_id):
    if request.method == "OPTIONS":
        return "", 204
    user = get_user(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "user_id":          user[0],
        "username":         user[1],
        "role":             user[3],
        "ai_model":         user[4],
        "sub_type":         user[5],
        "sub_until":        user[6],
        "balance":          get_balance(user_id),
        "referrals":        get_referral_count(user_id),
        "has_sub":          has_active_sub(user_id),
        "ref_link":         f"https://t.me/{BOT_USERNAME}?start={user_id}",
        "purchase_history": [list(p) for p in get_user_purchase_history(user_id)],
    })


# ── Mini App — чаты (сохранение на сервере) ───────────────────────────────

@app.route("/api/chats/<int:user_id>", methods=["GET", "OPTIONS"])
def api_get_chats(user_id):
    if request.method == "OPTIONS":
        return "", 204
    chats = get_mini_app_chats(user_id)
    return jsonify({"chats": chats})


@app.route("/api/chats/<int:user_id>", methods=["POST", "OPTIONS"])
def api_save_chat(user_id):
    if request.method == "OPTIONS":
        return "", 204
    try:
        data     = request.json
        chat_id  = data.get("chat_id")
        title    = data.get("title", "New chat")
        model    = data.get("model", "gpt")
        messages = data.get("messages", [])
        if not chat_id:
            return jsonify({"error": "Missing chat_id"}), 400
        save_mini_app_chat(user_id, chat_id, title, model, messages)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chats/<int:user_id>/<chat_id>", methods=["DELETE", "OPTIONS"])
def api_delete_chat(user_id, chat_id):
    if request.method == "OPTIONS":
        return "", 204
    delete_mini_app_chat(user_id, chat_id)
    return jsonify({"ok": True})


@app.route("/api/file_b64", methods=["POST", "OPTIONS"])
def api_file_b64():
    if request.method == "OPTIONS":
        return "", 204
    try:
        import base64 as b64
        import json as json_lib
        data      = request.json
        user_id   = data.get("user_id")
        model     = data.get("model", "gpt")
        prompt    = data.get("prompt", "Проанализируй этот файл")
        file_name = data.get("file_name", "file")
        file_type = data.get("file_type", "application/octet-stream")
        file_b64  = data.get("file_data", "")
        history   = data.get("history", [])
        if not user_id:
            return jsonify({"error": "Missing user_id"}), 400
        user_id = int(user_id)
        if is_maintenance() and not (user_id == OWNER_ID and not owner_test_mode):
            return jsonify({"error": "Maintenance in progress"}), 503
        if model == "gemini" and not has_active_sub(user_id):
            return jsonify({"error": "No active subscription"}), 403
        file_bytes = b64.b64decode(file_b64)
        mime_type  = file_type
        if not mime_type or mime_type == "application/octet-stream":
            ext = os.path.splitext(file_name.lower())[1]
            mime_map = {
                ".py": "text/x-python", ".js": "text/javascript",
                ".cpp": "text/x-c++", ".c": "text/x-c",
                ".java": "text/x-java", ".txt": "text/plain",
                ".md": "text/plain", ".csv": "text/csv",
                ".json": "application/json", ".html": "text/html",
                ".css": "text/css", ".sql": "text/plain",
                ".gif": "image/gif", ".png": "image/png",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".pdf": "application/pdf",
            }
            mime_type = mime_map.get(ext, "text/plain")
        tier_map   = {"gpt": "core", "core": "core", "nova": "nova",
                      "gemini": "nova", "pro": "pro", "absolution": "absolution"}
        use_pro    = model in ("gemini", "nova", "pro", "absolution")
        model_tier = tier_map.get(model, "core")
        reply = ask_with_file(file_bytes, mime_type, file_name, prompt,
                              history, use_pro=use_pro, model_tier=model_tier)
        log_session_activity(user_id, "miniapp")
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/file", methods=["POST", "OPTIONS"])
def api_file():
    if request.method == "OPTIONS":
        return "", 204
    try:
        import json as json_lib
        user_id     = request.form.get("user_id")
        model       = request.form.get("model", "gpt")
        prompt      = request.form.get("prompt", "Проанализируй этот файл")
        history_raw = request.form.get("history", "[]")
        history     = json_lib.loads(history_raw)
        if not user_id:
            return jsonify({"error": "Missing user_id"}), 400
        user_id = int(user_id)
        if is_maintenance() and not (user_id == OWNER_ID and not owner_test_mode):
            return jsonify({"error": "Maintenance in progress"}), 503
        if model == "gemini" and not has_active_sub(user_id):
            return jsonify({"error": "No active subscription"}), 403
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400
        f          = request.files["file"]
        file_bytes = f.read()
        file_name  = f.filename or "file"
        mime_type  = f.content_type or "application/octet-stream"
        if mime_type == "application/octet-stream":
            ext = os.path.splitext(file_name.lower())[1]
            mime_map = {
                ".py": "text/x-python", ".js": "text/javascript",
                ".cpp": "text/x-c++", ".c": "text/x-c",
                ".java": "text/x-java", ".txt": "text/plain",
                ".md": "text/plain", ".csv": "text/csv",
                ".json": "application/json", ".html": "text/html",
                ".css": "text/css", ".sql": "text/plain",
            }
            mime_type = mime_map.get(ext, "text/plain")
        tier_map   = {"gpt": "core", "core": "core", "nova": "nova",
                      "gemini": "nova", "pro": "pro", "absolution": "absolution"}
        use_pro    = model in ("gemini", "nova", "pro", "absolution")
        model_tier = tier_map.get(model, "core")
        reply = ask_with_file(file_bytes, mime_type, file_name, prompt,
                              history, use_pro=use_pro, model_tier=model_tier)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Auth endpoints ────────────────────────────────────────────────────────

import hashlib
import hmac
import time
import json as _json

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID",
    "468899724697-mct44qubsrdaps8ll6m4npv34k6jeucn.apps.googleusercontent.com")


def _upsert_web_user(email, first_name, last_name, avatar="", provider="email"):
    """Создаёт или обновляет пользователя из веб-приложения. Возвращает user dict."""
    import database as _db
    import sqlite3

    db_conn   = _db.conn
    db_cursor = _db.cursor

    # Ищем по email
    db_cursor.execute("SELECT user_id FROM users WHERE username = ?", (email,))
    row = db_cursor.fetchone()
    if row:
        user_id = row[0]
    else:
        user_id = abs(hash(email + provider)) % (10**12)
        db_cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, joined_at, role) VALUES (?, ?, ?, ?)",
            (user_id, email, datetime.now().strftime("%d.%m.%Y %H:%M"), "default user")
        )
        db_conn.commit()
    return {
        "user_id":    user_id,
        "email":      email,
        "first_name": first_name,
        "last_name":  last_name,
        "avatar":     avatar,
        "provider":   provider,
    }


@app.route("/api/auth/google", methods=["POST", "OPTIONS"])
def auth_google():
    """Верифицирует Google JWT токен (credential от Google Identity Services)."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        from urllib.request import urlopen
        import base64 as _b64

        data  = request.json or {}
        token = data.get("token", "")
        if not token:
            return jsonify({"ok": False, "error": "Missing token"}), 400

        # Декодируем JWT payload (без верификации подписи — для продакшна
        # нужна библиотека google-auth, но для старта достаточно)
        parts = token.split(".")
        if len(parts) < 2:
            return jsonify({"ok": False, "error": "Invalid token"}), 400

        # Добавляем паддинг для base64
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = _json.loads(_b64.urlsafe_b64decode(payload_b64))

        # Базовая проверка
        if payload.get("aud") != GOOGLE_CLIENT_ID:
            return jsonify({"ok": False, "error": "Invalid audience"}), 401
        if payload.get("exp", 0) < time.time():
            return jsonify({"ok": False, "error": "Token expired"}), 401

        email      = payload.get("email", "")
        first_name = payload.get("given_name", "")
        last_name  = payload.get("family_name", "")
        avatar     = payload.get("picture", "")

        if not email:
            return jsonify({"ok": False, "error": "No email in token"}), 400

        user = _upsert_web_user(email, first_name, last_name, avatar, "google")
        return jsonify({"ok": True, "user": user})

    except Exception as e:
        print("auth_google error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/auth/google_profile", methods=["POST", "OPTIONS"])
def auth_google_profile():
    """Принимает профиль пользователя из Google OAuth (access token flow)."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        data    = request.json or {}
        profile = data.get("profile", {})
        email      = profile.get("email", "")
        first_name = profile.get("given_name", "")
        last_name  = profile.get("family_name", "")
        avatar     = profile.get("picture", "")

        if not email:
            return jsonify({"ok": False, "error": "No email in profile"}), 400

        user = _upsert_web_user(email, first_name, last_name, avatar, "google")
        return jsonify({"ok": True, "user": user})

    except Exception as e:
        print("auth_google_profile error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/auth/telegram", methods=["POST", "OPTIONS"])
def auth_telegram():
    """
    Верифицирует данные от Telegram Login Widget.
    Проверяет HMAC-SHA256 подпись используя BOT_TOKEN.
    """
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.json or {}
        user = data.get("user", {})

        if not user or "id" not in user:
            return jsonify({"ok": False, "error": "Missing user data"}), 400

        # Верификация подписи Telegram
        auth_data = {k: v for k, v in user.items() if k != "hash"}
        check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(auth_data.items())
        )
        secret_key = hashlib.sha256(TOKEN.encode()).digest()
        computed   = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

        if computed != user.get("hash", ""):
            return jsonify({"ok": False, "error": "Invalid signature"}), 401

        # Проверяем свежесть (не старше 24 часов)
        auth_date = int(user.get("auth_date", 0))
        if time.time() - auth_date > 86400:
            return jsonify({"ok": False, "error": "Auth data expired"}), 401

        tg_id      = user["id"]
        username   = user.get("username", f"tg_{tg_id}")
        first_name = user.get("first_name", "")
        last_name  = user.get("last_name", "")
        avatar     = user.get("photo_url", "")

        # Регистрируем / обновляем пользователя
        register_user(tg_id, username)

        web_user = {
            "user_id":    tg_id,
            "email":      f"{username}@telegram",
            "first_name": first_name,
            "last_name":  last_name,
            "avatar":     avatar,
            "provider":   "telegram",
            "username":   username,
        }
        return jsonify({"ok": True, "user": web_user})

    except Exception as e:
        print("auth_telegram error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/auth/email", methods=["POST", "OPTIONS"])
def auth_email():
    """Email/password авторизация. В продакшне добавить хэширование паролей."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        import hashlib as _hl
        import database as _db

        data       = request.json or {}
        action     = data.get("action", "signin")
        email      = data.get("email", "").lower().strip()
        password   = data.get("password", "")
        first_name = data.get("first_name", "")
        last_name  = data.get("last_name", "")

        if not email or not password:
            return jsonify({"ok": False, "error": "Missing email or password"}), 400

        pw_hash = _hl.sha256(password.encode()).hexdigest()

        if action == "signup":
            _db.cursor.execute("SELECT user_id FROM users WHERE username = ?", (email,))
            if _db.cursor.fetchone():
                return jsonify({"ok": False, "error": "Email already registered"}), 409

            user_id = abs(hash(email + pw_hash)) % (10**12)
            _db.cursor.execute(
                "INSERT OR IGNORE INTO users (user_id, username, joined_at, role) VALUES (?, ?, ?, ?)",
                (user_id, email, datetime.now().strftime("%d.%m.%Y %H:%M"), "default user")
            )
            _db.cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (f"pw_{email}", pw_hash)
            )
            _db.conn.commit()
            user = _upsert_web_user(email, first_name, last_name, "", "email")
            return jsonify({"ok": True, "verify": False, "user": user})

        else:
            stored = get_setting(f"pw_{email}")
            if not stored or stored != pw_hash:
                return jsonify({"ok": False, "error": "Invalid email or password"}), 401
            user = _upsert_web_user(email, "", "", "", "email")
            return jsonify({"ok": True, "user": user})

    except Exception as e:
        print("auth_email error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/admin/stats", methods=["GET", "OPTIONS"])
def api_admin_stats():
    if request.method == "OPTIONS":
        return "", 204
    stats = get_stats()
    return jsonify(stats)


@app.route("/api/admin/users", methods=["GET", "OPTIONS"])
def api_admin_users():
    if request.method == "OPTIONS":
        return "", 204
    users = get_all_users()
    result = []
    for u in users:
        result.append({
            "user_id":  u[0],
            "username": u[1] or "",
            "role":     u[3] or "default user",
            "sub_type": u[5] or "none",
            "balance":  u[7] if len(u) > 7 else 0,
        })
    return jsonify({"users": result})


@app.route("/api/bot_info", methods=["GET"])
def api_bot_info():
    try:
        info = bot.get_me()
        return jsonify({"bot_id": info.id, "username": info.username})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/auth/telegram_token", methods=["POST", "OPTIONS"])
def auth_telegram_token():
    """Верифицирует токен из /auth команды бота."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        data  = request.json or {}
        token = data.get("token", "")
        if not token:
            return jsonify({"ok": False, "error": "Missing token"}), 400

        info = _auth_tokens.get(token)
        if not info:
            return jsonify({"ok": False, "error": "Invalid or already used token"}), 401

        if datetime.now().timestamp() > info["expires"]:
            _auth_tokens.pop(token, None)
            return jsonify({"ok": False, "error": "Token expired. Use /auth again in bot."}), 401

        # Одноразовый — удаляем после использования
        _auth_tokens.pop(token, None)

        user_id  = info["user_id"]
        username = info["username"]
        register_user(user_id, username)
        user     = get_user(user_id)
        sub_type = user[5] if user else "none"

        web_user = {
            "user_id":    user_id,
            "email":      f"{username}@telegram" if username else f"tg_{user_id}@telegram",
            "first_name": info["first_name"],
            "last_name":  "",
            "avatar":     "",
            "provider":   "telegram",
            "username":   username,
            "sub_type":   sub_type,
        }
        return jsonify({"ok": True, "user": web_user})

    except Exception as e:
        print("auth_telegram_token error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# ── Запуск ────────────────────────────────────────────────────────────────

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)


def keep_alive():
    import time
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://elyon-bot.onrender.com")
    while True:
        time.sleep(600)
        try:
            requests.get(f"{url}/health", timeout=10)
            print("keep_alive ping sent")
        except Exception as e:
            print(f"keep_alive error: {e}")


def start_polling():
    """Запускает polling с защитой от 409 конфликта."""
    import time as _time
    # Сначала удаляем webhook если есть
    try:
        bot.delete_webhook(drop_pending_updates=True)
        print("Webhook cleared.")
    except Exception as e:
        print(f"Webhook clear error: {e}")

    retries = 0
    while True:
        try:
            print("Starting polling...")
            bot.infinity_polling(timeout=20, long_polling_timeout=5)
            break
        except Exception as e:
            err = str(e)
            if "409" in err or "Conflict" in err:
                retries += 1
                wait = min(30, 5 * retries)
                print(f"409 Conflict — another instance running. Retry in {wait}s...")
                _time.sleep(wait)
            else:
                print(f"Polling error: {e}")
                _time.sleep(5)


try:
    print("bot is running...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    start_polling()
except KeyboardInterrupt:
    print("Stopped.")
except Exception as e:
    print("Error:", e)
