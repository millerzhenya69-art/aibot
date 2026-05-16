import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
import os
import random
import time
import re
from google import genai
from google.genai import types as genai_types
from openai import OpenAI

# ── Ключи ─────────────────────────────────────────────────────────────────

OPENAI_KEYS = [
    os.environ.get("OPENAI_API_KEY_1", ""),
    os.environ.get("OPENAI_API_KEY_2", ""),
    os.environ.get("OPENAI_API_KEY_3", ""),
]
OPENAI_KEYS = [k for k in OPENAI_KEYS if k]

GEMINI_KEYS = [
    os.environ.get("GEMINI_API_KEY_1", ""),
    os.environ.get("GEMINI_API_KEY_2", ""),
    os.environ.get("GEMINI_API_KEY_3", ""),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

# ── Системный промпт — скрываем происхождение ─────────────────────────────

SYSTEM_PROMPT = (
    "You are Elyon AI — a smart and helpful assistant. "
    "Your name is Elyon. Never mention that you are ChatGPT, GPT, OpenAI, Gemini, Google, "
    "or any other AI system. If asked who you are or what your name is, "
    "always say you are Elyon AI, created by the Elyon team. "
    "Be friendly, concise, and helpful."
)

# Слова для замены в ответах (на случай если модель всё равно упомянет себя)
REPLACE_PAIRS = [
    (r'\bChatGPT\b', 'Elyon'),
    (r'\bGPT-4[^\s]*', 'Elyon'),
    (r'\bGPT\b', 'Elyon'),
    (r'\bOpenAI\b', 'Elyon team'),
    (r'\bGemini\b', 'Elyon'),
    (r'\bGoogle DeepMind\b', 'Elyon team'),
    (r'\bGoogle\b', 'Elyon team'),
    (r'\bI am an AI (made|created|developed|trained) by (OpenAI|Google)[^.]*\.',
     'I am Elyon AI, created by the Elyon team.'),
]

def mask_identity(text):
    """Заменяем упоминания реальных AI на Elyon."""
    for pattern, replacement in REPLACE_PAIRS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text

def safe_text(text):
    if text is None:
        return "Пустой ответ от AI."
    text = text.encode("utf-8", errors="ignore").decode("utf-8")
    return mask_identity(text)


# ── Построение сообщений ──────────────────────────────────────────────────

def build_openai_messages(messages):
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


# ── Бесплатная модель: OpenAI ─────────────────────────────────────────────

def ask_gpt(messages):
    """Бесплатная модель: сначала Gemini flash-lite, при исчерпании — OpenAI резерв."""
    # Сначала пробуем Gemini (бесплатно)
    if GEMINI_KEYS:
        keys = GEMINI_KEYS.copy()
        random.shuffle(keys)
        last_gemini_error = None
        for key in keys:
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model="gemini-2.0-flash-lite",
                    contents=build_gemini_contents(messages),
                    config=genai_types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT
                    )
                )
                return safe_text(response.text)
            except Exception as e:
                error_text = str(e)
                print(f"Gemini free ключ не сработал: {error_text[:100]}")
                last_gemini_error = error_text
                if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                    continue
                raise

    # Gemini исчерпан — пробуем OpenAI резерв
    if OPENAI_KEYS:
        keys = OPENAI_KEYS.copy()
        random.shuffle(keys)
        last_error = None
        for key in keys:
            try:
                client = OpenAI(api_key=key)
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=build_openai_messages(messages),
                    max_tokens=1000
                )
                return safe_text(response.choices[0].message.content)
            except Exception as e:
                error_text = str(e)
                print(f"OpenAI резерв не сработал: {error_text[:100]}")
                last_error = error_text
                if "429" in error_text or "quota" in error_text.lower() or "rate" in error_text.lower():
                    continue
                raise
        raise Exception(f"Все ключи исчерпаны. Попробуй через минуту.")

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")


# ── Платная модель: Gemini (думающая) ─────────────────────────────────────

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
                continue  # пробуем следующий ключ
            raise  # другая ошибка — сразу пробрасываем

    raise Exception(f"Все Gemini ключи исчерпаны. Попробуй через минуту.\n{last_error[:200]}")
