import sys
import io
import os
import tempfile
import threading as _threading
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
owner_test_mode = False

def is_privileged(user_id):
    return user_id == OWNER_ID and not owner_test_mode

# ── Авто-удаление служебных сообщений ────────────────────────────────────

def send_and_delete(chat_id, text, delay=25, reply_markup=None, parse_mode=None):
    """
    Отправляет сообщение и удаляет его через delay секунд.
    Для служебных сообщений чтобы не засорять чат.
    Важные ответы AI НЕ удаляются.
    """
    try:
        kwargs = {}
        if reply_markup: kwargs['reply_markup'] = reply_markup
        if parse_mode:   kwargs['parse_mode']   = parse_mode
        msg = bot.send_message(chat_id, text, **kwargs)
        def _delete():
            try:
                bot.delete_message(chat_id, msg.message_id)
            except:
                pass
        t = _threading.Timer(delay, _delete)
        t.daemon = True
        t.start()
        return msg
    except Exception as e:
        print(f"send_and_delete error: {e}")
        return None

def delete_msg(chat_id, message_id, delay=0):
    """Удаляет сообщение (сразу или через delay секунд)."""
    def _do():
        try:
            bot.delete_message(chat_id, message_id)
        except:
            pass
    if delay > 0:
        t = _threading.Timer(delay, _do)
        t.daemon = True
        t.start()
    else:
        _do()

# ── Обязательные каналы ───────────────────────────────────────────────────

REQUIRED_CHANNELS = [
    {"id": "@unkonyy",   "title": "Owner channel",    "url": "https://t.me/unkonyy"},
    {"id": "@AI_Elyon",  "title": "Elyon AI channel", "url": "https://t.me/AI_Elyon"},
]

def check_subscriptions(user_id):
    not_subscribed = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = bot.get_chat_member(ch["id"], user_id)
            if member.status in ("left", "kicked", "banned"):
                not_subscribed.append(ch)
        except Exception as e:
            print(f"Sub check error for {ch['id']}: {e}")
    return not_subscribed

