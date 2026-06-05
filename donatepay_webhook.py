# ═══════════════════════════════════════════════════════════════
# DonatePay Webhook — добавить в bot.py после строки health()
# ═══════════════════════════════════════════════════════════════
# 
# НАСТРОЙКА:
# 1. Зайди в DonatePay → Интеграция с DA → Webhook
# 2. Укажи URL: https://elyon-bot.onrender.com/api/donatepay_webhook
# 3. В поле "Секретный ключ" придумай любой токен и запиши его
# 4. Добавь в Render Environment Variable:
#    DONATEPAY_SECRET = твой_секретный_токен
# ═══════════════════════════════════════════════════════════════

import hmac as _hmac
import hashlib as _hashlib

DONATEPAY_SECRET = os.environ.get("DONATEPAY_SECRET", "")

# Маппинг сумм → тариф
DONATEPAY_AMOUNT_TO_TIER = {
    "91":  "nova",
    "182": "pro",
    "265": "absolution",
    # Копейки на случай если сумма придёт с .00
    "91.00":  "nova",
    "182.00": "pro",
    "265.00": "absolution",
}

@app.route("/api/donatepay_webhook", methods=["POST", "OPTIONS"])
def donatepay_webhook():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.json or {}
        
        # DonatePay шлёт поле "notification_type"
        # Нас интересует только тип "donation" (оплата прошла)
        notif_type = data.get("notification_type", "")
        if notif_type != "donation":
            return jsonify({"ok": True, "skip": True})

        # Проверка подписи (если настроен секрет)
        if DONATEPAY_SECRET:
            incoming_hash = request.headers.get("X-DonatePay-Signature", "")
            raw_body = request.get_data(as_text=True)
            expected = _hmac.new(
                DONATEPAY_SECRET.encode(),
                raw_body.encode(),
                _hashlib.sha256
            ).hexdigest()
            if not _hmac.compare_digest(incoming_hash, expected):
                return jsonify({"ok": False, "error": "Invalid signature"}), 403

        # Данные донации
        vars_data  = data.get("vars", {})
        amount_str = str(data.get("sum", "0")).split(".")[0]  # "91.00" → "91"
        username   = data.get("username", "")   # имя отправителя
        comment    = data.get("comment", "")    # комментарий
        
        # Пробуем извлечь user_id из комментария или vars
        # Пользователь должен указать свой Telegram ID в комментарии
        # Либо мы добавляем его в ссылку как параметр
        user_id_str = vars_data.get("user_id", "") or comment.strip()
        
        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            # Если user_id не удалось извлечь — логируем и уведомляем owner
            print(f"DonatePay: не удалось извлечь user_id из webhook. Data: {data}")
            try:
                bot.send_message(
                    OWNER_ID,
                    f"💰 DonatePay: новый платёж {amount_str}₽\n"
                    f"Плательщик: {username}\n"
                    f"Комментарий: {comment}\n\n"
                    f"⚠️ user_id не определён — активируйте вручную:\n"
                    f"/give @username nova 30d"
                )
            except:
                pass
            return jsonify({"ok": True, "manual": True})

        # Определяем тариф по сумме
        tier = DONATEPAY_AMOUNT_TO_TIER.get(amount_str)
        if not tier:
            print(f"DonatePay: неизвестная сумма {amount_str}")
            return jsonify({"ok": True, "skip": True})

        tier_labels = {
            "nova":       "Elyon Nova — 91 ₽",
            "pro":        "Elyon PRO — 182 ₽",
            "absolution": "Elyon Absolution — 265 ₽",
        }

        # Активируем подписку
        from datetime import datetime, timedelta
        until = datetime.now() + timedelta(days=30)
        until_str = until.strftime("%d.%m.%Y %H:%M")
        set_subscription(user_id, tier, until_str)
        set_ai_model(user_id, "gemini")
        log_payment(user_id, username, tier, "donatepay", amount_str)

        # Уведомляем пользователя в Telegram
        try:
            bot.send_message(
                user_id,
                f"✅ Оплата получена! Подписка {tier_labels[tier]} активирована на 30 дней.\n\n"
                f"Используй /start чтобы начать пользоваться расширенной моделью."
            )
        except Exception as e:
            print(f"DonatePay: не удалось уведомить пользователя {user_id}: {e}")

        # Уведомляем owner
        try:
            bot.send_message(
                OWNER_ID,
                f"💰 DonatePay платёж!\n"
                f"Пользователь: {user_id} ({username})\n"
                f"Тариф: {tier_labels[tier]}\n"
                f"Сумма: {amount_str} ₽"
            )
        except:
            pass

        return jsonify({"ok": True, "tier": tier, "user_id": user_id})

    except Exception as e:
        print(f"DonatePay webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
