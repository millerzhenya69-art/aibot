
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
import os
import random
import time
from google import genai
from google.genai import types as genai_types
from openai import OpenAI

# ── Gemini ключи (для обеих моделей) ──────────────────────────────────────
GEMINI_KEYS = [
    os.environ.get("GEMINI_API_KEY_1", ""),
    os.environ.get("GEMINI_API_KEY_2", ""),
    os.environ.get("GEMINI_API_KEY_3", ""),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

# ── OpenAI ключи (резерв для бесплатной модели) ───────────────────────────
OPENAI_KEYS = [
    os.environ.get("OPENAI_API_KEY_1", ""),
    os.environ.get("OPENAI_API_KEY_2", ""),
    os.environ.get("OPENAI_API_KEY_3", ""),
]
OPENAI_KEYS = [k for k in OPENAI_KEYS if k]


# ── Вспомогательные функции ───────────────────────────────────────────────

def get_gemini_client(attempt=0):
    if not GEMINI_KEYS:
        raise Exception("NO_KEYS: не настроены GEMINI_API_KEY переменные окружения")
    key = GEMINI_KEYS[attempt % len(GEMINI_KEYS)]
    return genai.Client(api_key=key)


def get_openai_client():
    if not OPENAI_KEYS:
        raise Exception("NO_KEYS: не настроены OPENAI_API_KEY переменные окружения")
    key = random.choice(OPENAI_KEYS)
    return OpenAI(api_key=key)


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


def build_openai_messages(messages):
    result = []
    for msg in messages:
        role = "assistant" if msg["role"] == "assistant" else "user"
        result.append({"role": role, "content": str(msg["content"])})
    return result


def safe_text(text):
    if text is None:
        return "Пустой ответ от AI."
    return text.encode("utf-8", errors="ignore").decode("utf-8")


# ── Бесплатная модель: Gemini → OpenAI fallback ───────────────────────────

def ask_gpt_via_openai(messages):
    """Резервный вызов через OpenAI GPT-4o-mini если все Gemini ключи исчерпаны."""
    print("Переключаемся на OpenAI fallback...")
    exhausted = []
    keys_to_try = OPENAI_KEYS.copy()
    random.shuffle(keys_to_try)

    for key in keys_to_try:
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
            if "429" in error_text or "quota" in error_text.lower() or "rate" in error_text.lower():
                print(f"OpenAI ключ исчерпан, пробуем следующий...")
                exhausted.append(key)
                continue
            raise

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")


def ask_gpt(messages, attempt=0):
    """Бесплатная модель: сначала Gemini flash-lite, при исчерпании — OpenAI."""
    # Пробуем все Gemini ключи
    if attempt < len(GEMINI_KEYS):
        try:
            client = get_gemini_client(attempt)
            response = client.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=build_gemini_contents(messages)
            )
            return safe_text(response.text)
        except Exception as e:
            error_text = str(e)
            if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                print(f"Gemini ключ {attempt + 1} исчерпан, пробуем следующий...")
                time.sleep(1)
                return ask_gpt(messages, attempt + 1)
            raise

    # Все Gemini ключи исчерпаны — переключаемся на OpenAI
    if OPENAI_KEYS:
        return ask_gpt_via_openai(messages)

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")


# ── Платная модель: только Gemini ─────────────────────────────────────────

def ask_gemini(messages, attempt=0):
    if attempt >= max(len(GEMINI_KEYS), 1):
        raise Exception("Все API ключи исчерпаны")

    try:
        client = get_gemini_client(attempt)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=build_gemini_contents(messages)
        )
        return safe_text(response.text)

    except Exception as e:
        error_text = str(e)
        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            print(f"Gemini ключ исчерпан, попытка {attempt + 1} из {len(GEMINI_KEYS)}")
            return ask_gemini(messages, attempt + 1)
        raise