def send_subscribe_prompt(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for ch in REQUIRED_CHANNELS:
        markup.add(types.InlineKeyboardButton(f"📢 {ch['title']}", url=ch["url"]))
    markup.add(types.InlineKeyboardButton("✅ Я подписался — проверить", callback_data="check_subs"))
    send_and_delete(chat_id,
        "👋 Добро пожаловать в Elyon AI!\n\n"
        "Для использования бота подпишись на каналы:\n\n"
        "📢 Owner channel — @unkonyy\n"
        "📢 Elyon AI channel — @AI_Elyon\n\n"
        "После подписки нажми кнопку ниже 👇",
        delay=120, reply_markup=markup)

def is_subscribed(user_id):
    return len(check_subscriptions(user_id)) == 0

# ── Цены ──────────────────────────────────────────────────────────────────

PRICES = {
    "nova":       {"stars": 50,  "label": "Elyon Nova — 50 ⭐",       "days": 30, "rub": 91},
    "pro":        {"stars": 100, "label": "Elyon PRO — 100 ⭐",        "days": 30, "rub": 182},
    "absolution": {"stars": 150, "label": "Elyon Absolution — 150 ⭐", "days": 30, "rub": 265},
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
STARS_PER_RUB   = 1 / 1.82
RUB_PER_DAY     = 50 / 30
BETA_TESTER_STARS = 50

GEMINI_SUPPORTED = {
    "image/jpeg","image/png","image/gif","image/webp",
    "video/mp4","video/mpeg","video/mov","video/avi","video/webm",
    "audio/mp3","audio/mpeg","audio/wav","audio/ogg","audio/flac",
    "application/pdf",
    "text/plain","text/html","text/css","text/javascript",
    "text/x-python","text/x-c","text/x-c++","text/x-java",
    "application/json","text/csv","text/xml",
}
TEXT_EXTENSIONS = {
    ".py":"text/x-python",".js":"text/javascript",".ts":"text/javascript",
    ".cpp":"text/x-c++",".c":"text/x-c",".h":"text/x-c",
    ".java":"text/x-java",".cs":"text/plain",".go":"text/plain",
    ".rs":"text/plain",".php":"text/plain",".rb":"text/plain",
    ".swift":"text/plain",".kt":"text/plain",".sh":"text/plain",
    ".txt":"text/plain",".md":"text/plain",".csv":"text/csv",
    ".json":"application/json",".xml":"text/xml",".html":"text/html",
    ".css":"text/css",".sql":"text/plain",".yaml":"text/plain",
    ".yml":"text/plain",".toml":"text/plain",".env":"text/plain",
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
    bot.send_message(chat_id,
        "👋 Добро пожаловать в Elyon AI!\n\n"
        "🆓 Elyon Core — бесплатно, быстрые ответы\n"
        "⭐ Elyon Nova — про, глубокое мышление\n\n"
        "Выбери версию:", reply_markup=markup)

def show_payment_options(chat_id, user_id):
    balance = get_balance(user_id)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⭐ Nova — 50 звёзд",       callback_data="pay_stars_nova"),
        types.InlineKeyboardButton("💳 Nova — 91 ₽",            callback_data="pay_crypto_nova"),
        types.InlineKeyboardButton("⭐ PRO — 100 звёзд",        callback_data="pay_stars_pro"),
        types.InlineKeyboardButton("💳 PRO — 182 ₽",            callback_data="pay_crypto_pro"),
        types.InlineKeyboardButton("⭐ Abs — 150 звёзд",        callback_data="pay_stars_absolution"),
        types.InlineKeyboardButton("💳 Abs — 265 ₽",            callback_data="pay_crypto_absolution"),
        types.InlineKeyboardButton("📅 Произвольный срок",      callback_data="pay_custom"),
        types.InlineKeyboardButton("🎁 Подарком [TEST]",        callback_data="pay_gift_menu"),
    )
    if balance >= 50:
        markup.add(types.InlineKeyboardButton(
            f"🪙 Монеты (баланс: {balance})", callback_data="pay_virtual_menu"))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_start"))
    bot.send_message(chat_id,
        "⭐ Elyon AI — Подписка\n\n"
        "Выбери тариф и способ оплаты:",
        reply_markup=markup)

def show_virtual_payment(chat_id, user_id):
    balance = get_balance(user_id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for plan, data in VIRTUAL_PRICES.items():
        if balance >= data["rub"]:
            markup.add(types.InlineKeyboardButton(
                f"🪙 {data['label']}", callback_data=f"pay_virtual_{plan}"))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="choose_pro"))
    bot.send_message(chat_id,
        f"🪙 Оплата монетами\n\nБаланс: {balance} монет\n\nВыбери тариф:",
        reply_markup=markup)

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
    bot.send_message(chat_id,
        f"✅ Подписка активирована!\nТариф: {price_label}\n\nТеперь ты используешь Elyon Nova 🌟",
        reply_markup=main_menu_keyboard(user_id))
    try:
        uname = bot.get_chat(user_id).username or "без username"
        bot.send_message(OWNER_ID, f"💰 Новая подписка!\n@{uname} | {user_id}\nТариф: {price_label}")
    except:
        pass

def create_crypto_invoice(amount, user_id, plan, label=None):
    try:
        response = requests.post(
            "https://pay.crypt.bot/api/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN},
            json={"asset":"USDT","amount":str(amount),
                  "description":f"Elyon — {label or plan}",
                  "payload":f"{user_id}_{plan}","expires_in":3600})
        data = response.json()
        if data["ok"]:
            return data["result"]
    except Exception as e:
        print("CryptoBot error:", e)
    return None

# ── Файлы ─────────────────────────────────────────────────────────────────

def download_telegram_file(file_id):
    file_info = bot.get_file(file_id)
    file_url  = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
    return requests.get(file_url, timeout=30).content

def handle_file_message(message, user_id, ai_model):
    user = get_user(user_id)
    if not user:
        return
    if is_maintenance() and user_id != OWNER_ID:
        send_and_delete(message.chat.id, "🔧 Технические работы")
        return
    if ai_model == "gemini" and not has_active_sub(user_id):
        send_and_delete(message.chat.id, "⚠️ Подписка истекла.")
        show_payment_options(message.chat.id, user_id)
        return

    file_id = None; file_name = "file"; mime_type = None
    caption = message.caption or "Проанализируй этот файл и опиши его содержимое."

    if message.content_type == "photo":
        file_id = message.photo[-1].file_id; mime_type = "image/jpeg"; file_name = "image.jpg"
    elif message.content_type == "document":
        doc = message.document; file_id = doc.file_id
        file_name = doc.file_name or "document"; mime_type = doc.mime_type
        if not mime_type or mime_type == "application/octet-stream":
            mime_type = get_mime_for_extension(file_name)
    elif message.content_type == "video":
        file_id = message.video.file_id; mime_type = "video/mp4"; file_name = "video.mp4"
    elif message.content_type == "audio":
        file_id = message.audio.file_id; mime_type = "audio/mpeg"; file_name = "audio.mp3"
    elif message.content_type == "voice":
        file_id = message.voice.file_id; mime_type = "audio/ogg"; file_name = "voice.ogg"

    if not file_id:
        send_and_delete(message.chat.id, "❌ Неподдерживаемый тип файла.")
        return
    if mime_type not in GEMINI_SUPPORTED:
        ext = os.path.splitext(file_name.lower())[1]
        if ext in [".zip",".rar",".7z",".tar"]:
            send_and_delete(message.chat.id, "Архивы нельзя анализировать. Распакуй и отправь файлы по отдельности.")
            return
        if ext in [".docx",".xlsx",".xls",".doc"]:
            send_and_delete(message.chat.id, "Word/Excel: лучше сохрани как PDF или TXT.")
            mime_type = "application/octet-stream"

    bot.send_chat_action(message.chat.id, "typing")
    log_session_activity(user_id, "bot")
    try:
        file_bytes = download_telegram_file(file_id)
        history    = get_history(user_id)
        add_message(user_id, "user", f"[Файл: {file_name}] {caption}")
        reply = ask_with_file(file_bytes, mime_type, file_name, caption, history,
                              use_pro=(ai_model != "gpt"))
        add_message(user_id, "assistant", reply)
        bot.send_message(message.chat.id, reply)  # AI ответ — НЕ удаляем
    except Exception as e:
        err = str(e)
        if "429" in err or "RESOURCE_EXHAUSTED" in err:
            send_and_delete(message.chat.id, "Слишком много запросов. Попробуй через минуту.")
        else:
            send_and_delete(message.chat.id, f"Ошибка анализа файла: {err[:150]}")

# ── /start ────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    # Удаляем команду /start пользователя
    delete_msg(message.chat.id, message.message_id, delay=2)

    args  = message.text.split()
    param = args[1] if len(args) > 1 else None

    if param and param.startswith("pay_"):
        tier_map = {"pay_nova":"nova","pay_pro":"pro","pay_abs":"absolution"}
        tier = tier_map.get(param)
        register_user(user_id, message.from_user.username or "")
        if tier:
            _show_single_tier_payment(message.chat.id, user_id, tier)
        else:
            _show_all_tiers_payment(message.chat.id, user_id)
        return

    if param == "auth":
        register_user(user_id, message.from_user.username or "")
        send_and_delete(message.chat.id,
            "Напиши /auth чтобы получить ссылку для входа на сайт Elyon AI.", delay=60)
        return

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
    show_start(message.chat.id, user_id)

# ── /auth ─────────────────────────────────────────────────────────────────

import secrets as _secrets
_auth_tokens = {}

@bot.message_handler(commands=["auth"])
def cmd_auth(message):
    delete_msg(message.chat.id, message.message_id, delay=2)
    user_id    = message.from_user.id
    username   = message.from_user.username or ""
    first_name = message.from_user.first_name or ""

    token   = _secrets.token_urlsafe(24)
    expires = datetime.now().timestamp() + 300

    _auth_tokens[token] = {
        "user_id": user_id, "username": username,
        "first_name": first_name, "expires": expires,
    }

    user      = get_user(user_id)
    sub_label = user[5] if user and user[5] != "none" else "нет подписки"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "✅ Войти на сайт Elyon AI",
        url=f"https://elyon-ai-web.vercel.app/auth.html?tg_token={token}"))
    # Это важное сообщение — не удаляем автоматически
    bot.send_message(message.chat.id,
        f"🔐 Авторизация на сайте\n\nСсылка действует 5 минут.\nПодписка: {sub_label}",
        reply_markup=markup)

# ── /give ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["give"])
def cmd_give(message):
    if message.from_user.id != OWNER_ID:
        return
    delete_msg(message.chat.id, message.message_id, delay=2)

    parts = message.text.strip().split()
    if len(parts) < 3:
        send_and_delete(message.chat.id,
            "Использование:\n"
            "/give @username nova 30d\n"
            "/give @username nova forever\n"
            "/give @username role sponsor\n"
            "/give @username remove sub\n"
            "/give @username remove role", delay=60)
        return

    raw_username = parts[1].lstrip("@")
    target = get_user_by_username(raw_username)
    if not target:
        send_and_delete(message.chat.id,
            f"Пользователь @{raw_username} не найден.", delay=30)
        return

    target_id = target[0]
    action    = parts[2].lower()

    if action == "nova":
        duration = parts[3].lower() if len(parts) > 3 else "30d"
        if duration == "forever":
            set_subscription(target_id, "forever", "none")
            set_ai_model(target_id, "gemini")
            label = "навсегда"; expires_str = "none"
        else:
            try:
                days = int(duration.replace("d","").replace("д",""))
            except:
                send_and_delete(message.chat.id, "Неверный формат срока. Пример: 30d", delay=20)
                return
            until = datetime.now() + timedelta(days=days)
            until_str = until.strftime("%d.%m.%Y %H:%M")
            set_subscription(target_id, "custom", until_str)
            set_ai_model(target_id, "gemini")
            label = f"{days} дней (до {until_str})"; expires_str = until_str
        log_admin_grant(target_id, "subscription", "nova", expires_str, OWNER_ID)
        send_and_delete(message.chat.id, f"✅ Подписка Nova выдана @{raw_username} на {label}.", delay=30)
        try:
            bot.send_message(target_id,
                f"Тебе выдана подписка Elyon Nova на {label}!")
        except:
            pass
        return

    if action == "role":
        if len(parts) < 4:
            send_and_delete(message.chat.id, "Укажи роль. Пример: /give @user role sponsor", delay=20)
            return
        role_name = parts[3]; expires_str = "none"
        if len(parts) > 4:
            try:
                days = int(parts[4].replace("d",""))
                expires_str = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y %H:%M")
            except:
                pass
        set_role(target_id, role_name)
        log_admin_grant(target_id, "role", role_name, expires_str, OWNER_ID)
        exp_label = f"до {expires_str}" if expires_str != "none" else "бессрочно"
        send_and_delete(message.chat.id, f"✅ Роль '{role_name}' выдана @{raw_username} ({exp_label}).", delay=30)
        try:
            bot.send_message(target_id, f"Тебе присвоена роль: {role_name}!")
        except:
            pass
        return

    if action == "remove":
        what = parts[3].lower() if len(parts) > 3 else ""
        if what == "sub":
            remove_subscription(target_id)
            send_and_delete(message.chat.id, f"✅ Подписка @{raw_username} удалена.", delay=20)
            try:
                bot.send_message(target_id, "Твоя подписка была деактивирована.")
            except:
                pass
        elif what == "role":
            set_role(target_id, "default user")
            send_and_delete(message.chat.id, f"✅ Роль @{raw_username} сброшена.", delay=20)
        else:
            send_and_delete(message.chat.id, "Укажи: sub или role", delay=20)
        return

    send_and_delete(message.chat.id, "Неизвестное действие. Используй: nova / role / remove", delay=20)

