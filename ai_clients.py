import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
import os
import random
import re
from google import genai
from google.genai import types as genai_types

# ── Ключи ─────────────────────────────────────────────────────────────────

GEMINI_FREE_KEYS = [
    os.environ.get("GEMINI_FREE_KEY_1", ""),
    os.environ.get("GEMINI_FREE_KEY_2", ""),
    os.environ.get("GEMINI_FREE_KEY_3", ""),
]
GEMINI_FREE_KEYS = [k for k in GEMINI_FREE_KEYS if k]

GEMINI_PRO_KEYS = [
    os.environ.get("GEMINI_PRO_KEY_1", ""),
    os.environ.get("GEMINI_PRO_KEY_2", ""),
    os.environ.get("GEMINI_PRO_KEY_3", ""),
]
GEMINI_PRO_KEYS = [k for k in GEMINI_PRO_KEYS if k]

# ── Системный промпт ───────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are Elyon AI — a smart and helpful assistant. "
    "Your name is Elyon. Never mention that you are Gemini, Google, ChatGPT, GPT, "
    "OpenAI, Grok, xAI, or any other AI system. "
    "If asked who you are or what your name is, always say you are Elyon AI, "
    "created by the Elyon team. Be friendly, concise, and helpful."
)

# ── Замена упоминаний реальных AI в ответах ───────────────────────────────

REPLACE_PAIRS = [
    (r'\bGemini\b', 'Elyon'),
    (r'\bGoogle DeepMind\b', 'Elyon team'),
    (r'\bGoogle\b', 'Elyon team'),
    (r'\bChatGPT\b', 'Elyon'),
    (r'\bGPT-[^\s]*', 'Elyon'),
    (r'\bGPT\b', 'Elyon'),
    (r'\bOpenAI\b', 'Elyon team'),
    (r'\bGrok\b', 'Elyon'),
    (r'\bxAI\b', 'Elyon team'),
]

def mask_identity(text):
    for pattern, replacement in REPLACE_PAIRS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text

def safe_text(text):
    if text is None:
        return "Пустой ответ от AI."
    text = text.encode("utf-8", errors="ignore").decode("utf-8")
    return mask_identity(text)

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

# ── Общая функция запроса к Gemini ────────────────────────────────────────

def ask_gemini_with_keys(messages, keys, model):
    if not keys:
        raise Exception("NO_KEYS: ключи не настроены в переменных окружения")

    shuffled = keys.copy()
    random.shuffle(shuffled)
    last_error = None

    for key in shuffled:
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=model,
                contents=build_contents(messages),
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                )
            )
            return safe_text(response.text)
        except Exception as e:
            error_text = str(e)
            print(f"Ключ не сработал ({model}): {error_text[:120]}")
            last_error = error_text
            if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                continue
            raise

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")

# ── Бесплатная модель ─────────────────────────────────────────────────────

def ask_gpt(messages):
    return ask_gemini_with_keys(messages, GEMINI_FREE_KEYS, "gemini-2.0-flash-lite")

# ── Платная модель ────────────────────────────────────────────────────────

def ask_gemini(messages):
    return ask_gemini_with_keys(messages, GEMINI_PRO_KEYS, "gemini-2.5-pro")
