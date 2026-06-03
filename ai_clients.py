import sys
import io
import os
import random
import re
from google import genai
from google.genai import types as genai_types

# ── Ключи ─────────────────────────────────────────────────────────────────

# Elyon Core (бесплатная) — DeepSeek, 2 ключа
DEEPSEEK_KEYS = [
    os.environ.get("DEEPSEEK_KEY_1", ""),
    os.environ.get("DEEPSEEK_KEY_2", ""),
]
DEEPSEEK_KEYS = [k for k in DEEPSEEK_KEYS if k]

# Elyon Nova — Gemini Flash с thinking, 2 ключа
GEMINI_NOVA_KEYS = [
    os.environ.get("GEMINI_NOVA_KEY_1", ""),
    os.environ.get("GEMINI_NOVA_KEY_2", ""),
]
GEMINI_NOVA_KEYS = [k for k in GEMINI_NOVA_KEYS if k]

# Elyon PRO — Gemini Flash с расширенным thinking, 2 ключа
GEMINI_PRO_KEYS = [
    os.environ.get("GEMINI_PRO_KEY_1", ""),
    os.environ.get("GEMINI_PRO_KEY_2", ""),
]
GEMINI_PRO_KEYS = [k for k in GEMINI_PRO_KEYS if k]

# Elyon Absolution — Gemini Pro, 2 ключа
GEMINI_ABS_KEYS = [
    os.environ.get("GEMINI_ABS_KEY_1", ""),
    os.environ.get("GEMINI_ABS_KEY_2", ""),
]
GEMINI_ABS_KEYS = [k for k in GEMINI_ABS_KEYS if k]

# Обратная совместимость для Telegram бота
# Если отдельные ключи не заданы — используем старые переменные
if not DEEPSEEK_KEYS:
    # Fallback на Gemini для Core если DeepSeek не настроен
    _fb_free = [os.environ.get(f"GEMINI_FREE_KEY_{i}", "") for i in range(1, 4)]
    DEEPSEEK_KEYS = [k for k in _fb_free if k]

if not GEMINI_NOVA_KEYS:
    _fb_pro = [os.environ.get(f"GEMINI_PRO_KEY_{i}", "") for i in range(1, 4)]
    GEMINI_NOVA_KEYS = [k for k in _fb_pro if k]

if not GEMINI_PRO_KEYS and GEMINI_NOVA_KEYS:
    GEMINI_PRO_KEYS = GEMINI_NOVA_KEYS

if not GEMINI_ABS_KEYS and GEMINI_PRO_KEYS:
    GEMINI_ABS_KEYS = GEMINI_PRO_KEYS

# Для обратной совместимости в bot.py
GEMINI_FREE_KEYS = DEEPSEEK_KEYS

# ── Системный промпт ───────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are Elyon AI — a smart and helpful assistant. "
    "Your name is Elyon. Never mention that you are Gemini, Google, ChatGPT, GPT, "
    "OpenAI, Grok, xAI, or any other AI system. "
    "If asked who you are or what your name is, always say you are Elyon AI, "
    "created by the Elyon team. Be friendly, concise, and helpful. "
    "When you use web search results, present the information naturally without "
    "mentioning that you searched the web or citing sources explicitly. "
    "IMPORTANT: Never use markdown formatting in your responses. "
    "Do not use asterisks (*), backticks (`), underscores (_), pound signs (#), "
    "or any other markdown symbols. Write in plain text only. "
    "For code examples, write the code without code fences or backticks. "
    "Use plain dashes or numbers for lists."
)

# ── Замена упоминаний реальных AI в ответах ───────────────────────────────

REPLACE_PAIRS = [
    (r'\bGemini\b', 'Gemimi'),
    (r'\bGoogle DeepMind\b', 'Google DeepMind'),
    (r'\bGoogle\b', 'Google'),
    (r'\bChatGPT\b', 'ChatGPT'),
    (r'\bGPT-[^\s]*', 'GPT'),
    (r'\bGPT\b', 'GPT'),
    (r'\bOpenAI\b', 'OpenAI'),
    (r'\bGrok\b', 'Grok'),
    (r'\bxAI\b', 'xAI'),
]