# ── /pay — ОБЪЕДИНЁННЫЙ обработчик ───────────────────────────────────────

@bot.message_handler(commands=["pay"])
def cmd_pay_unified(message):
    """
    /pay            — все тарифы
    /pay nova/pro/abs — конкретный тариф
    /pay @username 100 — owner: выдача монет
    """
    delete_msg(message.chat.id, message.message_id, delay=2)
    parts   = message.text.strip().split()
    user_id = message.from_user.id
    tier    = parts[1].lower() if len(parts) > 1 else None

    # Owner: /pay @username сумма
    if user_id == OWNER_ID and tier and tier.startswith("@"):
        if len(parts) < 3:
            send_and_delete(message.chat.id, "Использование: /pay @username сумма", delay=20)
            return
        raw_username = parts[1].lstrip("@")
        try:
            amount = int(parts[2])
            if amount <= 0: raise ValueError
        except ValueError:
            send_and_delete(message.chat.id, "Укажи корректную сумму монет.", delay=20)
            return
        target = get_user_by_username(raw_username)
        if not target:
            send_and_delete(message.chat.id, f"Пользователь @{raw_username} не найден.", delay=20)
            return
        target_id = target[0]
        add_balance(target_id, amount)
        new_balance = get_balance(target_id)
        send_and_delete(message.chat.id,
            f"✅ Начислено {amount} монет @{raw_username}. Баланс: {new_balance}", delay=30)
        try:
            bot.send_message(target_id,
                f"Тебе начислено {amount} монет!\nБаланс: {new_balance} монет.")
        except:
            pass
        return

    # Обычный /pay
    tier_map = {
        "nova":"nova","n":"nova",
        "pro":"pro","p":"pro",
        "abs":"absolution","absolution":"absolution","a":"absolution",
    }
    target_tier = tier_map.get(tier) if tier else None
    if target_tier:
        _show_single_tier_payment(message.chat.id, user_id, target_tier)
    else:
        _show_all_tiers_payment(message.chat.id, user_id)

def _show_single_tier_payment(chat_id, user_id, tier):
    tier_info = {
        "nova":       {"label":"Elyon Nova",      "stars":50,  "rub":91,  "desc":"25 сообщений/день"},
        "pro":        {"label":"Elyon PRO",        "stars":100, "rub":182, "desc":"40 сообщений/день"},
        "absolution": {"label":"Elyon Absolution", "stars":150, "rub":265, "desc":"50 сообщений/день"},
    }
    info = tier_info.get(tier)
    if not info: return
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(f"⭐ {info['stars']} звёзд", callback_data=f"pay_stars_{tier}"),
        types.InlineKeyboardButton(f"💳 {info['rub']} ₽ (DonatePay)", callback_data=f"pay_crypto_{tier}"),
    )
    bot.send_message(chat_id,
        f"⭐ {info['label']}\n{info['desc']} · 30 дней\n\nВыбери способ оплаты:",
        reply_markup=markup)

def _show_all_tiers_payment(chat_id, user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⭐ Nova — 50 ⭐",          callback_data="pay_stars_nova"),
        types.InlineKeyboardButton("💳 Nova — 91 ₽",           callback_data="pay_crypto_nova"),
        types.InlineKeyboardButton("⭐ PRO — 100 ⭐",           callback_data="pay_stars_pro"),
        types.InlineKeyboardButton("💳 PRO — 182 ₽",           callback_data="pay_crypto_pro"),
        types.InlineKeyboardButton("⭐ Absolution — 150 ⭐",   callback_data="pay_stars_absolution"),
        types.InlineKeyboardButton("💳 Absolution — 265 ₽",   callback_data="pay_crypto_absolution"),
    )
    bot.send_message(chat_id,
        "⭐ Elyon AI — Подписка\n\nВыбери тариф и способ оплаты:",
        reply_markup=markup)

# ── /testmode ─────────────────────────────────────────────────────────────

@bot.message_handler(commands=["testmode"])
def cmd_testmode(message):
    global owner_test_mode
    if message.from_user.id != OWNER_ID:
        return
    delete_msg(message.chat.id, message.message_id, delay=2)
    owner_test_mode = not owner_test_mode
    status = "ВКЛ 🧪" if owner_test_mode else "ВЫКЛ 👑"
    send_and_delete(message.chat.id, f"Тест-режим: {status}", delay=30)

# ── Control Panel ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🛠 Control Panel" and m.from_user.id == OWNER_ID)
def control_panel(message):
    delete_msg(message.chat.id, message.message_id, delay=2)
    send_control_panel(message.chat.id)

def send_control_panel(chat_id):
    maintenance = is_maintenance()
    stats = get_stats()
    roles_text = "".join(f"  {r}: {cnt}\n" for r, cnt in stats.get("roles", {}).items())
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📋 Список пользователей",  callback_data="admin_users"),
        types.InlineKeyboardButton("💰 Последние платежи",     callback_data="admin_payments"),
        types.InlineKeyboardButton("🔑 Статус API ключей",     callback_data="admin_check_keys"),
        types.InlineKeyboardButton("🎫 Управление подписками", callback_data="admin_subs_help"),
        types.InlineKeyboardButton(
            "🔴 Вкл тех.работы" if not maintenance else "🟢 Выкл тех.работы",
            callback_data="admin_toggle_maintenance"),
    )
    bot.send_message(chat_id,
        f"🛠 Панель управления\n\n"
        f"👥 Всего: {stats['total_users']}\n"
        f"🆕 Новых сегодня: {stats['new_today']}\n"
        f"✅ Активных подписок: {stats['active_subs']}\n"
        f"🆓 Core: {stats['free_users']}\n"
        f"⭐ Nova+: {stats['pro_users']}\n"
        f"📱 Web users: {stats['miniapp_users']}\n"
        f"💰 Платежей: {stats['total_payments']}\n"
        f"💬 Сообщений: {stats['total_messages']}\n\n"
        f"Роли:\n{roles_text}"
        f"🔧 Тех.работы: {'ВКЛ 🔴' if maintenance else 'ВЫКЛ 🟢'}",
        reply_markup=markup)

