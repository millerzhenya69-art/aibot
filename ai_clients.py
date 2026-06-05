import sys
import io
import os
import random
import re
from google import genai
from google.genai import types as genai_types

# ── Ключи ─────────────────────────────────────────────────────────────────

# Elyon Core — Gemini Flash (DeepSeek убран)
GEMINI_NOVA_KEYS = [
    os.environ.get("GEMINI_NOVA_KEY_1", ""),
    os.environ.get("GEMINI_NOVA_KEY_2", ""),
]
GEMINI_NOVA_KEYS = [k for k in GEMINI_NOVA_KEYS if k]

# Elyon PRO — Gemini Flash с расширенным thinking
GEMINI_PRO_KEYS = [
    os.environ.get("GEMINI_PRO_KEY_1", ""),
    os.environ.get("GEMINI_PRO_KEY_2", ""),
]
GEMINI_PRO_KEYS = [k for k in GEMINI_PRO_KEYS if k]

# Elyon Absolution — Gemini Pro
GEMINI_ABS_KEYS = [
    os.environ.get("GEMINI_ABS_KEY_1", ""),
    os.environ.get("GEMINI_ABS_KEY_2", ""),
]
GEMINI_ABS_KEYS = [k for k in GEMINI_ABS_KEYS if k]

# Fallbacks: если отдельные ключи не заданы — используем старые переменные
if not GEMINI_NOVA_KEYS:
    _fb = [os.environ.get(f"GEMINI_PRO_KEY_{i}", "") for i in range(1, 4)]
    GEMINI_NOVA_KEYS = [k for k in _fb if k]

if not GEMINI_PRO_KEYS and GEMINI_NOVA_KEYS:
    GEMINI_PRO_KEYS = GEMINI_NOVA_KEYS

if not GEMINI_ABS_KEYS and GEMINI_PRO_KEYS:
    GEMINI_ABS_KEYS = GEMINI_PRO_KEYS

# Обратная совместимость для bot.py
GEMINI_FREE_KEYS = GEMINI_NOVA_KEYS

# ── Системный промпт ───────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are Elyon AI — a smart and helpful assistant. "
    "Your name is Elyon. Never mention that you are Gemini, Google, ChatGPT, GPT, "
    "OpenAI, Grok, xAI, DeepSeek, or any other AI system. "
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

# ── Замена упоминаний реальных AI ─────────────────────────────────────────

REPLACE_PAIRS = [
    (r'\bGemini\b',        'Elyon'),
    (r'\bGoogle DeepMind\b','the Elyon team'),
    (r'\bDeepSeek\b',      'Elyon'),
    (r'\bChatGPT\b',       'ChatGPT'),
    (r'\bGPT-[^\s]*',      'GPT'),
    (r'\bGPT\b',           'GPT'),
    (r'\bOpenAI\b',        'OpenAI'),
    (r'\bGrok\b',          'Grok'),
    (r'\bxAI\b',           'xAI'),
]

def mask_identity(text):
    for pattern, replacement in REPLACE_PAIRS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def clean_text(text):
    if not text:
        return text
    code_blocks = {}
    placeholder_idx = [0]

    def save_code_block(m):
        key = f"\x00CODE{placeholder_idx[0]}\x00"
        placeholder_idx[0] += 1
        lang = m.group(1).strip()
        code = m.group(2)
        code_blocks[key] = (lang, code)
        return key

    text = re.sub(r'```(\w*)\n?([\s\S]*?)```', save_code_block, text)
    text = re.sub(r'`([^`\n]+)`', r'\1', text)
    text = re.sub(r'\*{3}(.+?)\*{3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*{2}(.+?)\*{2}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*',       r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{3}(.+?)_{3}',   r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{2}(.+?)_{2}',   r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_',         r'\1', text, flags=re.DOTALL)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\*\s+', '- ', text, flags=re.MULTILINE)
    text = re.sub(r'^•\s+',  '- ', text, flags=re.MULTILINE)
    text = re.sub(r'^-{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\*{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)

    for key, (lang, code) in code_blocks.items():
        label = f"[{lang.upper()}]" if lang else "[КОД]"
        restored = f"{label}\n{code.strip()}"
        text = text.replace(key, restored)

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

# ── Elyon Core — Gemini Flash без thinking (бесплатно) ────────────────────

def ask_gpt(messages):
    """Elyon Core — Gemini Flash без thinking."""
    last_msg   = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    keys = GEMINI_NOVA_KEYS or GEMINI_PRO_KEYS or GEMINI_ABS_KEYS
    if not keys:
        raise Exception("NO_KEYS: настрой GEMINI_NOVA_KEY_1 в переменных окружения")
    return ask_gemini_with_keys(
        messages, keys, "gemini-2.5-flash",
        thinking=False, use_search=use_search
    )

# ── Elyon Nova ────────────────────────────────────────────────────────────

def ask_nova(messages):
    last_msg = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    return ask_gemini_with_keys(
        messages, GEMINI_NOVA_KEYS, "gemini-2.5-flash",
        thinking=True, use_search=use_search
    )

# ── Elyon PRO ─────────────────────────────────────────────────────────────

def ask_pro(messages):
    last_msg = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    return ask_gemini_with_keys(
        messages, GEMINI_PRO_KEYS, "gemini-2.5-flash",
        thinking=True, use_search=use_search
    )

# ── Elyon Absolution ──────────────────────────────────────────────────────

def ask_absolution(messages):
    last_msg = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    return ask_gemini_with_keys(
        messages, GEMINI_ABS_KEYS, "gemini-2.5-pro",
        thinking=True, use_search=use_search
    )

# ── Обратная совместимость ────────────────────────────────────────────────

def ask_gemini(messages):
    """Алиас — обратная совместимость для старых вызовов."""
    return ask_nova(messages)

# ── Запрос с файлом ───────────────────────────────────────────────────────

def ask_with_file(file_bytes, mime_type, file_name, user_prompt, history,
                  use_pro=False, model_tier="core"):
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
        # Core — используем Nova ключи без thinking
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
                        file_uri=file_obj.uri, mime_type=mime_type
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
                model=model, contents=contents, config=cfg
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

# ── Проверка статуса ключей ───────────────────────────────────────────────

def check_keys_status():
    results = {"core_nova": [], "nova": [], "pro": [], "absolution": []}

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

    for k in GEMINI_NOVA_KEYS: results["core_nova"].append(test_gemini(k))
    for k in GEMINI_NOVA_KEYS: results["nova"].append(test_gemini(k))
    for k in GEMINI_PRO_KEYS:  results["pro"].append(test_gemini(k))
    for k in GEMINI_ABS_KEYS:  results["absolution"].append(test_gemini(k))
    return results