def mask_identity(text):
    for pattern, replacement in REPLACE_PAIRS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def clean_text(text):
    """
    Очищает текст от лишних markdown-символов.
    Сохраняет читаемость: убирает *, `, _, #, но оставляет структуру текста.
    Код внутри блоков ``` оборачиваем чисто без фенсов.
    """
    if not text:
        return text

    # Извлекаем и обрабатываем блоки кода отдельно — они нужны как есть
    code_blocks = {}
    placeholder_idx = [0]

    def save_code_block(m):
        key = f"\x00CODE{placeholder_idx[0]}\x00"
        placeholder_idx[0] += 1
        lang = m.group(1).strip()
        code = m.group(2)
        code_blocks[key] = (lang, code)
        return key

    # Сохраняем блоки ```lang\ncode```
    text = re.sub(r'```(\w*)\n?([\s\S]*?)```', save_code_block, text)

    # Убираем inline код `...` — оставляем содержимое без бэктиков
    text = re.sub(r'`([^`\n]+)`', r'\1', text)

    # Убираем bold/italic: ***text***, **text**, *text*, ___text___, __text__, _text_
    text = re.sub(r'\*{3}(.+?)\*{3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*{2}(.+?)\*{2}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*',       r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{3}(.+?)_{3}',   r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{2}(.+?)_{2}',   r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_',         r'\1', text, flags=re.DOTALL)

    # Убираем заголовки ## Заголовок → Заголовок
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # Убираем маркеры списков - * • в начале строки, оставляем дефис
    text = re.sub(r'^\*\s+', '- ', text, flags=re.MULTILINE)
    text = re.sub(r'^•\s+',  '- ', text, flags=re.MULTILINE)

    # Убираем горизонтальные линии ---
    text = re.sub(r'^-{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\*{3,}\s*$', '', text, flags=re.MULTILINE)

    # Убираем > цитаты
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)

    # Восстанавливаем блоки кода без фенсов — просто сам код с отступом
    for key, (lang, code) in code_blocks.items():
        label = f"[{lang.upper()}]" if lang else "[КОД]"
        restored = f"{label}\n{code.strip()}"
        text = text.replace(key, restored)

    # Убираем множественные пустые строки (больше 2)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def safe_text(text):
    if text is None:
        return "Пустой ответ от AI."
    text = text.encode("utf-8", errors="ignore").decode("utf-8")
    text = mask_identity(text)
    text = clean_text(text)
    return text

# ── Построение сообщений ──────────────────────────────────────────────────

def build_contents(messages):
    contents = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(
            genai_types.Content(
                role=role,
                parts=[genai_types.Part(text=str(msg["content"]))]
            )
        )
    return contents

# ── Определяем нужен ли веб-поиск ────────────────────────────────────────

SEARCH_TRIGGERS = [
    r'\b(кто такой|who is|who are|расскажи о|tell me about)\b',
    r'\b(сейчас|сегодня|now|today|current|latest|новости|news|2024|2025|2026)\b',
    r'\b(цена|price|курс|rate|стоимость|cost)\b',
    r'\b(president|президент|minister|министр|ceo|founder|основатель)\b',
    r'\b(правда ли|is it true|fact|факт|реально|really)\b',
]

def needs_search(text):
    text_lower = text.lower()
    for pattern in SEARCH_TRIGGERS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False

# ── Общая функция запроса к DeepSeek ─────────────────────────────

