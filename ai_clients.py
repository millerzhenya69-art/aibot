import sys
import io
import os
import random
import re
from google import genai
from google.genai import types as genai_types

# ══════════════════════════════════════════════════════════════════
# МОДЕЛИ:
#   Elyon Core        → gemini-2.5-flash-lite  (бесплатно, быстро)
#   Elyon Nova        → gemini-2.5-flash        (платно, thinking)
#   Elyon PRO         → gemini-2.5-pro           (платно, thinking)
#   Elyon Absolution  → gemini-2.5-pro           (платно, thinking+)
#
# ЛИМИТЫ (задаются в database.py):
#   Core        → 15 сообщений/день
#   Nova        → 30 сообщений/день
#   PRO         → 30 сообщений/день
#   Absolution  → 30 сообщений/день
# ══════════════════════════════════════════════════════════════════

# ── Ключи ─────────────────────────────────────────────────────────

# Nova ключи — используются и для Core (flash-lite), и для Nova (flash)
GEMINI_NOVA_KEYS = [
    os.environ.get("GEMINI_NOVA_KEY_1", ""),
    os.environ.get("GEMINI_NOVA_KEY_2", ""),
]
GEMINI_NOVA_KEYS = [k for k in GEMINI_NOVA_KEYS if k]

# PRO ключи — для PRO и Absolution (gemini-2.5-pro)
GEMINI_PRO_KEYS = [
    os.environ.get("GEMINI_PRO_KEY_1", ""),
    os.environ.get("GEMINI_PRO_KEY_2", ""),
]
GEMINI_PRO_KEYS = [k for k in GEMINI_PRO_KEYS if k]

# ABS ключи — для Absolution (если отдельные, иначе fallback на PRO)
GEMINI_ABS_KEYS = [
    os.environ.get("GEMINI_ABS_KEY_1", ""),
    os.environ.get("GEMINI_ABS_KEY_2", ""),
]
GEMINI_ABS_KEYS = [k for k in GEMINI_ABS_KEYS if k]

# Fallbacks
if not GEMINI_NOVA_KEYS:
    _fb = [os.environ.get(f"GEMINI_PRO_KEY_{i}", "") for i in range(1, 4)]
    GEMINI_NOVA_KEYS = [k for k in _fb if k]

if not GEMINI_PRO_KEYS and GEMINI_NOVA_KEYS:
    GEMINI_PRO_KEYS = GEMINI_NOVA_KEYS

if not GEMINI_ABS_KEYS and GEMINI_PRO_KEYS:
    GEMINI_ABS_KEYS = GEMINI_PRO_KEYS

# Обратная совместимость для bot.py
GEMINI_FREE_KEYS = GEMINI_NOVA_KEYS

# ── Названия моделей ──────────────────────────────────────────────
MODEL_CORE        = "gemini-2.5-flash-lite"   # Elyon Core — быстрая, бесплатная
MODEL_NOVA        = "gemini-2.5-flash"         # Elyon Nova
MODEL_PRO         = "gemini-2.5-pro"           # Elyon PRO
MODEL_ABSOLUTION  = "gemini-2.5-pro"           # Elyon Absolution (тот же pro, но макс. thinking)

# ── Системный промпт ──────────────────────────────────────────────

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

# ── Маскировка идентичности ───────────────────────────────────────

REPLACE_PAIRS = [
    (r'\bGemini\b',         'Elyon'),
    (r'\bGoogle DeepMind\b','the Elyon team'),
    (r'\bDeepSeek\b',       'Elyon'),
    (r'\bChatGPT\b',        'ChatGPT'),
    (r'\bGPT-[^\s]*',       'GPT'),
    (r'\bGPT\b',            'GPT'),
    (r'\bOpenAI\b',         'OpenAI'),
    (r'\bGrok\b',           'Grok'),
    (r'\bxAI\b',            'xAI'),
]

