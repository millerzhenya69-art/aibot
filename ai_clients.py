import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
import os
import random
from google import genai
from google.genai import types as genai_types

GEMINI_KEYS = [
    os.environ.get("GEMINI_API_KEY_1", "сюда_ключ_1"),
    os.environ.get("GEMINI_API_KEY_2", "сюда_ключ_2"),
    os.environ.get("GEMINI_API_KEY_3", "сюда_ключ_3"),
]

GEMINI_KEYS = [k for k in GEMINI_KEYS if k and "сюда_ключ" not in k]


def get_client():
    key = random.choice(GEMINI_KEYS)
    return genai.Client(api_key=key)


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


def safe_text(text):
    # Принудительно кодируем и декодируем в utf-8 — убирает проблемы с кодировкой
    if text is None:
        return "Пустой ответ от AI."
    return text.encode("utf-8", errors="ignore").decode("utf-8")


def ask_gpt(messages, attempt=0):
    if attempt >= len(GEMINI_KEYS):
        raise Exception("Все API ключи исчерпаны")

    try:
        client = get_client()
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=build_contents(messages)
        )
        return safe_text(response.text)

    except Exception as e:
        error_text = str(e)
        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            print(f"Ключ исчерпан, попытка {attempt + 1} из {len(GEMINI_KEYS)}")
            return ask_gpt(messages, attempt + 1)
        raise


def ask_gemini(messages, attempt=0):
    if attempt >= len(GEMINI_KEYS):
        raise Exception("Все API ключи исчерпаны")

    try:
        client = get_client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=build_contents(messages)
        )
        return safe_text(response.text)

    except Exception as e:
        error_text = str(e)
        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            print(f"Ключ исчерпан, попытка {attempt + 1} из {len(GEMINI_KEYS)}")
            return ask_gemini(messages, attempt + 1)
        raise