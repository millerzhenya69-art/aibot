import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
import os
import random
import re
from google import genai
from google.genai import types as genai_types
from openai import OpenAI

# ── Ключи ─────────────────────────────────────────────────────────────────

GROK_KEYS = [
    os.environ.get("GROK_API_KEY_1", ""),
    os.environ.get("GROK_API_KEY_2", ""),
    os.environ.get("GROK_API_KEY_3", ""),
]
GROK_KEYS = [k for k in GROK_KEYS if k]

GEMINI_KEYS = [
    os.environ.get("GEMINI_API_KEY_1", ""),
    os.environ.get("GEMINI_API_KEY_2", ""),
    os.environ.get("GEMINI_API_KEY_3", ""),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

# ── Системный промпт ───────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are Elyon AI — a smart and helpful assistant. "
    "Your name is Elyon. Never mention that you are Grok, xAI, ChatGPT, GPT, OpenAI, "
    "Gemini, Google, or any other AI system. "
    "If asked who you are or what your name is, always say you are Elyon AI, "
    "created by the Elyon team. Be friendly, concise, and helpful."
)

# ── Замена упоминаний реальных AI в ответах ───────────────────────────────

REPLACE_PAIRS = [
    (r'\bGrok\b', 'Elyon'),
    (r'\bxAI\b', 'Elyon team'),
    (r'\bChatGPT\b', 'Elyon'),
    (r'\bGPT-[^\s]*', 'Elyon'),
    (r'\bGPT\b', 'Elyon'),
    (r'\bOpenAI\b', 'Elyon team'),
    (r'\bGemini\b', 'Elyon'),
    (r'\bGoogle DeepMind\b', 'Elyon team'),
    (r'\bGoogle\b', 'Elyon team'),
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

def build_grok_messages(messages):
    result = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in messages:
        role = "assistant" if msg["role"] == "assistant" else "user"
        result.append({"role": role, "content": str(msg["content"])})
    return result

def build_gemini_contents(messages):
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

# ── Бесплатная модель: Grok ───────────────────────────────────────────────

def ask_gpt(messages):
    if not GROK_KEYS:
        raise Exception("NO_KEYS: не настроены GROK_API_KEY переменные окружения")

    keys = GROK_KEYS.copy()
    random.shuffle(keys)
    last_error = None

    for key in keys:
        try:
            client = OpenAI(api_key=key, base_url="https://api.x.ai/v1")
            response = client.chat.completions.create(
                model="grok-3-mini",
                messages=build_grok_messages(messages),
                max_tokens=1000
            )
            return safe_text(response.choices[0].message.content)
        except Exception as e:
            error_text = str(e)
            print(f"Grok ключ не сработал: {error_text[:100]}")
            last_error = error_text
            if "429" in error_text or "quota" in error_text.lower() or "rate" in error_text.lower():
                continue
            raise

    raise Exception(f"Все Grok ключи исчерпаны. Попробуй через минуту.")

# ── Платная модель: Gemini ────────────────────────────────────────────────

def ask_gemini(messages):
    if not GEMINI_KEYS:
        raise Exception("NO_KEYS: не настроены GEMINI_API_KEY переменные окружения")

    keys = GEMINI_KEYS.copy()
    random.shuffle(keys)
    last_error = None

    for key in keys:
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=build_gemini_contents(messages),
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                )
            )
            return safe_text(response.text)
        except Exception as e:
            error_text = str(e)
            print(f"Gemini ключ не сработал: {error_text[:100]}")
            last_error = error_text
            if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                continue
            raise

    raise Exception(f"Все Gemini ключи исчерпаны. Попробуй через минуту.")