def mask_identity(text):
    for pattern, replacement in REPLACE_PAIRS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def clean_text(text):
    if not text:
        return text
    code_blocks = {}
    idx = [0]

    def save_code(m):
        key = f"\x00CODE{idx[0]}\x00"; idx[0] += 1
        code_blocks[key] = (m.group(1).strip(), m.group(2))
        return key

    text = re.sub(r'```(\w*)\n?([\s\S]*?)```', save_code, text)
    text = re.sub(r'`([^`\n]+)`',   r'\1', text)
    text = re.sub(r'\*{3}(.+?)\*{3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*{2}(.+?)\*{2}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*',       r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{3}(.+?)_{3}',   r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{2}(.+?)_{2}',   r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_',         r'\1', text, flags=re.DOTALL)
    text = re.sub(r'^#{1,6}\s+', '',  text, flags=re.MULTILINE)
    text = re.sub(r'^\*\s+',  '- ',   text, flags=re.MULTILINE)
    text = re.sub(r'^•\s+',   '- ',   text, flags=re.MULTILINE)
    text = re.sub(r'^-{3,}\s*$', '',  text, flags=re.MULTILINE)
    text = re.sub(r'^\*{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s+', '',        text, flags=re.MULTILINE)
    for key, (lang, code) in code_blocks.items():
        label = f"[{lang.upper()}]" if lang else "[КОД]"
        text = text.replace(key, f"{label}\n{code.strip()}")
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def safe_text(text):
    if text is None:
        return "Пустой ответ от AI."
    text = text.encode("utf-8", errors="ignore").decode("utf-8")
    text = mask_identity(text)
    text = clean_text(text)
    return text

# ── Построение истории сообщений ──────────────────────────────────

def build_contents(messages):
    contents = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(genai_types.Content(
            role=role,
            parts=[genai_types.Part(text=str(msg["content"]))]
        ))
    return contents

# ── Определяем нужен ли веб-поиск ────────────────────────────────

SEARCH_TRIGGERS = [
    r'\b(кто такой|who is|who are|расскажи о|tell me about)\b',
    r'\b(сейчас|сегодня|now|today|current|latest|новости|news|2024|2025|2026)\b',
    r'\b(цена|price|курс|rate|стоимость|cost)\b',
    r'\b(president|президент|minister|министр|ceo|founder|основатель)\b',
    r'\b(правда ли|is it true|fact|факт|реально|really)\b',
]

def needs_search(text):
    text_lower = text.lower()
    return any(re.search(p, text_lower, re.IGNORECASE) for p in SEARCH_TRIGGERS)

# ── Основная функция запроса к Gemini ─────────────────────────────

def ask_gemini_with_keys(messages, keys, model, thinking_budget=0, use_search=False):
    """
    thinking_budget:
        0     = thinking отключён (Core)
        1024  = лёгкое thinking (Nova)
        8000  = глубокое thinking (PRO)
        16000 = максимальное thinking (Absolution)
    """
    if not keys:
        raise Exception("NO_KEYS: ключи не настроены в переменных окружения")

    shuffled = keys.copy()
    random.shuffle(shuffled)

    for key in shuffled:
        try:
            client = genai.Client(api_key=key)
            cfg = genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT
            )
            if thinking_budget > 0:
                cfg.thinking_config = genai_types.ThinkingConfig(
                    thinking_budget=thinking_budget
                )
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
            err = str(e)
            print(f"Key error ({model}): {err[:120]}")
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                continue
            if "503" in err or "UNAVAILABLE" in err:
                continue
            raise

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")

# ══════════════════════════════════════════════════════════════════
# ПУБЛИЧНЫЕ ФУНКЦИИ — по одной на каждый тир
# ══════════════════════════════════════════════════════════════════

def ask_gpt(messages):
    """
    Elyon Core — gemini-2.5-flash-lite
    Быстро, бесплатно, без thinking. Лимит: 15 сообщений/день.
    """
    last = messages[-1]["content"] if messages else ""
    keys = GEMINI_NOVA_KEYS or GEMINI_PRO_KEYS or GEMINI_ABS_KEYS
    if not keys:
        raise Exception("NO_KEYS: настрой GEMINI_NOVA_KEY_1 в переменных окружения")
    return ask_gemini_with_keys(
        messages, keys, MODEL_CORE,
        thinking_budget=0,
        use_search=needs_search(last)
    )