# ── Медиа/документы ───────────────────────────────────────────────────────

@bot.message_handler(content_types=["photo","video","audio","voice"])
def handle_media(message):
    ensure_registered(message)
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user or user[4] == "none":
        show_start(message.chat.id, user_id); return
    handle_file_message(message, user_id, user[4])

@bot.message_handler(content_types=["document"])
def handle_document(message):
    ensure_registered(message)
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user or user[4] == "none":
        show_start(message.chat.id, user_id); return
    handle_file_message(message, user_id, user[4])

@bot.message_handler(content_types=["gift"])
def handle_gift(message):
    ensure_registered(message)
    user_id = message.from_user.id
    send_and_delete(message.chat.id,
        "Подарок получен! Администратор активирует подписку вручную.", delay=60)
    try:
        bot.send_message(OWNER_ID,
            f"Пользователь @{message.from_user.username or '?'} (ID: {user_id}) отправил подарок.")
    except:
        pass

# ── Inline callback ───────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass

    if call.data == "check_subs":
        not_subbed = check_subscriptions(user_id)
        if not_subbed:
            markup = types.InlineKeyboardMarkup(row_width=1)
            for ch in not_subbed:
                markup.add(types.InlineKeyboardButton(f"📢 {ch['title']}", url=ch["url"]))
            markup.add(types.InlineKeyboardButton("✅ Проверить снова", callback_data="check_subs"))
            send_and_delete(chat_id,
                "❌ Ещё не подписан на:\n" +
                "\n".join(f"— {ch['title']}" for ch in not_subbed) +
                "\n\nПодпишись и нажми проверить снова 👇",
                delay=120, reply_markup=markup)
        else:
            referred_by = None
            if user_id in user_states and "pending_ref" in user_states[user_id]:
                try: referred_by = int(user_states[user_id]["pending_ref"])
                except: pass
                del user_states[user_id]
            register_user(user_id, call.from_user.username or "", referred_by)
            if referred_by:
                try: bot.send_message(referred_by, "Кто-то перешёл по твоей реф. ссылке! +10 монет.")
                except: pass
            send_and_delete(chat_id, "✅ Подписка подтверждена!", delay=5,
                reply_markup=main_menu_keyboard(user_id))
            show_start(chat_id, user_id)
        return

    if call.data == "admin_users" and user_id == OWNER_ID:
        users = get_all_users()
        if not users:
            send_and_delete(chat_id, "Пользователей пока нет.", delay=20); return
        lines = [f"👥 Всего: {len(users)}\n"]
        for u in users:
            uname = f"@{u[1]}" if u[1] else f"ID:{u[0]}"
            lines.append(f"{uname} | {u[3] or '—'} | {u[5] if u[5]!='none' else '—'} | 🪙{u[7] if len(u)>7 else 0}")
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 3500:
                send_and_delete(chat_id, chunk, delay=120)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk:
            send_and_delete(chat_id, chunk, delay=120)
        return

    if call.data == "admin_payments" and user_id == OWNER_ID:
        payments = get_payments(20)
        if not payments:
            send_and_delete(chat_id, "Платежей пока нет.", delay=20); return
        text = "💰 Последние платежи:\n\n"
        for p in payments:
            text += f"@{p[0] or '?'} — {p[1]} / {p[2]} ({p[3]}₽) — {p[4]}\n"
        send_and_delete(chat_id, text, delay=60)
        return

    if call.data == "admin_check_keys" and user_id == OWNER_ID:
        send_and_delete(chat_id, "Проверяю ключи...", delay=5)
        try:
            status = check_keys_status()
            text = "🔑 Статус API ключей:\n\n"
            labels = {"core_flash_lite":"Core","nova_flash":"Nova","pro":"PRO","absolution":"Absolution"}
            for tier, keys in status.items():
                text += f"{labels.get(tier,tier)}:\n"
                for k in keys:
                    emoji = "✅" if k["status"]=="ok" else ("🔴" if k["status"] in ("exhausted","invalid") else "⚠️")
                    text += f"  {emoji} {k['key']} — {k['status']}\n"
                text += "\n"
        except Exception as e:
            text = f"Ошибка: {e}"
        send_and_delete(chat_id, text, delay=60)
        return

    if call.data == "admin_subs_help" and user_id == OWNER_ID:
        send_and_delete(chat_id,
            "🎫 Управление подписками:\n\n"
            "/give @user nova 30d\n"
            "/give @user nova forever\n"
            "/give @user remove sub\n"
            "/give @user role sponsor\n"
            "/give @user remove role\n"
            "/pay @user 100 — монеты\n\n"
            "Также доступно в веб-панели:\n"
            "https://elyon-ai-web.vercel.app/app.html",
            delay=60)
        return

    if call.data == "admin_toggle_maintenance" and user_id == OWNER_ID:
        new_val = "0" if is_maintenance() else "1"
        set_setting("maintenance", new_val)
        status = "ВКЛ 🔴" if new_val == "1" else "ВЫКЛ 🟢"
        markup = None
        if new_val == "1":
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🟢 Выключить", callback_data="admin_toggle_maintenance"))
        send_and_delete(chat_id, f"Тех.работы: {status}", delay=30, reply_markup=markup)
        return

    if call.data == "pay_gift_menu":
        markup = types.InlineKeyboardMarkup(row_width=1)
        for pk, pd in PRICES.items():
            markup.add(types.InlineKeyboardButton(
                f"🎁 {pk.upper()} — {pd['stars']} звёзд", callback_data=f"pay_gift_info_{pk}"))
        markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="choose_pro"))
        bot.send_message(chat_id,
            "🎁 Оплата подарком [TEST]\n\nОтправь боту подарок на нужную сумму.",
            reply_markup=markup)
        return

    if call.data.startswith("pay_gift_info_"):
        plan = call.data.replace("pay_gift_info_", "")
        price = PRICES.get(plan)
        if price:
            send_and_delete(chat_id,
                f"🎁 Тариф: {price['label']}\nОтправь {price['stars']} звёзд боту.", delay=60)
        return

    if call.data == "buy_beta_tester":
        bot.send_invoice(chat_id, title="Роль Beta-Tester",
            description="Получи роль Beta-Tester в Elyon AI",
            invoice_payload=f"beta_tester_{user_id}",
            provider_token="", currency="XTR",
            prices=[types.LabeledPrice("Beta-Tester", BETA_TESTER_STARS)])
        return

    if call.data == "pay_custom":
        user_states[user_id] = {"state": "waiting_custom_days"}
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="choose_pro"))
        bot.send_message(chat_id, "Введи количество дней (1–365):", reply_markup=markup)
        return

    if call.data == "back_start":
        show_start(chat_id, user_id)
    elif call.data == "choose_free":
        set_ai_model(user_id, "gpt"); clear_history(user_id)
        send_and_delete(chat_id, "🆓 Elyon Core активирован! Начинай общение.",
            delay=10, reply_markup=main_menu_keyboard(user_id))
    elif call.data == "choose_pro":
        if has_active_sub(user_id):
            set_ai_model(user_id, "gemini"); clear_history(user_id)
            send_and_delete(chat_id, "⭐ Elyon Nova активирован!",
                delay=10, reply_markup=main_menu_keyboard(user_id))
        else:
            show_payment_options(chat_id, user_id)
    elif call.data == "pay_virtual_menu":
        show_virtual_payment(chat_id, user_id)
    elif call.data.startswith("pay_virtual_"):
        plan = call.data.replace("pay_virtual_","")
        cost = VIRTUAL_PRICES[plan]["rub"]
        if spend_balance(user_id, cost):
            activate_subscription(user_id, plan, chat_id)
            log_payment(user_id, call.from_user.username or "", plan, "coins", str(cost))
        else:
            send_and_delete(chat_id, "Недостаточно монет.", delay=15)
    elif call.data.startswith("pay_stars_custom_"):
        days = int(call.data.replace("pay_stars_custom_",""))
        rub, stars, _ = calc_custom_price(days)
        bot.send_invoice(chat_id, title=f"Elyon — {days} дней",
            description=f"Доступ на {days} дней",
            invoice_payload=f"custom_{days}", provider_token="",
            currency="XTR", prices=[types.LabeledPrice(f"{days} дней", stars)])
    elif call.data.startswith("pay_crypto_custom_"):
        days = int(call.data.replace("pay_crypto_custom_",""))
        rub, _, usdt = calc_custom_price(days)
        invoice = create_crypto_invoice(usdt, user_id, f"custom_{days}")
        if invoice:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(f"Оплатить {rub} ₽", url=invoice["pay_url"]))
            markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_start"))
            bot.send_message(chat_id, f"CryptoBot: {rub} ₽\nПосле оплаты: /check",
                reply_markup=markup)
        else:
            send_and_delete(chat_id, "Ошибка создания платежа.", delay=15)
    elif call.data.startswith("pay_stars_"):
        plan = call.data.replace("pay_stars_","")
        price = PRICES[plan]
        bot.send_invoice(chat_id, title=f"Elyon — {price['label']}",
            description="Доступ к Elyon Nova",
            invoice_payload=f"pro_{plan}", provider_token="",
            currency="XTR", prices=[types.LabeledPrice(price["label"], price["stars"])])
    elif call.data.startswith("pay_crypto_"):
        plan = call.data.replace("pay_crypto_","")
        price = CRYPTO_PRICES[plan]
        invoice = create_crypto_invoice(price["amount"], user_id, plan)
        if invoice:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(f"Оплатить {price['label']}", url=invoice["pay_url"]))
            markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_start"))
            bot.send_message(chat_id, f"CryptoBot: {price['label']}\nПосле оплаты: /check",
                reply_markup=markup)
        else:
            send_and_delete(chat_id, "Ошибка создания платежа.", delay=15)

