import os
import telebot
from telebot import types
from datetime import datetime, timedelta
import logging
import requests
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS

from database import (init_db, get_user, register_user, set_ai_model,
                      set_subscription, has_active_sub, add_message,
                      get_history, clear_history, get_balance, spend_balance,
                      add_balance, get_referral_count, get_all_users,
                      get_stats, get_payments, log_payment,
                      is_maintenance, set_setting, get_setting)
from ai_clients import ask_gpt, ask_gemini

logging.basicConfig(level=logging.CRITICAL)

TOKEN        = os.environ.get("BOT_TOKEN", "")
OWNER_ID     = int(os.environ.get("OWNER_ID", "7113603197"))
CRYPTO_TOKEN = os.environ.get("CRYPTO_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Elyon_by_unkony_bot")

import database
database.OWNER_ID = OWNER_ID

bot = telebot.TeleBot(TOKEN, threaded=False)
init_db()

# Состояния для custom days
user_states = {}  # user_id: {"state": ..., "data": ...}

# ── Цены ──────────────────────────────────────────────────────────────────

PRICES = {
    "month":    {"stars": 30,  "label": "30 days — 30 ⭐",  "days": 30,  "rub": 50},
    "halfyear": {"stars": 60,  "label": "6 months — 60 ⭐", "days": 180, "rub": 182},
    "forever":  {"stars": 120, "label": "Forever — 120 ⭐", "days": 0,   "rub": 429},
}

CRYPTO_PRICES = {
    "month":    {"amount": "0.55", "label": "30 days — 50 ₽"},
    "halfyear": {"amount": "1.10", "label": "6 months — 182 ₽"},
    "forever":  {"amount": "2.20", "label": "Forever — 429 ₽"},
}

VIRTUAL_PRICES = {
    "month":    {"rub": 50,  "label": "30 days — 50 монет"},
    "halfyear": {"rub": 182, "label": "6 months — 182 монеты"},
    "forever":  {"rub": 429, "label": "Forever — 429 монет"},
}

STARS_PER_RUB = 1 / 1.82  # 1 рубль = ~0.549 звезды
RUB_PER_DAY   = 50 / 30   # ~1.67 руб/день


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
        "👋 *Welcome to Elyon AI!*\n\n"
        "🆓 *Elyon Core* — free, fast answers\n"
        "⭐ *Elyon Nova* — pro, deep thinking\n\n"
        "Choose your version:",
        parse_mode="Markdown",
        reply_markup=markup
    )