def ask_nova(messages):
    """
    Elyon Nova — gemini-2.5-flash с thinking.
    Лимит: 30 сообщений/день.
    """
    last = messages[-1]["content"] if messages else ""
    return ask_gemini_with_keys(
        messages, GEMINI_NOVA_KEYS, MODEL_NOVA,
        thinking_budget=1024,
        use_search=needs_search(last)
    )


def ask_pro(messages):
    """
    Elyon PRO — gemini-2.5-pro с глубоким thinking.
    Лимит: 30 сообщений/день.
    """
    last = messages[-1]["content"] if messages else ""
    return ask_gemini_with_keys(
        messages, GEMINI_PRO_KEYS, MODEL_PRO,
        thinking_budget=8000,
        use_search=needs_search(last)
    )


def ask_absolution(messages):
    """
    Elyon Absolution — gemini-2.5-pro с максимальным thinking.
    Лимит: 30 сообщений/день.
    """
    last = messages[-1]["content"] if messages else ""
    keys = GEMINI_ABS_KEYS or GEMINI_PRO_KEYS
    return ask_gemini_with_keys(
        messages, keys, MODEL_ABSOLUTION,
        thinking_budget=16000,
        use_search=needs_search(last)
    )


def ask_gemini(messages):
    """Алиас — обратная совместимость для старых вызовов в bot.py."""
    return ask_nova(messages)

# ── Запрос с файлом ───────────────────────────────────────────────

def ask_with_file(file_bytes, mime_type, file_name, user_prompt, history,
                  use_pro=False, model_tier="core"):
    """
    Отправляет файл + текст в Gemini.
    model_tier: 'core' | 'nova' | 'pro' | 'absolution'
    """
    tier_cfg = {
        "core":       (GEMINI_NOVA_KEYS or GEMINI_PRO_KEYS, MODEL_CORE,       0),
        "nova":       (GEMINI_NOVA_KEYS,                      MODEL_NOVA,      1024),
        "pro":        (GEMINI_PRO_KEYS,                       MODEL_PRO,       8000),
        "absolution": (GEMINI_ABS_KEYS or GEMINI_PRO_KEYS,    MODEL_ABSOLUTION,16000),
    }
    keys, model, thinking_budget = tier_cfg.get(model_tier, tier_cfg["core"])

    # use_pro override (обратная совместимость)
    if use_pro and model_tier == "core":
        keys = GEMINI_NOVA_KEYS; model = MODEL_NOVA; thinking_budget = 1024

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
            if thinking_budget > 0:
                cfg.thinking_config = genai_types.ThinkingConfig(
                    thinking_budget=thinking_budget
                )

            response = client.models.generate_content(
                model=model, contents=contents, config=cfg
            )
            try:
                client.files.delete(name=file_obj.name)
            except:
                pass
            return safe_text(response.text)

        except Exception as e:
            err = str(e)
            print(f"ask_with_file error: {err[:120]}")
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                continue
            if "503" in err or "UNAVAILABLE" in err:
                continue
            raise

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")

# ── Статус ключей ─────────────────────────────────────────────────

def check_keys_status():
    results = {"core_flash_lite": [], "nova_flash": [], "pro": [], "absolution": []}

    def test(key, model):
        try:
            client = genai.Client(api_key=key)
            client.models.generate_content(
                model=model,
                contents=[genai_types.Content(role="user", parts=[genai_types.Part(text="Hi")])],
                config=genai_types.GenerateContentConfig()
            )
            return {"key": key[:8]+"...", "status": "ok"}
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                return {"key": key[:8]+"...", "status": "exhausted"}
            elif "401" in err or "API_KEY_INVALID" in err:
                return {"key": key[:8]+"...", "status": "invalid"}
            elif "503" in err:
                return {"key": key[:8]+"...", "status": "unavailable"}
            return {"key": key[:8]+"...", "status": f"error: {err[:40]}"}

    for k in GEMINI_NOVA_KEYS: results["core_flash_lite"].append(test(k, MODEL_CORE))
    for k in GEMINI_NOVA_KEYS: results["nova_flash"].append(test(k, MODEL_NOVA))
    for k in GEMINI_PRO_KEYS:  results["pro"].append(test(k, MODEL_PRO))
    for k in (GEMINI_ABS_KEYS or GEMINI_PRO_KEYS): results["absolution"].append(test(k, MODEL_ABSOLUTION))
    return results