# ── Оплата ────────────────────────────────────────────────────────────────

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(query):
    bot.answer_pre_checkout_query(query.id, ok=True)

@bot.message_handler(content_types=["successful_payment"])
def payment_success(message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    if payload.startswith("beta_tester_"):
        set_role(user_id, "beta-tester")
        log_payment(user_id, message.from_user.username or "", "beta-tester", "stars", str(BETA_TESTER_STARS))
        bot.send_message(message.chat.id, "🔬 Роль Beta-Tester получена!",
            reply_markup=main_menu_keyboard(user_id))
        return
    if payload.startswith("custom_"):
        days = int(payload.replace("custom_",""))
        rub, stars, _ = calc_custom_price(days)
        activate_subscription(user_id, "custom", message.chat.id, days=days, label=f"{days} дней")
        log_payment(user_id, message.from_user.username or "", f"custom_{days}d", "stars", str(stars))
        return
    plan = payload.replace("pro_","")
    activate_subscription(user_id, plan, message.chat.id)
    log_payment(user_id, message.from_user.username or "", plan, "stars",
                str(PRICES.get(plan, {}).get("stars", "?")))

@bot.message_handler(commands=["check"])
def check_crypto_payment(message):
    delete_msg(message.chat.id, message.message_id, delay=2)
    user_id = message.from_user.id
    try:
        response = requests.get("https://pay.crypt.bot/api/getInvoices",
            headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN},
            params={"status": "paid"})
        data = response.json()
        if data["ok"]:
            for invoice in data["result"]["items"]:
                payload = invoice.get("payload", "")
                if payload.startswith(str(user_id) + "_"):
                    plan_part = payload.split("_", 1)[1]
                    if plan_part.startswith("custom_"):
                        days = int(plan_part.replace("custom_",""))
                        rub, _, usdt = calc_custom_price(days)
                        activate_subscription(user_id, "custom", message.chat.id, days=days, label=f"{days} дней")
                        log_payment(user_id, message.from_user.username or "", f"custom_{days}d", "crypto", str(usdt))
                    else:
                        activate_subscription(user_id, plan_part, message.chat.id)
                        log_payment(user_id, message.from_user.username or "", plan_part, "crypto",
                                    CRYPTO_PRICES.get(plan_part,{}).get("amount","?"))
                    return
        send_and_delete(message.chat.id, "Платёж не найден. Попробуй через минуту.", delay=30)
    except Exception as e:
        send_and_delete(message.chat.id, "Ошибка проверки платежа.", delay=20)

# ── Нижнее меню ───────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "💬 Chat with AI")
def menu_chat(message):
    delete_msg(message.chat.id, message.message_id, delay=2)
    user = get_user(message.from_user.id)
    if not user or user[4] == "none":
        show_start(message.chat.id, message.from_user.id); return
    model = "🆓 Elyon Core" if user[4] == "gpt" else "⭐ Elyon Nova"
    send_and_delete(message.chat.id, f"Модель: {model}\n\nНапиши сообщение!", delay=8)

