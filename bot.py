import os
import telebot
from telebot import types
from datetime import datetime, timedelta
import logging
import requests

from database import init_db, get_user, register_user, set_ai_model
from database import set_subscription, has_active_sub, add_message, get_history, clear_history
from ai_clients import ask_gpt, ask_gemini

logging.basicConfig(level=logging.CRITICAL)

TOKEN        = "BOT_TOKEN"
OWNER_ID     = "OWNER_ID"
CRYPTO_TOKEN = "CRYPTO_TOKEN"

import database
database.OWNER_ID = OWNER_ID

bot = telebot.TeleBot(TOKEN)
init_db()

PRICES = {
    "month":    {"stars": 30,  "label": "30 days — 30 stars",   "days": 30},
    "halfyear": {"stars": 100,  "label": "six months — 60 stars",   "days": 180},
    "forever":  {"stars": 250, "label": "forever — 120 stars", "days": 0},
}

CRYPTO_PRICES = {
    "month":    {"amount": "0.55", "label": "30 days — 50 rub."},
    "halfyear": {"amount": "1.10", "label": "six months — 182 rub."},
    "forever":  {"amount": "2.20", "label": "forever — 429 rub."},
}


# ==============================
# Вспомогательные функции
# ==============================

def back_button():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("◀️ back", callback_data="back_start"))
    return markup


def show_start(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🆓 Elyon Core", callback_data="choose_free"),
        types.InlineKeyboardButton("⭐ Elyon Nova",  callback_data="choose_pro")
    )
    bot.send_message(
        chat_id,
        "👋 Hello!\n\n"
        "I'm Elyon AI:\n\n"
        "🆓 *Elyon Core* — free\n"
        "⭐ *Elyon Nova* — by subscription\n\n"
        "select version:",
        parse_mode="Markdown",
        reply_markup=markup
    )


def set_main_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(
        types.KeyboardButton("💬 Chat with AI"),
        types.KeyboardButton("👤 personal account")
    )
    bot.send_message(chat_id, "Select chapter:", reply_markup=markup)


def show_payment_options(chat_id, user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⭐ 30 days — 30 stars",   callback_data="pay_stars_month"),
        types.InlineKeyboardButton("💳 30 days — 50 руб.",    callback_data="pay_crypto_month"),
        types.InlineKeyboardButton("⭐ six months — 60 з",   callback_data="pay_stars_halfyear"),
        types.InlineKeyboardButton("💳 six months — 100 руб.",   callback_data="pay_crypto_halfyear"),
        types.InlineKeyboardButton("⭐ forever — 120 звёзд", callback_data="pay_stars_forever"),
        types.InlineKeyboardButton("💳 forever — 200 руб.",  callback_data="pay_crypto_forever"),
    )
    markup.add(types.InlineKeyboardButton("◀️ back", callback_data="back_start"))
    bot.send_message(
        chat_id,
        "⭐ *Pro AI version*\n\n"
        "Select your subscription type and payment method:\n\n"
        "⭐ — Telegram Stars\n"
        "💳 — CryptoBot (rub/USDT)\n\n"
        "🔹 30 days\n"
        "🔹 six months\n"
        "🔹 forever",
        parse_mode="Markdown",
        reply_markup=markup
    )


def activate_subscription(user_id, plan, chat_id):
    price = PRICES[plan]
    if plan == "forever":
        set_subscription(user_id, "forever", "none")
    else:
        until = datetime.now() + timedelta(days=price["days"])
        set_subscription(user_id, plan, until.strftime("%d.%m.%Y %H:%M"))

    set_ai_model(user_id, "gemini")
    clear_history(user_id)

    markup = back_button()
    bot.send_message(
        chat_id,
        f"✅ subscription activated!\n"
        f"Rate: *{price['label']}*\n\n"
        "Your select *Elyon Nova*.\n"
        "Now you can use Elyon Nova.",
        parse_mode="Markdown",
        reply_markup=markup
    )
    set_main_menu(chat_id)


