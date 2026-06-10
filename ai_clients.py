import sys
import io
import os
import random
import re
from google import genai
from google.genai import types as genai_types

# ══════════════════════════════════════════════════════════════════
# МОДЕЛИ:
#   Elyon Core        → gemini-2.5-flash (thinking_budget=0, быстрый/бесплатный)
#   Elyon Nova        → gemini-2.5-flash (thinking_budget=1024)
#   Elyon PRO         → gemini-2.5-flash (thinking_budget=8000)
#   Elyon Absolution  → gemini-2.5-flash (thinking_budget=16000)
#
# ВАЖНО по exhausted: убедись что GEMINI_PRO_KEY_1/2 и GEMINI_ABS_KEY_1/2
# созданы в ОТДЕЛЬНЫХ Google Cloud проектах от NOVA ключей.
# Один проект = одна квота. Разные проекты = разные квоты.
# ══════════════════════════════════════════════════════════════════

GEMINI_NOVA_KEYS = [
    os.environ.get("GEMINI_NOVA_KEY_1", ""),
    os.environ.get("GEMINI_NOVA_KEY_2", ""),
]
GEMINI_NOVA_KEYS = [k for k in GEMINI_NOVA_KEYS if k]

GEMINI_PRO_KEYS = [
    os.environ.get("GEMINI_PRO_KEY_1", ""),
    os.environ.get("GEMINI_PRO_KEY_2", ""),
]
GEMINI_PRO_KEYS = [k for k in GEMINI_PRO_KEYS if k]

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

GEMINI_FREE_KEYS = GEMINI_NOVA_KEYS

# ── Модели ────────────────────────────────────────────────────────
# Core использует flash без thinking — быстро и бесплатно
# gemini-2.5-flash-lite-preview-06-17 была удалена Google
MODEL_CORE       = "gemini-2.5-flash"
MODEL_NOVA       = "gemini-2.5-flash"
# gemini-2.5-pro требует Google Cloud Billing
# На бесплатном AI Studio — используем flash с большим thinking
# Для включения pro: замени на "gemini-2.5-pro"
MODEL_PRO        = "gemini-2.5-flash"
MODEL_ABSOLUTION = "gemini-2.5-flash"

MODEL_PRO_FALLBACK = "gemini-2.5-flash"

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

def build_contents(messages):
    contents = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(genai_types.Content(
            role=role,
            parts=[genai_types.Part(text=str(msg["content"]))]
        ))
    return contents

SEARCH_TRIGGERS = [
    r'\b(кто такой|who is|who are|расскажи о|tell me about)\b',
    r'\b(сейчас|сегодня|now|today|current|latest|новости|news|2024|2025|2026)\b',
    r'\b(цена|price|курс|rate|стоимость|cost)\b',
    r'\b(president|президент|minister|министр|ceo|founder|основатель)\b',
    r'\b(правда ли|is it true|fact|факт|реально|really)\b',
]

def needs_search(text):
    return any(re.search(p, text.lower(), re.IGNORECASE) for p in SEARCH_TRIGGERS)

def ask_gemini_with_keys(messages, keys, model, thinking_budget=0,
                          use_search=False, fallback_model=None):
    """
    thinking_budget: 0=нет (Core), 1024=Nova, 8000=PRO, 16000=Absolution
    fallback_model: если основная модель exhausted — пробуем fallback
    """
    if not keys:
        raise Exception("NO_KEYS: ключи не настроены в переменных окружения")

    shuffled = keys.copy()
    random.shuffle(shuffled)
    all_exhausted = True

    for key in shuffled:
        try:
            client = genai.Client(api_key=key)
            cfg = genai_types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
            if thinking_budget > 0:
                cfg.thinking_config = genai_types.ThinkingConfig(thinking_budget=thinking_budget)
            if use_search:
                cfg.tools = [genai_types.Tool(google_search=genai_types.GoogleSearch())]

            response = client.models.generate_content(
                model=model, contents=build_contents(messages), config=cfg
            )
            return safe_text(response.text)

        except Exception as e:
            err = str(e)
            print(f"Key error ({model}): {err[:120]}")
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                continue
            if "503" in err or "UNAVAILABLE" in err:
                continue
            all_exhausted = False
            raise

    # Все ключи exhausted — пробуем fallback модель
    if fallback_model and fallback_model != model:
        print(f"All keys exhausted for {model}, trying fallback {fallback_model}")
        shuffled2 = keys.copy()
        random.shuffle(shuffled2)
        for key in shuffled2:
            try:
                client = genai.Client(api_key=key)
                cfg = genai_types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
                if thinking_budget > 0:
                    cfg.thinking_config = genai_types.ThinkingConfig(thinking_budget=min(thinking_budget, 8000))
                response = client.models.generate_content(
                    model=fallback_model,
                    contents=build_contents(messages),
                    config=cfg
                )
                return safe_text(response.text)
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    continue
                raise

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")