def show_payment_options(chat_id, user_id):
    balance = get_balance(user_id)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⭐ 30 days — 30 stars",   callback_data="pay_stars_month"),
        types.InlineKeyboardButton("💳 30 days — 50 ₽",       callback_data="pay_crypto_month"),
        types.InlineKeyboardButton("⭐ 6 months — 60 stars",  callback_data="pay_stars_halfyear"),
        types.InlineKeyboardButton("💳 6 months — 182 ₽",     callback_data="pay_crypto_halfyear"),
        types.InlineKeyboardButton("⭐ Forever — 120 stars",  callback_data="pay_stars_forever"),
        types.InlineKeyboardButton("💳 Forever — 429 ₽",      callback_data="pay_crypto_forever"),
        types.InlineKeyboardButton("📅 Custom days",          callback_data="pay_custom"),
    )
    if balance >= 50:
        markup.add(types.InlineKeyboardButton(
            f"🪙 Pay with coins (balance: {balance})", callback_data="pay_virtual_menu"
        ))
    markup.add(types.InlineKeyboardButton("◀️ Back", callback_data="back_start"))
    bot.send_message(
        chat_id,
        "⭐ *Elyon Nova — Subscription*\n\n"
        "⭐ Telegram Stars\n"
        "💳 CryptoBot (₽/USDT)\n"
        "📅 Custom number of days\n"
        + (f"🪙 Virtual coins (balance: *{balance}*)\n" if balance >= 50 else "") +
        "\nChoose plan and payment method:",
        parse_mode="Markdown",
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
    markup.add(types.InlineKeyboardButton("◀️ Back", callback_data="choose_pro"))
    bot.send_message(
        chat_id,
        f"🪙 *Pay with virtual coins*\n\nYour balance: *{balance}* coins\n\nChoose plan:",
        parse_mode="Markdown",
        reply_markup=markup
    )


def calc_custom_price(days):
    rub   = round(RUB_PER_DAY * days, 2)
    stars = max(1, round(rub * STARS_PER_RUB))
    usdt  = round(rub * 0.011, 2)  # примерный курс
    return rub, stars, usdt


def activate_subscription(user_id, plan, chat_id, days=None, label=None):
    if plan == "forever":
        set_subscription(user_id, "forever", "none")
        price_label = label or PRICES["forever"]["label"]
    elif plan == "custom" and days:
        until = datetime.now() + timedelta(days=days)
        set_subscription(user_id, "custom", until.strftime("%d.%m.%Y %H:%M"))
        price_label = label or f"{days} days"
    else:
        price_label = label or PRICES[plan]["label"]
        until = datetime.now() + timedelta(days=PRICES[plan]["days"])
        set_subscription(user_id, plan, until.strftime("%d.%m.%Y %H:%M"))

    set_ai_model(user_id, "gemini")
    clear_history(user_id)
    bot.send_message(
        chat_id,
        f"✅ *Subscription activated!*\nPlan: *{price_label}*\n\nYou are now using *Elyon Nova* 🌟",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(user_id)
    )
    try:
        uname = bot.get_chat(user_id).username or "no username"
        bot.send_message(OWNER_ID, f"💰 New subscription!\nUser: @{uname}\nID: {user_id}\nPlan: {price_label}")
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


# ── /start ────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def start(message):
    args = message.text.split()
    referred_by = None
    if len(args) > 1:
        try:
            referred_by = int(args[1])
        except:
            pass
    is_new = register_user(message.from_user.id, message.from_user.username or "", referred_by)
    if is_new and referred_by:
        try:
            bot.send_message(referred_by, "🎉 Someone joined via your referral link! +10 coins added.")
        except:
            pass
    bot.send_message(message.chat.id, "🔄 Loading...", reply_markup=main_menu_keyboard(message.from_user.id))
    show_start(message.chat.id, message.from_user.id)


# ── Панель управления (только owner) ──────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🛠 Control Panel" and m.from_user.id == OWNER_ID)
def control_panel(message):
    maintenance = is_maintenance()
    stats = get_stats()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📊 Full user list", callback_data="admin_users"),
        types.InlineKeyboardButton("💰 Recent payments", callback_data="admin_payments"),
        types.InlineKeyboardButton(
            "🔴 Disable bot for users" if not maintenance else "🟢 Enable bot for users",
            callback_data="admin_toggle_maintenance"
        )
    )
    bot.send_message(
        message.chat.id,
        f"🛠 *Control Panel*\n\n"
        f"👥 Total users: *{stats['total_users']}*\n"
        f"💎 Subscribers: *{stats['subscribers']}*\n"
        f"🆓 Using Core: *{stats['free_users']}*\n"
        f"⭐ Using Nova: *{stats['pro_users']}*\n"
        f"💰 Total payments: *{stats['total_payments']}*\n"
        f"💬 Total messages: *{stats['total_messages']}*\n\n"
        f"🔧 Maintenance mode: *{'ON 🔴' if maintenance else 'OFF 🟢'}*",
        parse_mode="Markdown",
        reply_markup=markup
    )