@bot.message_handler(func=lambda m: m.text == "👤 Personal account")
def menu_profile(message):
    delete_msg(message.chat.id, message.message_id, delay=2)
    register_user(message.from_user.id, message.from_user.username or "")
    user = get_user(message.from_user.id)
    if not user: return
    user_id   = user[0]; sub_type = user[5]; sub_until = user[6]
    balance   = get_balance(user_id); ref_count = get_referral_count(user_id)
    ref_link  = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    if sub_type == "none": sub_info = "Нет подписки"
    elif sub_type == "forever": sub_info = "Навсегда"
    else:
        labels = {"month":"30 дней","halfyear":"6 месяцев","custom":"Custom"}
        sub_info = f"{labels.get(sub_type, sub_type)} (до {sub_until})"
    role_emoji = "👑" if user[3]=="owner" else ("🔬" if user[3]=="beta-tester" else "👤")
    markup = None
    if user[3] not in ("owner","beta-tester"):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔬 Beta-Tester за 50 ⭐", callback_data="buy_beta_tester"))
    # Профиль — показываем дольше (60 сек)
    send_and_delete(message.chat.id,
        f"Личный кабинет\n\n"
        f"@{user[1] or 'не указан'} | {role_emoji} {user[3]}\n"
        f"Подписка: {sub_info}\n"
        f"Монеты: {balance} 🪙\n"
        f"Рефералов: {ref_count}\n\n"
        f"Реф. ссылка: {ref_link}",
        delay=60, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🆓 Elyon Core")
def switch_free(message):
    delete_msg(message.chat.id, message.message_id, delay=2)
    register_user(message.from_user.id, message.from_user.username or "")
    set_ai_model(message.from_user.id, "gpt")
    clear_history(message.from_user.id)
    send_and_delete(message.chat.id, "🆓 Elyon Core активирован!", delay=8)

@bot.message_handler(func=lambda m: m.text == "⭐ Elyon Nova")
def switch_pro(message):
    delete_msg(message.chat.id, message.message_id, delay=2)
    register_user(message.from_user.id, message.from_user.username or "")
    user_id = message.from_user.id
    if has_active_sub(user_id):
        set_ai_model(user_id, "gemini"); clear_history(user_id)
        send_and_delete(message.chat.id, "⭐ Elyon Nova активирован!", delay=8)
    else:
        show_payment_options(message.chat.id, user_id)

# ── AI сообщения ──────────────────────────────────────────────────────────

MENU_TEXTS = {"💬 Chat with AI","👤 Personal account","🆓 Elyon Core","⭐ Elyon Nova","🛠 Control Panel"}

def ensure_registered(message):
    register_user(message.from_user.id, message.from_user.username or "")

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    ensure_registered(message)
    user_id = message.from_user.id

    if not is_privileged(user_id) and not is_subscribed(user_id):
        delete_msg(message.chat.id, message.message_id, delay=2)
        send_subscribe_prompt(message.chat.id)
        return

    if user_id in user_states and user_states[user_id].get("state") == "waiting_custom_days":
        delete_msg(message.chat.id, message.message_id, delay=2)
        try:
            days = int(message.text.strip())
            if days < 1 or days > 365: raise ValueError
        except ValueError:
            send_and_delete(message.chat.id, "Введи число от 1 до 365.", delay=15)
            return
        del user_states[user_id]
        rub, stars, usdt = calc_custom_price(days)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"⭐ {stars} звёзд", callback_data=f"pay_stars_custom_{days}"),
            types.InlineKeyboardButton(f"💳 {rub} ₽ CryptoBot", callback_data=f"pay_crypto_custom_{days}"),
            types.InlineKeyboardButton("◀️ Назад", callback_data="back_start"),
        )
        bot.send_message(message.chat.id,
            f"{days} дней подписки\nЗвёзды: {stars}\nРубли: {rub} ₽",
            reply_markup=markup)
        return

    if message.text and message.text in MENU_TEXTS:
        return

    if is_maintenance() and not is_privileged(user_id):
        send_and_delete(message.chat.id, "🔧 Технические работы. Скоро вернёмся!", delay=30)
        return

    user = get_user(user_id)
    if not user:
        show_start(message.chat.id, user_id); return

    ai_model = user[4]
    if ai_model == "none":
        show_start(message.chat.id, user_id); return

    if ai_model == "gemini" and not has_active_sub(user_id):
        send_and_delete(message.chat.id, "⚠️ Подписка истекла.", delay=10)
        show_payment_options(message.chat.id, user_id)
        return

    is_pro = (ai_model == "gemini")
    allowed, current, limit = check_daily_limit(user_id, is_pro)
    if not allowed and not (user_id == OWNER_ID and not owner_test_mode):
        model_name = "Elyon Nova" if is_pro else "Elyon Core"
        send_and_delete(message.chat.id,
            f"⏳ Дневной лимит {model_name}: {limit}/{limit}.\n"
            f"Сброс в 00:00 МСК.", delay=30)
        return

    bot.send_chat_action(message.chat.id, "typing")
    add_message(user_id, "user", message.text)
    log_session_activity(user_id, "bot")
    history = get_history(user_id)

    try:
        reply = ask_gpt(history) if ai_model == "gpt" else ask_gemini(history)
        increment_daily_count(user_id)
        add_message(user_id, "assistant", reply)
        bot.send_message(message.chat.id, reply)  # AI ответ — НЕ удаляем

    except Exception as e:
        err = str(e)
        print("AI error:", e)
        if "429" in err or "RESOURCE_EXHAUSTED" in err:
            send_and_delete(message.chat.id, "⏳ Слишком много запросов. Попробуй через минуту.", delay=30)
        elif "404" in err or "NOT_FOUND" in err:
            send_and_delete(message.chat.id, "❌ Модель недоступна. Напишите администратору.", delay=30)
        else:
            send_and_delete(message.chat.id, f"❌ Ошибка: {err[:150]}", delay=30)

# ══════════════════════════════════════════════════════════════════
# Flask API
# ══════════════════════════════════════════════════════════════════

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

@app.after_request
def after_request(response):
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization,X-Admin-Id")
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
        if not user_id or not messages:
            return jsonify({"error": "Missing user_id or messages"}), 400
        if is_maintenance() and not (user_id == OWNER_ID and not owner_test_mode):
            return jsonify({"error": "Maintenance in progress"}), 503
        if model in ("gemini", "nova"):
            if not has_active_sub(user_id): return jsonify({"error": "No active subscription"}), 403
            reply = ask_nova(messages)
        elif model == "pro":
            if not has_active_sub(user_id): return jsonify({"error": "No active subscription"}), 403
            reply = ask_pro(messages)
        elif model == "absolution":
            if not has_active_sub(user_id): return jsonify({"error": "No active subscription"}), 403
            reply = ask_absolution(messages)
        else:
            reply = ask_gpt(messages)

        if not (user_id == OWNER_ID and not owner_test_mode):
            is_pro = (model != "gpt" and model != "core")
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
    if request.method == "OPTIONS": return "", 204
    user = get_user(user_id)
    if not user: return jsonify({"error": "User not found"}), 404
    return jsonify({
        "user_id": user[0], "username": user[1], "role": user[3],
        "ai_model": user[4], "sub_type": user[5], "sub_until": user[6],
        "balance": get_balance(user_id), "referrals": get_referral_count(user_id),
        "has_sub": has_active_sub(user_id),
        "ref_link": f"https://t.me/{BOT_USERNAME}?start={user_id}",
        "purchase_history": [list(p) for p in get_user_purchase_history(user_id)],
    })

@app.route("/api/chats/<int:user_id>", methods=["GET", "OPTIONS"])
def api_get_chats(user_id):
    if request.method == "OPTIONS": return "", 204
    return jsonify({"chats": get_mini_app_chats(user_id)})

@app.route("/api/chats/<int:user_id>", methods=["POST", "OPTIONS"])
def api_save_chat(user_id):
    if request.method == "OPTIONS": return "", 204
    try:
        data = request.json
        chat_id = data.get("chat_id")
        if not chat_id: return jsonify({"error": "Missing chat_id"}), 400
        save_mini_app_chat(user_id, chat_id, data.get("title","New chat"),
                           data.get("model","gpt"), data.get("messages",[]))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/chats/<int:user_id>/<chat_id>", methods=["DELETE", "OPTIONS"])
def api_delete_chat(user_id, chat_id):
    if request.method == "OPTIONS": return "", 204
    delete_mini_app_chat(user_id, chat_id)
    return jsonify({"ok": True})