def ask_deepseek_with_keys(messages, keys):
    """Запрос к DeepSeek через OpenAI-совместимый API."""
    if not keys:
        # Fallback на Gemini если DeepSeek ключи не настроены
        return ask_gemini_with_keys(
            messages, GEMINI_NOVA_KEYS or GEMINI_PRO_KEYS,
            "gemini-2.5-flash", thinking=False
        )

    import urllib.request
    import json as _json

    shuffled = keys.copy()
    random.shuffle(shuffled)

    # Конвертируем историю в формат OpenAI
    openai_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in messages:
        role = "assistant" if msg["role"] == "assistant" else "user"
        openai_messages.append({"role": role, "content": str(msg["content"])})

    for key in shuffled:
        try:
            payload = _json.dumps({
                "model":       "deepseek-chat",
                "messages":    openai_messages,
                "temperature": 0.7,
                "max_tokens":  2048,
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions",
                data=payload,
                headers={
                    "Content-Type":  "application/json",
                    "Authorization": f"Bearer {key}",
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
                text = data["choices"][0]["message"]["content"]
                return safe_text(text)

        except Exception as e:
            err = str(e)
            print(f"DeepSeek key error: {err[:120]}")
            if "429" in err or "quota" in err.lower():
                continue
            if "503" in err or "502" in err:
                continue
            raise

    raise Exception("DeepSeek лимит исчерпан. Попробуй через минуту.")

# ── Общая функция запроса к Gemini ────────────────────────────────────────

def ask_gemini_with_keys(messages, keys, model, thinking=False, use_search=False):
    if not keys:
        raise Exception("NO_KEYS: ключи не настроены в переменных окружения")

    shuffled = keys.copy()
    random.shuffle(shuffled)
    last_error = None

    for key in shuffled:
        try:
            client = genai.Client(api_key=key)
            cfg = genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT
            )
            if thinking:
                cfg.thinking_config = genai_types.ThinkingConfig(thinking_budget=8000)
            if use_search:
                cfg.tools = [genai_types.Tool(
                    google_search=genai_types.GoogleSearch()
                )]

            response = client.models.generate_content(
                model=model,
                contents=build_contents(messages),
                config=cfg
            )
            return safe_text(response.text)

        except Exception as e:
            error_text = str(e)
            print(f"Ключ не сработал ({model}): {error_text[:120]}")
            last_error = error_text
            if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                continue
            if "503" in error_text or "UNAVAILABLE" in error_text:
                continue
            raise

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")

# ── Elyon Core — DeepSeek ─────────────────────────────────────────

def ask_gpt(messages):
    """Elyon Core — Gemini Flash без thinking (бесплатно, надёжно)."""
    last_msg   = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    # Используем Nova ключи для Core — те же ключи, без thinking
    keys = GEMINI_NOVA_KEYS or GEMINI_PRO_KEYS or GEMINI_ABS_KEYS
    if not keys:
        raise Exception("NO_KEYS: настрой GEMINI_NOVA_KEY_1 в переменных окружения")
    return ask_gemini_with_keys(
        messages, keys, "gemini-2.5-flash",
        thinking=False, use_search=use_search
    )

# ── Elyon Nova ────────────────────────────────────────────────────

def ask_nova(messages):
    last_msg = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    return ask_gemini_with_keys(
        messages, GEMINI_NOVA_KEYS, "gemini-2.5-flash",
        thinking=True, use_search=use_search
    )

# ── Elyon PRO ─────────────────────────────────────────────────────

def ask_pro(messages):
    last_msg = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    return ask_gemini_with_keys(
        messages, GEMINI_PRO_KEYS, "gemini-2.5-flash",
        thinking=True, use_search=use_search
    )

# ── Elyon Absolution ──────────────────────────────────────────────

def ask_absolution(messages):
    last_msg = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    return ask_gemini_with_keys(
        messages, GEMINI_ABS_KEYS, "gemini-2.5-pro",
        thinking=True, use_search=use_search
    )

# ── Обратная совместимость ────────────────────────────────────────

def ask_gemini(messages):
    """Алиас — обратная совместимость для старых вызовов."""
    return ask_nova(messages)

# ── Запрос с файлом ───────────────────────────────────────────────────────

def ask_with_file(file_bytes, mime_type, file_name, user_prompt, history,
                  use_pro=False, model_tier="core"):
    """
    Отправляет файл + текст в Gemini для анализа.
    DeepSeek не поддерживает файлы — для Core используем Nova ключи.
    model_tier: 'core' | 'nova' | 'pro' | 'absolution'
    """
    # Выбираем ключи по тиру
    if model_tier == "absolution":
        keys  = GEMINI_ABS_KEYS or GEMINI_PRO_KEYS
        model = "gemini-2.5-pro"
        thinking = True
    elif model_tier == "pro":
        keys  = GEMINI_PRO_KEYS or GEMINI_NOVA_KEYS
        model = "gemini-2.5-flash"
        thinking = True
    elif model_tier == "nova" or use_pro:
        keys  = GEMINI_NOVA_KEYS or GEMINI_PRO_KEYS
        model = "gemini-2.5-flash"
        thinking = True
    else:
        # Core — DeepSeek не умеет файлы, используем Nova ключи
        keys  = GEMINI_NOVA_KEYS or GEMINI_PRO_KEYS or GEMINI_ABS_KEYS
        model = "gemini-2.5-flash"
        thinking = False

    if not keys:
        raise Exception("NO_KEYS: ключи не настроены")

    shuffled = keys.copy()
    random.shuffle(shuffled)

    for key in shuffled:
        try:
            client = genai.Client(api_key=key)

            file_obj = client.files.upload(
                file=io.BytesIO(file_bytes),
                config={"mime_type": mime_type, "display_name": file_name}
            )

            contents = []
            for msg in history[:-1]:
                role = "model" if msg["role"] == "assistant" else "user"
                contents.append(genai_types.Content(
                    role=role,
                    parts=[genai_types.Part(text=str(msg["content"]))]
                ))

            contents.append(genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(file_data=genai_types.FileData(
                        file_uri=file_obj.uri,
                        mime_type=mime_type
                    )),
                    genai_types.Part(text=user_prompt)
                ]
            ))

            cfg = genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT
            )
            if thinking:
                cfg.thinking_config = genai_types.ThinkingConfig(thinking_budget=8000)

            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=cfg
            )

            try:
                client.files.delete(name=file_obj.name)
            except:
                pass

            return safe_text(response.text)

        except Exception as e:
            error_text = str(e)
            print(f"ask_with_file error: {error_text[:120]}")
            if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                continue
            if "503" in error_text or "UNAVAILABLE" in error_text:
                continue
            raise

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")