def ask_gpt(messages):
    """Elyon Core — gemini-2.5-flash без thinking. 15 сообщений/день."""
    last = messages[-1]["content"] if messages else ""
    keys = GEMINI_NOVA_KEYS or GEMINI_PRO_KEYS or GEMINI_ABS_KEYS
    if not keys:
        raise Exception("NO_KEYS: настрой GEMINI_NOVA_KEY_1 в переменных окружения")
    return ask_gemini_with_keys(
        messages, keys, MODEL_CORE,
        thinking_budget=0,
        use_search=needs_search(last),
        fallback_model=None
    )

def ask_nova(messages):
    """Elyon Nova — gemini-2.5-flash с thinking. 30 сообщений/день."""
    last = messages[-1]["content"] if messages else ""
    return ask_gemini_with_keys(
        messages, GEMINI_NOVA_KEYS, MODEL_NOVA,
        thinking_budget=1024,
        use_search=needs_search(last)
    )

def ask_pro(messages):
    """Elyon PRO — gemini-2.5-flash с высоким thinking. 30 сообщений/день."""
    last = messages[-1]["content"] if messages else ""
    return ask_gemini_with_keys(
        messages, GEMINI_PRO_KEYS, MODEL_PRO,
        thinking_budget=8000,
        use_search=needs_search(last),
        fallback_model=MODEL_PRO_FALLBACK
    )

def ask_absolution(messages):
    """Elyon Absolution — gemini-2.5-flash max thinking. 30 сообщений/день."""
    last = messages[-1]["content"] if messages else ""
    keys = GEMINI_ABS_KEYS or GEMINI_PRO_KEYS
    return ask_gemini_with_keys(
        messages, keys, MODEL_ABSOLUTION,
        thinking_budget=16000,
        use_search=needs_search(last),
        fallback_model=MODEL_PRO_FALLBACK
    )

def ask_gemini(messages):
    """Алиас — обратная совместимость."""
    return ask_nova(messages)

def ask_with_file(file_bytes, mime_type, file_name, user_prompt, history,
                  use_pro=False, model_tier="core"):
    tier_cfg = {
        "core":       (GEMINI_NOVA_KEYS or GEMINI_PRO_KEYS, MODEL_CORE,        0),
        "nova":       (GEMINI_NOVA_KEYS,                     MODEL_NOVA,       1024),
        "pro":        (GEMINI_PRO_KEYS,                      MODEL_PRO,        8000),
        "absolution": (GEMINI_ABS_KEYS or GEMINI_PRO_KEYS,   MODEL_ABSOLUTION, 16000),
    }
    keys, model, thinking_budget = tier_cfg.get(model_tier, tier_cfg["core"])
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
                    role=role, parts=[genai_types.Part(text=str(msg["content"]))]
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
            cfg = genai_types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
            if thinking_budget > 0:
                cfg.thinking_config = genai_types.ThinkingConfig(thinking_budget=thinking_budget)
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

def check_keys_status():
    results = {"core_flash": [], "nova_flash": [], "pro": [], "absolution": []}
    def test(key, model):
        if not key:
            return {"key": "(empty)", "status": "NOT SET — check Render env vars"}
        key_preview = key[:8] + "..." + key[-4:]
        try:
            client = genai.Client(api_key=key)
            client.models.generate_content(
                model=model,
                contents=[genai_types.Content(role="user", parts=[genai_types.Part(text="Hi")])],
                config=genai_types.GenerateContentConfig()
            )
            return {"key": key_preview, "status": "ok"}
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                detail = "no billing" if "billing" in err.lower() else "quota exceeded"
                return {"key": key_preview, "status": f"exhausted ({detail})"}
            elif "401" in err or "API_KEY_INVALID" in err:
                return {"key": key_preview, "status": "invalid key"}
            elif "503" in err or "UNAVAILABLE" in err:
                return {"key": key_preview, "status": "unavailable"}
            elif "404" in err or "not found" in err.lower():
                return {"key": key_preview, "status": f"model not found: {model}"}
            return {"key": key_preview, "status": f"error: {err[:80]}"}

    for k in GEMINI_NOVA_KEYS: results["core_flash"].append(test(k, MODEL_CORE))
    for k in GEMINI_NOVA_KEYS: results["nova_flash"].append(test(k, MODEL_NOVA))
    for k in GEMINI_PRO_KEYS:  results["pro"].append(test(k, MODEL_PRO))
    for k in (GEMINI_ABS_KEYS or GEMINI_PRO_KEYS): results["absolution"].append(test(k, MODEL_ABSOLUTION))
    return results