@app.route("/api/file_b64", methods=["POST", "OPTIONS"])
def api_file_b64():
    if request.method == "OPTIONS": return "", 204
    try:
        import base64 as b64
        data      = request.json
        user_id   = data.get("user_id")
        model     = data.get("model", "gpt")
        prompt    = data.get("prompt", "Проанализируй этот файл")
        file_name = data.get("file_name", "file")
        file_type = data.get("file_type", "application/octet-stream")
        file_b64  = data.get("file_data", "")
        history   = data.get("history", [])
        if not user_id: return jsonify({"error": "Missing user_id"}), 400
        user_id = int(user_id)
        if is_maintenance() and not (user_id == OWNER_ID and not owner_test_mode):
            return jsonify({"error": "Maintenance in progress"}), 503
        if model == "gemini" and not has_active_sub(user_id):
            return jsonify({"error": "No active subscription"}), 403
        file_bytes = b64.b64decode(file_b64)
        mime_type  = file_type
        if not mime_type or mime_type == "application/octet-stream":
            ext = os.path.splitext(file_name.lower())[1]
            mime_map = {".py":"text/x-python",".js":"text/javascript",".cpp":"text/x-c++",
                        ".c":"text/x-c",".java":"text/x-java",".txt":"text/plain",
                        ".md":"text/plain",".csv":"text/csv",".json":"application/json",
                        ".html":"text/html",".css":"text/css",".sql":"text/plain",
                        ".gif":"image/gif",".png":"image/png",".jpg":"image/jpeg",
                        ".jpeg":"image/jpeg",".webp":"image/webp",".pdf":"application/pdf"}
            mime_type = mime_map.get(ext, "text/plain")
        tier_map   = {"gpt":"core","core":"core","nova":"nova","gemini":"nova","pro":"pro","absolution":"absolution"}
        use_pro    = model in ("gemini","nova","pro","absolution")
        model_tier = tier_map.get(model, "core")
        reply = ask_with_file(file_bytes, mime_type, file_name, prompt,
                              history, use_pro=use_pro, model_tier=model_tier)
        log_session_activity(user_id, "miniapp")
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/file", methods=["POST", "OPTIONS"])
def api_file():
    if request.method == "OPTIONS": return "", 204
    try:
        import json as json_lib
        user_id     = request.form.get("user_id")
        model       = request.form.get("model", "gpt")
        prompt      = request.form.get("prompt", "Проанализируй этот файл")
        history     = json_lib.loads(request.form.get("history","[]"))
        if not user_id: return jsonify({"error": "Missing user_id"}), 400
        user_id = int(user_id)
        if "file" not in request.files: return jsonify({"error": "No file"}), 400
        f          = request.files["file"]
        file_bytes = f.read(); file_name = f.filename or "file"
        mime_type  = f.content_type or "application/octet-stream"
        if mime_type == "application/octet-stream":
            ext = os.path.splitext(file_name.lower())[1]
            mime_map = {".py":"text/x-python",".js":"text/javascript",".txt":"text/plain",
                        ".md":"text/plain",".csv":"text/csv",".json":"application/json"}
            mime_type = mime_map.get(ext, "text/plain")
        tier_map   = {"gpt":"core","core":"core","nova":"nova","gemini":"nova","pro":"pro","absolution":"absolution"}
        model_tier = tier_map.get(model, "core")
        reply = ask_with_file(file_bytes, mime_type, file_name, prompt,
                              history, use_pro=(model!="gpt"), model_tier=model_tier)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Auth ──────────────────────────────────────────────────────────────────

import hashlib, hmac, time
import json as _json

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID",
    "468899724697-mct44qubsrdaps8ll6m4npv34k6jeucn.apps.googleusercontent.com")

def _upsert_web_user(email, first_name, last_name, avatar="", provider="email"):
    import database as _db
    _db.cursor.execute("SELECT user_id FROM users WHERE username = ?", (email,))
    row = _db.cursor.fetchone()
    if row:
        user_id = row[0]
    else:
        user_id = abs(hash(email + provider)) % (10**12)
        _db.cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, joined_at, role) VALUES (?, ?, ?, ?)",
            (user_id, email, datetime.now().strftime("%d.%m.%Y %H:%M"), "default user"))
        _db.conn.commit()
    return {"user_id":user_id,"email":email,"first_name":first_name,
            "last_name":last_name,"avatar":avatar,"provider":provider}

@app.route("/api/auth/google_profile", methods=["POST","OPTIONS"])
def auth_google_profile():
    if request.method == "OPTIONS": return "", 204
    try:
        profile = (request.json or {}).get("profile", {})
        email = profile.get("email","")
        if not email: return jsonify({"ok":False,"error":"No email"}), 400
        user = _upsert_web_user(email, profile.get("given_name",""),
                                profile.get("family_name",""), profile.get("picture",""), "google")
        return jsonify({"ok":True,"user":user})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/auth/email", methods=["POST","OPTIONS"])
def auth_email():
    if request.method == "OPTIONS": return "", 204
    try:
        import hashlib as _hl, database as _db
        data     = request.json or {}
        action   = data.get("action","signin")
        email    = data.get("email","").lower().strip()
        password = data.get("password","")
        if not email or not password:
            return jsonify({"ok":False,"error":"Missing email or password"}), 400
        pw_hash = _hl.sha256(password.encode()).hexdigest()
        if action == "signup":
            _db.cursor.execute("SELECT user_id FROM users WHERE username=?", (email,))
            if _db.cursor.fetchone():
                return jsonify({"ok":False,"error":"Email already registered"}), 409
            user_id = abs(hash(email + pw_hash)) % (10**12)
            _db.cursor.execute(
                "INSERT OR IGNORE INTO users (user_id,username,joined_at,role) VALUES (?,?,?,?)",
                (user_id, email, datetime.now().strftime("%d.%m.%Y %H:%M"), "default user"))
            _db.cursor.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (f"pw_{email}", pw_hash))
            _db.conn.commit()
            user = _upsert_web_user(email, data.get("first_name",""), data.get("last_name",""), "", "email")
            return jsonify({"ok":True,"verify":False,"user":user})
        else:
            stored = get_setting(f"pw_{email}")
            if not stored or stored != pw_hash:
                return jsonify({"ok":False,"error":"Invalid email or password"}), 401
            user = _upsert_web_user(email, "", "", "", "email")
            return jsonify({"ok":True,"user":user})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/auth/telegram_token", methods=["POST","OPTIONS"])
def auth_telegram_token():
    if request.method == "OPTIONS": return "", 204
    try:
        data  = request.json or {}
        token = data.get("token","")
        if not token: return jsonify({"ok":False,"error":"Missing token"}), 400
        info = _auth_tokens.get(token)
        if not info: return jsonify({"ok":False,"error":"Invalid token"}), 401
        if datetime.now().timestamp() > info["expires"]:
            _auth_tokens.pop(token, None)
            return jsonify({"ok":False,"error":"Token expired"}), 401
        _auth_tokens.pop(token, None)
        user_id  = info["user_id"]; username = info["username"]
        register_user(user_id, username)
        user     = get_user(user_id)
        sub_type = user[5] if user else "none"
        web_user = {
            "user_id": user_id,
            "email":   f"{username}@telegram" if username else f"tg_{user_id}@telegram",
            "first_name": info["first_name"], "last_name": "",
            "avatar": "", "provider": "telegram",
            "username": username, "sub_type": sub_type,
        }
        return jsonify({"ok":True,"user":web_user})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/admin/stats", methods=["GET","OPTIONS"])