# ── Проверка исчерпанности ключей ─────────────────────────────────────────

def check_keys_status():
    """Проверяет статус всех ключей."""
    results = {"core_deepseek": [], "nova": [], "pro": [], "absolution": []}

    def test_gemini(key):
        try:
            client = genai.Client(api_key=key)
            client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[genai_types.Content(role="user", parts=[genai_types.Part(text="Hi")])],
                config=genai_types.GenerateContentConfig()
            )
            return {"key": key[:8] + "...", "status": "ok"}
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                return {"key": key[:8] + "...", "status": "exhausted"}
            elif "401" in err or "API_KEY_INVALID" in err:
                return {"key": key[:8] + "...", "status": "invalid"}
            elif "503" in err:
                return {"key": key[:8] + "...", "status": "unavailable"}
            return {"key": key[:8] + "...", "status": f"error: {err[:40]}"}

    def test_deepseek(key):
        import urllib.request, json as _j
        try:
            payload = _j.dumps({
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 5
            }).encode()
            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10):
                return {"key": key[:8] + "...", "status": "ok"}
        except Exception as e:
            err = str(e)
            if "429" in err: return {"key": key[:8] + "...", "status": "exhausted"}
            if "401" in err: return {"key": key[:8] + "...", "status": "invalid"}
            return {"key": key[:8] + "...", "status": f"error: {err[:40]}"}

    for k in DEEPSEEK_KEYS:    results["core_deepseek"].append(test_deepseek(k))
    for k in GEMINI_NOVA_KEYS: results["nova"].append(test_gemini(k))
    for k in GEMINI_PRO_KEYS:  results["pro"].append(test_gemini(k))
    for k in GEMINI_ABS_KEYS:  results["absolution"].append(test_gemini(k))
    return results