# ── Inline кнопки ─────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass

    # ── Admin actions ──
    if call.data == "admin_users" and user_id == OWNER_ID:
        users = get_all_users()
        if not users:
            bot.send_message(chat_id, "No users yet.")
            return
        text = "👥 *All users:*\n\n"
        for u in users[:30]:
            sub = u[5] if u[5] != "none" else "—"
            text += f"@{u[1] or '?'} | {u[2][:10]} | sub: {sub}\n"
        if len(users) > 30:
            text += f"\n...and {len(users)-30} more"
        bot.send_message(chat_id, text, parse_mode="Markdown")
        return

    if call.data == "admin_payments" and user_id == OWNER_ID:
        payments = get_payments(20)
        if not payments:
            bot.send_message(chat_id, "No payments yet.")
            return
        text = "💰 *Recent payments:*\n\n"
        for p in payments:
            text += f"@{p[0] or '?'} — {p[1]} via {p[2]} ({p[3]}₽) — {p[4]}\n"
        bot.send_message(chat_id, text, parse_mode="Markdown")
        return

    if call.data == "admin_toggle_maintenance" and user_id == OWNER_ID:
        new_val = "0" if is_maintenance() else "1"
        set_setting("maintenance", new_val)
        status = "🔴 ON" if new_val == "1" else "🟢 OFF"
        bot.send_message(chat_id, f"🔧 Maintenance mode: *{status}*", parse_mode="Markdown")
        return

    # ── Custom days ──
    if call.data == "pay_custom":
        user_states[user_id] = {"state": "waiting_custom_days"}
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("◀️ Back", callback_data="choose_pro"))
        bot.send_message(
            chat_id,
            "📅 *Custom subscription*\n\nEnter the number of days (1–365):",
            parse_mode="Markdown",
            reply_markup=markup
        )
        return

    if call.data == "back_start":
        show_start(chat_id, user_id)

    elif call.data == "choose_free":
        set_ai_model(user_id, "gpt")
        clear_history(user_id)
        bot.send_message(chat_id, "✅ *Elyon Core* activated!\n\nFast answers, always free.",
                         parse_mode="Markdown", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "choose_pro":
        if has_active_sub(user_id):
            set_ai_model(user_id, "gemini")
            clear_history(user_id)
            bot.send_message(chat_id, "✅ *Elyon Nova* activated!\n\nDeep thinking mode.",
                             parse_mode="Markdown", reply_markup=main_menu_keyboard(user_id))
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
            bot.send_message(chat_id, "❌ Not enough coins.")

    elif call.data.startswith("pay_stars_custom_"):
        days = int(call.data.replace("pay_stars_custom_", ""))
        rub, stars, _ = calc_custom_price(days)
        label = f"{days} days — {stars} ⭐"
        bot.send_invoice(
            chat_id,
            title=f"Elyon Nova — {days} days",
            description=f"Access to Elyon Nova for {days} days",
            invoice_payload=f"custom_{days}",
            provider_token="",
            currency="XTR",
            prices=[types.LabeledPrice(label, stars)]
        )

    elif call.data.startswith("pay_crypto_custom_"):
        days = int(call.data.replace("pay_crypto_custom_", ""))
        rub, _, usdt = calc_custom_price(days)
        label = f"{days} days — {rub} ₽"
        invoice = create_crypto_invoice(usdt, user_id, f"custom_{days}", label)
        if invoice:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(f"💳 Pay {label}", url=invoice["pay_url"]))
            markup.add(types.InlineKeyboardButton("◀️ Back", callback_data="back_start"))
            bot.send_message(chat_id, f"💳 *Payment via CryptoBot*\n*{label}*\n\nAfter payment press /check",
                             parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(chat_id, "❌ Payment error. Try again later.")

    elif call.data.startswith("pay_stars_"):
        plan = call.data.replace("pay_stars_", "")
        price = PRICES[plan]
        bot.send_invoice(
            chat_id,
            title=f"Elyon Nova — {price['label']}",
            description="Access to Elyon Nova (deep thinking AI)",
            invoice_payload=f"pro_{plan}",
            provider_token="",
            currency="XTR",
            prices=[types.LabeledPrice(price["label"], price["stars"])]
        )

    elif call.data.startswith("pay_crypto_"):
        plan = call.data.replace("pay_crypto_", "")
        price = CRYPTO_PRICES[plan]
        invoice = create_crypto_invoice(price["amount"], user_id, plan)
        if invoice:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(f"💳 Pay {price['label']}", url=invoice["pay_url"]))
            markup.add(types.InlineKeyboardButton("◀️ Back", callback_data="back_start"))
            bot.send_message(chat_id, f"💳 *Payment via CryptoBot*\n*{price['label']}*\n\nAfter payment press /check",
                             parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(chat_id, "❌ Payment error. Try again later.")


# ── Оплата звёздами ───────────────────────────────────────────────────────

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(query):
    bot.answer_pre_checkout_query(query.id, ok=True)


@bot.message_handler(content_types=["successful_payment"])
def payment_success(message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    if payload.startswith("custom_"):
        days = int(payload.replace("custom_", ""))
        rub, stars, _ = calc_custom_price(days)
        activate_subscription(user_id, "custom", message.chat.id, days=days, label=f"{days} days")
        log_payment(user_id, message.from_user.username or "", f"custom_{days}d", "stars", str(stars))
    else:
        plan = payload.replace("pro_", "")
        activate_subscription(user_id, plan, message.chat.id)
        log_payment(user_id, message.from_user.username or "", plan, "stars", str(PRICES[plan]["stars"]))


# ── Проверка CryptoBot ────────────────────────────────────────────────────

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
                        activate_subscription(user_id, "custom", message.chat.id, days=days, label=f"{days} days")
                        log_payment(user_id, message.from_user.username or "", f"custom_{days}d", "crypto", str(usdt))
                    else:
                        activate_subscription(user_id, plan_part, message.chat.id)
                        log_payment(user_id, message.from_user.username or "", plan_part, "crypto",
                                    CRYPTO_PRICES.get(plan_part, {}).get("amount", "?"))
                    return
        bot.send_message(message.chat.id, "❌ Payment not found. Try again in a minute.")
    except Exception as e:
        print("Check error:", e)
        bot.send_message(message.chat.id, "❌ Payment verification error.")


# ── Нижнее меню ───────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "💬 Chat with AI")
def menu_chat(message):
    user = get_user(message.from_user.id)
    if not user or user[4] == "none":
        show_start(message.chat.id, message.from_user.id)
        return
    model = "🆓 Elyon Core" if user[4] == "gpt" else "⭐ Elyon Nova"
    bot.send_message(message.chat.id, f"Current model: *{model}*\n\nWrite your message!", parse_mode="Markdown")


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
        sub_info = "❌ No subscription"
    elif sub_type == "forever":
        sub_info = "♾️ Forever"
    else:
        labels = {"month": "30 days", "halfyear": "6 months", "custom": "Custom"}
        sub_info = f"✅ {labels.get(sub_type, sub_type)} (until {sub_until})"

    role_emoji = "👑" if user[3] == "owner" else "👤"
    bot.send_message(
        message.chat.id,
        f"👤 Personal Account\n\n"
        f"Username: @{user[1] or 'not specified'}\n"
        f"Registered: {user[2]}\n"
        f"Role: {role_emoji} {user[3]}\n"
        f"Subscription: {sub_info}\n\n"
        f"🪙 Coins balance: {balance}\n"
        f"👥 Referrals: {ref_count}\n\n"
        f"🔗 Your referral link:\n{ref_link}\n\n"
        f"Each friend who joins gives you +10 coins"
    )


@bot.message_handler(func=lambda m: m.text == "🆓 Elyon Core")
def switch_free(message):
    register_user(message.from_user.id, message.from_user.username or "")
    set_ai_model(message.from_user.id, "gpt")
    clear_history(message.from_user.id)
    bot.send_message(message.chat.id, "✅ Switched to *Elyon Core*\n\nFast free AI. Start chatting!",
                     parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == "⭐ Elyon Nova")
def switch_pro(message):
    register_user(message.from_user.id, message.from_user.username or "")
    user_id = message.from_user.id
    if has_active_sub(user_id):
        set_ai_model(user_id, "gemini")
        clear_history(user_id)
        bot.send_message(message.chat.id, "✅ Switched to *Elyon Nova*\n\nDeep thinking AI.",
                         parse_mode="Markdown")
    else:
        show_payment_options(message.chat.id, user_id)


# ── AI сообщения ──────────────────────────────────────────────────────────

MENU_TEXTS = {"💬 Chat with AI", "👤 Personal account", "🆓 Elyon Core", "⭐ Elyon Nova", "🛠 Control Panel"}

def ensure_registered(message):
    register_user(message.from_user.id, message.from_user.username or "")


@bot.message_handler(func=lambda m: True)
def handle_message(message):
    ensure_registered(message)
    user_id = message.from_user.id

    # Обработка ввода custom days
    if user_id in user_states and user_states[user_id].get("state") == "waiting_custom_days":
        try:
            days = int(message.text.strip())
            if days < 1 or days > 365:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "❌ Please enter a number between 1 and 365.")
            return
        del user_states[user_id]
        rub, stars, usdt = calc_custom_price(days)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"⭐ Pay {stars} stars", callback_data=f"pay_stars_custom_{days}"),
            types.InlineKeyboardButton(f"💳 Pay {rub} ₽ via CryptoBot", callback_data=f"pay_crypto_custom_{days}"),
            types.InlineKeyboardButton("◀️ Back", callback_data="back_start"),
        )
        bot.send_message(
            message.chat.id,
            f"📅 *{days} days subscription*\n\n"
            f"⭐ Stars: *{stars}*\n"
            f"💳 Rubles: *{rub} ₽*\n\n"
            f"Choose payment method:",
            parse_mode="Markdown",
            reply_markup=markup
        )
        return

    if message.text and message.text in MENU_TEXTS:
        return

    # Режим обслуживания
    if is_maintenance() and user_id != OWNER_ID:
        bot.send_message(message.chat.id,
                         "🔧 *Maintenance in progress*\n\nElyon AI is temporarily unavailable. Please try again later.",
                         parse_mode="Markdown")
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
        bot.send_message(message.chat.id, "⚠️ Subscription expired.")
        show_payment_options(message.chat.id, user_id)
        return

    bot.send_chat_action(message.chat.id, "typing")
    add_message(user_id, "user", message.text)
    history = get_history(user_id)

    try:
        reply = ask_gpt(history) if ai_model == "gpt" else ask_gemini(history)
        add_message(user_id, "assistant", reply)
        bot.send_message(message.chat.id, reply)
    except Exception as e:
        error_text = str(e)
        print("AI error:", e)
        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            bot.send_message(message.chat.id, "⏳ Too many requests. Try again in a minute.")
        elif "404" in error_text or "NOT_FOUND" in error_text:
            bot.send_message(message.chat.id, "❌ Model unavailable. Contact administrator.")
        else:
            bot.send_message(message.chat.id, f"❌ {error_text[:200]}")