def api_admin_stats():
    if request.method == "OPTIONS": return "", 204
    return jsonify(get_stats())

@app.route("/api/admin/users", methods=["GET","OPTIONS"])
def api_admin_users():
    if request.method == "OPTIONS": return "", 204
    users = get_all_users()
    return jsonify({"users": [{"user_id":u[0],"username":u[1] or "","role":u[3] or "default user",
        "sub_type":u[5] or "none","balance":u[7] if len(u)>7 else 0} for u in users]})

@app.route("/api/bot_info", methods=["GET"])
def api_bot_info():
    try:
        info = bot.get_me()
        return jsonify({"bot_id": info.id, "username": info.username})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════
# Admin API — /give sub и /remove sub из веб-панели (задача 2 и 4)
# ══════════════════════════════════════════════════════════════════

def _check_admin_auth():
    """Проверяет X-Admin-Id заголовок."""
    try:
        return int(request.headers.get("X-Admin-Id", "")) == OWNER_ID
    except:
        return False

@app.route("/api/admin/give_sub", methods=["POST","OPTIONS"])
def api_admin_give_sub():
    """
    Выдать подписку из веб-панели.
    Headers: X-Admin-Id: OWNER_ID
    Body: {"target":"@username","tier":"nova|pro|absolution","days":30}
    """
    if request.method == "OPTIONS": return "", 204
    if not _check_admin_auth():
        return jsonify({"ok":False,"error":"Unauthorized"}), 403
    try:
        data   = request.json or {}
        target = data.get("target","").strip().lstrip("@")
        tier   = data.get("tier","nova").lower()
        days   = int(data.get("days", 30))
        if tier not in ("nova","pro","absolution"):
            return jsonify({"ok":False,"error":"Unknown tier"}), 400
        user = get_user_by_username(target)
        if not user:
            return jsonify({"ok":False,"error":f"User @{target} not found"}), 404
        user_id   = user[0]
        until_str = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y %H:%M")
        set_subscription(user_id, tier, until_str)
        set_ai_model(user_id, "gemini")
        log_admin_grant(user_id, "subscription", tier, until_str, OWNER_ID)
        try:
            bot.send_message(user_id,
                f"✅ Подписка {tier.upper()} на {days} дней активирована (до {until_str}).")
        except:
            pass
        return jsonify({"ok":True,"user_id":user_id,"tier":tier,"until":until_str})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/admin/remove_sub", methods=["POST","OPTIONS"])
def api_admin_remove_sub():
    """
    Снять подписку из веб-панели.
    Headers: X-Admin-Id: OWNER_ID
    Body: {"target":"@username"}
    """
    if request.method == "OPTIONS": return "", 204
    if not _check_admin_auth():
        return jsonify({"ok":False,"error":"Unauthorized"}), 403
    try:
        target = (request.json or {}).get("target","").strip().lstrip("@")
        user = get_user_by_username(target)
        if not user:
            return jsonify({"ok":False,"error":f"User @{target} not found"}), 404
        user_id = user[0]
        remove_subscription(user_id)
        try:
            bot.send_message(user_id, "ℹ️ Твоя подписка деактивирована администратором.")
        except:
            pass
        return jsonify({"ok":True,"user_id":user_id})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/admin/test_keys", methods=["GET","OPTIONS"])
def api_admin_test_keys():
    """Проверить API ключи из веб-панели."""
    if request.method == "OPTIONS": return "", 204
    if not _check_admin_auth():
        return jsonify({"ok":False,"error":"Unauthorized"}), 403
    try:
        status = check_keys_status()
        return jsonify({"ok":True,"keys":status})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

# ══════════════════════════════════════════════════════════════════
# DonatePay Webhook
# ══════════════════════════════════════════════════════════════════
import hmac as _hmac
import hashlib as _hashlib

DONATEPAY_SECRET = os.environ.get("DONATEPAY_SECRET", "")

_DP_TIER_MAP = {
    "91":"nova","91.00":"nova",
    "182":"pro","182.00":"pro",
    "265":"absolution","265.00":"absolution",
}

@app.route("/api/donatepay_webhook", methods=["POST","OPTIONS"])
def donatepay_webhook():
    if request.method == "OPTIONS": return "", 204
    try:
        data = request.json or {}
        if data.get("notification_type") != "donation":
            return jsonify({"ok":True,"skip":True})

        if DONATEPAY_SECRET:
            sig = request.headers.get("X-DonatePay-Signature","")
            raw = request.get_data(as_text=True)
            exp = _hmac.new(DONATEPAY_SECRET.encode(), raw.encode(), _hashlib.sha256).hexdigest()
            if not _hmac.compare_digest(sig, exp):
                return jsonify({"ok":False,"error":"Bad signature"}), 403

        amount_raw  = str(data.get("sum","0")).split(".")[0]
        dp_username = data.get("username","unknown")
        comment     = (data.get("comment") or "").strip()
        vars_data   = data.get("vars") or {}

        # Определяем user_id тремя способами
        user_id = None
        if comment and comment.isdigit():
            user_id = int(comment)
        if not user_id:
            uid_raw = vars_data.get("user_id","")
            if str(uid_raw).isdigit():
                user_id = int(uid_raw)
        if not user_id and comment:
            found = get_user_by_username(comment.lstrip("@").lower())
            if found:
                user_id = found[0]

        if not user_id:
            try:
                bot.send_message(OWNER_ID,
                    f"💰 DonatePay: {amount_raw}₽ от {dp_username}\n"
                    f"Комментарий: {comment!r}\n\n"
                    f"⚠️ user_id не определён — активируй вручную:\n"
                    f"/give @username nova 30d")
            except:
                pass
            return jsonify({"ok":True,"manual":True})

        tier = _DP_TIER_MAP.get(amount_raw)
        if not tier:
            return jsonify({"ok":True,"skip":"unknown_amount"})

        tier_labels = {"nova":"Elyon Nova — 91 ₽","pro":"Elyon PRO — 182 ₽",
                       "absolution":"Elyon Absolution — 265 ₽"}

        until_str = (datetime.now() + timedelta(days=30)).strftime("%d.%m.%Y %H:%M")
        set_subscription(user_id, tier, until_str)
        set_ai_model(user_id, "gemini")
        log_payment(user_id, dp_username, tier, "donatepay", amount_raw)

        try:
            bot.send_message(user_id,
                f"✅ Оплата получена!\n\n"
                f"Подписка {tier_labels[tier]} активирована на 30 дней.\n"
                f"Используй /start чтобы начать.")
        except Exception as e:
            print(f"DonatePay notify error: {e}")

        try:
            bot.send_message(OWNER_ID,
                f"💰 DonatePay!\nПользователь: {user_id} (@{dp_username})\n"
                f"Тариф: {tier_labels[tier]}\nДо: {until_str}")
        except:
            pass

        return jsonify({"ok":True,"tier":tier,"user_id":user_id})
    except Exception as e:
        print(f"DonatePay webhook error: {e}")
        return jsonify({"ok":False,"error":str(e)}), 500

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
        except Exception as e:
            print(f"keep_alive error: {e}")

def start_polling():
    import time as _time
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
                print(f"409 Conflict. Retry in {wait}s...")
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