def create_crypto_invoice(amount, user_id, plan):
    try:
        response = requests.post(
            "https://pay.crypt.bot/api/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN},
            json={
                "asset": "USDT",
                "amount": amount,
                "description": f"Elyon Nova — {plan}",
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


# ==============================
# /start
# ==============================

@bot.message_handler(commands=["start"])
def start(message):
    register_user(message.from_user.id, message.from_user.username or "")
    show_start(message.chat.id)


# ==============================
# Inline кнопки
# ==============================

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    bot.delete_message(chat_id, call.message.message_id)

    if call.data == "back_start":
        show_start(chat_id)

    elif call.data == "choose_free":
        set_ai_model(user_id, "gpt")
        clear_history(user_id)
        bot.send_message(
            chat_id,
            "✅ You select *Elyon Core*.\n\n"
            "Now you can use Elyon Core.",
            parse_mode="Markdown",
            reply_markup=back_button()
        )
        set_main_menu(chat_id)

    elif call.data == "choose_pro":
        if has_active_sub(user_id):
            set_ai_model(user_id, "gemini")
            clear_history(user_id)
            bot.send_message(
                chat_id,
                "✅ You select *Elyon Nova*.\n\n"
                "Now you can use Elyon Core.",
                parse_mode="Markdown",
                reply_markup=back_button()
            )
            set_main_menu(chat_id)
        else:
            show_payment_options(chat_id, user_id)

    elif call.data.startswith("pay_stars_"):
        plan = call.data.replace("pay_stars_", "")
        price = PRICES[plan]
        bot.send_invoice(
            chat_id,
            title=f"Pro AI version — {price['label']}",
            description="access to Elyon Nova",
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
            markup.add(types.InlineKeyboardButton(
                f"💳 Pay {price['label']}", url=invoice["pay_url"]
            ))
            markup.add(types.InlineKeyboardButton("◀️ back", callback_data="back_start"))
            bot.send_message(
                chat_id,
                f"payment via CryptoBot:\n*{price['label']}*\n\n"
                "After payment, click /check",
                parse_mode="Markdown",
                reply_markup=markup
            )
        else:
            bot.send_message(
                chat_id,
                "❌ Error creating payment. Try again later..",
                reply_markup=back_button()
            )


# ==============================
# Оплата звёздами
# ==============================

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(query):
    bot.answer_pre_checkout_query(query.id, ok=True)


@bot.message_handler(content_types=["successful_payment"])
def payment_success(message):
    user_id = message.from_user.id
    plan = message.successful_payment.invoice_payload.replace("pro_", "")
    activate_subscription(user_id, plan, message.chat.id)
    bot.send_message(
        OWNER_ID,
        f"💰 New payment in stars!\n"
        f"User: @{message.from_user.username or 'without username'}\n"
        f"ID: {user_id}\n"
        f"Plan: {PRICES[plan]['label']}"
    )


# ==============================
# Проверка оплаты CryptoBot
# ==============================

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
                    plan = payload.split("_")[1]
                    activate_subscription(user_id, plan, message.chat.id)
                    bot.send_message(
                        OWNER_ID,
                        f"💰 Payment CryptoBot!\n"
                        f"User: @{message.from_user.username or 'without username'}\n"
                        f"ID: {user_id}\n"
                        f"Plan: {plan}"
                    )
                    return
        bot.send_message(
            message.chat.id,
            "❌ Payment not found. Try again in a minute..",
            reply_markup=back_button()
        )
    except Exception as e:
        print("Check error:", e)
        bot.send_message(message.chat.id, "❌ Payment verification error.")


# ==============================
# Кнопки нижнего меню
# ==============================

@bot.message_handler(func=lambda m: m.text == "💬 chat with AI")
def menu_chat(message):
    user = get_user(message.from_user.id)
    if not user or user[4] == "none":
        show_start(message.chat.id)
        return
    model = "🆓 Elyon Core" if user[4] == "gpt" else "⭐ Elyon Nova"
    bot.send_message(
        message.chat.id,
        f"Current model: {model}\n\nWrite a message directly in the chat!",
        reply_markup=back_button()
    )


@bot.message_handler(func=lambda m: m.text == "👤 Personal account",
                     )

def menu_profile(message):
    user = get_user(message.from_user.id)
    if not user:
        return

    sub_type  = user[5]
    sub_until = user[6]

    if sub_type == "none":
        sub_info = "❌ no subscription"
    elif sub_type == "forever":
        sub_info = "♾ forever"
    else:
        labels = {"month": "30 days", "halfyear": "six months"}
        sub_info = f"✅ {labels.get(sub_type, sub_type)} (до {sub_until})"

    role_emoji = "👑" if user[3] == "owner" else "👤"

    bot.send_message(
        message.chat.id,
        f"👤 *Personal account*\n\n"
        f"Username: @{user[1] or 'not specified'}\n"
        f"Date of registration: {user[2]}\n"
        f"Role: {role_emoji} {user[3]}\n"
        f"Subscription: {sub_info}",
        parse_mode="Markdown",
        reply_markup=back_button()
    )


# ==============================
# Сообщения к AI
# ==============================

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    user_id = message.from_user.id
    user = get_user(user_id)

    if not user:
        show_start(message.chat.id)
        return

    ai_model = user[4]

    if ai_model == "none":
        show_start(message.chat.id)
        return

    if ai_model == "gemini" and not has_active_sub(user_id):
        bot.send_message(
            message.chat.id,
            "⚠️ Your subscription has expired. Choose a plan.:",
            reply_markup=back_button()
        )
        show_payment_options(message.chat.id, user_id)
        return

    bot.send_chat_action(message.chat.id, "typing")
    add_message(user_id, "user", message.text)
    history = get_history(user_id)

    try:
        if ai_model == "gpt":
            reply = ask_gpt(history)
        else:
            reply = ask_gemini(history)

        add_message(user_id, "assistant", reply)
        bot.send_message(message.chat.id, reply, reply_markup=back_button())

    except Exception as e:
        error_text = str(e)
        print("AI error:", e)

        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            bot.send_message(
                message.chat.id,
                "⏳ Too many requests. Try again in a minute..",
                reply_markup=back_button()
            )
        elif "404" in error_text or "NOT_FOUND" in error_text:
            bot.send_message(
                message.chat.id,
                "❌ The model is unavailable. Please contact the administrator..",
                reply_markup=back_button()
            )
        else:
            bot.send_message(
                message.chat.id,
                "❌ Request error. Please try again.",
                reply_markup=back_button()
            )


# ==============================
# Запуск
# ==============================


# Вставь это:
try:
    print("bot is running...")
    bot.infinity_polling(timeout=20, long_polling_timeout=5)
except KeyboardInterrupt:
    print("Бот остановлен вручную.")
except Exception as e:
    print("Ошибка:", e)
