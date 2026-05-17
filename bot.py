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
                      add_balance, get_referral_count)
from ai_clients import ask_gpt, ask_gemini

logging.basicConfig(level=logging.CRITICAL)

TOKEN        = os.environ.get("BOT_TOKEN", "")
OWNER_ID     = int(os.environ.get("OWNER_ID", "7113603197"))
CRYPTO_TOKEN = os.environ.get("CRYPTO_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "ElyonAI_bot")  # имя бота без @

import database
database.OWNER_ID = OWNER_ID

bot = telebot.TeleBot(TOKEN, threaded=False)
init_db()

# ── Цены ──────────────────────────────────────────────────────────────────

PRICES = {
    "month":    {"stars": 30,  "label": "30 days — 30 ⭐",   "days": 30,  "rub": 50},
    "halfyear": {"stars": 60,  "label": "6 months — 60 ⭐",  "days": 180, "rub": 182},
    "forever":  {"stars": 120, "label": "Forever — 120 ⭐",  "days": 0,   "rub": 429},
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


# ── Клавиатуры ────────────────────────────────────────────────────────────

def main_menu_keyboard():
    """Постоянная нижняя клавиатура."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("💬 Chat with AI"),
        types.KeyboardButton("👤 Personal account"),
        types.KeyboardButton("🆓 Elyon Core"),
        types.KeyboardButton("⭐ Elyon Nova"),
    )
    return markup


def show_start(chat_id):
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
        types.InlineKeyboardButton("⭐ 30 days — 30 stars",    callback_data="pay_stars_month"),
        types.InlineKeyboardButton("💳 30 days — 50 ₽",        callback_data="pay_crypto_month"),
        types.InlineKeyboardButton("⭐ 6 months — 60 stars",   callback_data="pay_stars_halfyear"),
        types.InlineKeyboardButton("💳 6 months — 182 ₽",      callback_data="pay_crypto_halfyear"),
        types.InlineKeyboardButton("⭐ Forever — 120 stars",   callback_data="pay_stars_forever"),
        types.InlineKeyboardButton("💳 Forever — 429 ₽",       callback_data="pay_crypto_forever"),
    )
    if balance >= 50:
        markup.add(
            types.InlineKeyboardButton(f"🪙 Pay with coins (balance: {balance})", callback_data="pay_virtual_menu")
        )
    markup.add(types.InlineKeyboardButton("◀️ Back", callback_data="back_start"))
    bot.send_message(
        chat_id,
        "⭐ *Elyon Nova — Subscription*\n\n"
        "⭐ Telegram Stars\n"
        "💳 CryptoBot (₽/USDT)\n"
        + (f"🪙 Virtual coins (your balance: *{balance}*)\n" if balance >= 50 else "") +
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


def activate_subscription(user_id, plan, chat_id):
    price = PRICES[plan]
    if plan == "forever":
        set_subscription(user_id, "forever", "none")
    else:
        until = datetime.now() + timedelta(days=price["days"])
        set_subscription(user_id, plan, until.strftime("%d.%m.%Y %H:%M"))
    set_ai_model(user_id, "gemini")
    clear_history(user_id)
    bot.send_message(
        chat_id,
        f"✅ *Subscription activated!*\n"
        f"Plan: *{price['label']}*\n\n"
        "You are now using *Elyon Nova* 🌟",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    bot.send_message(
        OWNER_ID,
        f"💰 New subscription!\n"
        f"User: @{bot.get_chat(user_id).username or 'no username'}\n"
        f"ID: {user_id}\nPlan: {price['label']}"
    )


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
            bot.send_message(referred_by, f"🎉 Someone joined via your referral link! +10 coins added to your balance.")
        except:
            pass
    bot.send_message(message.chat.id, "🔄 Loading...", reply_markup=main_menu_keyboard())
    show_start(message.chat.id)


# ── Inline кнопки ─────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass

    if call.data == "back_start":
        show_start(chat_id)

    elif call.data == "choose_free":
        set_ai_model(user_id, "gpt")
        clear_history(user_id)
        bot.send_message(
            chat_id,
            "✅ *Elyon Core* activated!\n\nFast answers, always free. Start chatting!",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

    elif call.data == "choose_pro":
        if has_active_sub(user_id):
            set_ai_model(user_id, "gemini")
            clear_history(user_id)
            bot.send_message(
                chat_id,
                "✅ *Elyon Nova* activated!\n\nDeep thinking mode. Start chatting!",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
        else:
            show_payment_options(chat_id, user_id)

    elif call.data == "pay_virtual_menu":
        show_virtual_payment(chat_id, user_id)

    elif call.data.startswith("pay_virtual_"):
        plan = call.data.replace("pay_virtual_", "")
        cost = VIRTUAL_PRICES[plan]["rub"]
        if spend_balance(user_id, cost):
            activate_subscription(user_id, plan, chat_id)
        else:
            bot.send_message(chat_id, "❌ Not enough coins.", reply_markup=main_menu_keyboard())

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
            bot.send_message(
                chat_id,
                f"💳 *Payment via CryptoBot*\n*{price['label']}*\n\nAfter payment press /check",
                parse_mode="Markdown",
                reply_markup=markup
            )
        else:
            bot.send_message(chat_id, "❌ Payment error. Try again later.")


# ── Оплата звёздами ───────────────────────────────────────────────────────

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(query):
    bot.answer_pre_checkout_query(query.id, ok=True)


@bot.message_handler(content_types=["successful_payment"])
def payment_success(message):
    user_id = message.from_user.id
    plan = message.successful_payment.invoice_payload.replace("pro_", "")
    activate_subscription(user_id, plan, message.chat.id)


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
                    plan = payload.split("_")[1]
                    activate_subscription(user_id, plan, message.chat.id)
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
        show_start(message.chat.id)
        return
    model = "🆓 Elyon Core" if user[4] == "gpt" else "⭐ Elyon Nova"
    bot.send_message(
        message.chat.id,
        f"Current model: *{model}*\n\nWrite your message!",
        parse_mode="Markdown"
    )


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
        labels = {"month": "30 days", "halfyear": "6 months"}
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
    bot.send_message(
        message.chat.id,
        "✅ Switched to *Elyon Core*\n\nFast free AI. Start chatting!",
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda m: m.text == "⭐ Elyon Nova")
def switch_pro(message):
    register_user(message.from_user.id, message.from_user.username or "")
    user_id = message.from_user.id
    if has_active_sub(user_id):
        set_ai_model(user_id, "gemini")
        clear_history(user_id)
        bot.send_message(
            message.chat.id,
            "✅ Switched to *Elyon Nova*\n\nDeep thinking AI. Start chatting!",
            parse_mode="Markdown"
        )
    else:
        show_payment_options(message.chat.id, user_id)


# ── AI сообщения ──────────────────────────────────────────────────────────

MENU_TEXTS = {"💬 Chat with AI", "👤 Personal account", "🆓 Elyon Core", "⭐ Elyon Nova"}

def ensure_registered(message):
    """Регистрирует пользователя если его нет в БД."""
    register_user(message.from_user.id, message.from_user.username or "")

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    ensure_registered(message)

    if message.text in MENU_TEXTS:
        return

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


# ── Flask API для Mini App ────────────────────────────────────────────────

app = Flask(__name__)
CORS(app, origins=[
    "https://elyon-miniapp.vercel.app",
    "https://*.vercel.app",
    "http://localhost:3000"
])

@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        data = request.json
        user_id = data.get("user_id")
        model   = data.get("model", "gpt")
        messages = data.get("messages", [])

        if not user_id or not messages:
            return jsonify({"error": "Missing user_id or messages"}), 400

        if model == "gemini":
            if not has_active_sub(user_id):
                return jsonify({"error": "No active subscription"}), 403
            reply = ask_gemini(messages)
        else:
            reply = ask_gpt(messages)

        return jsonify({"reply": reply})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/user/<int:user_id>", methods=["GET"])
def api_user(user_id):
    user = get_user(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "user_id": user[0],
        "username": user[1],
        "role": user[3],
        "ai_model": user[4],
        "sub_type": user[5],
        "sub_until": user[6],
        "balance": get_balance(user_id),
        "referrals": get_referral_count(user_id),
        "has_sub": has_active_sub(user_id)
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