# ── Flask API ─────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

@app.after_request
def after_request(response):
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
    response.headers.add("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    return response

@app.route("/api/chat", methods=["POST", "OPTIONS"])
def api_chat():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.json
        user_id  = data.get("user_id")
        model    = data.get("model", "gpt")
        messages = data.get("messages", [])
        if not user_id or not messages:
            return jsonify({"error": "Missing user_id or messages"}), 400
        if is_maintenance() and user_id != OWNER_ID:
            return jsonify({"error": "Maintenance in progress"}), 503
        if model == "gemini":
            if not has_active_sub(user_id):
                return jsonify({"error": "No active subscription"}), 403
            reply = ask_gemini(messages)
        else:
            reply = ask_gpt(messages)
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
        "user_id":   user[0],
        "username":  user[1],
        "role":      user[3],
        "ai_model":  user[4],
        "sub_type":  user[5],
        "sub_until": user[6],
        "balance":   get_balance(user_id),
        "referrals": get_referral_count(user_id),
        "has_sub":   has_active_sub(user_id),
        "ref_link":  f"https://t.me/{BOT_USERNAME}?start={user_id}"
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# ── Запуск ────────────────────────────────────────────────────────────────

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)

try:
    print("bot is running...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    bot.infinity_polling(timeout=20, long_polling_timeout=5)
except KeyboardInterrupt:
    print("Stopped.")
except Exception as e:
    print("Error:", e)
