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
    "created by the Elyon team. Be friendly, concise, and helpful. "
    "When you use web search results, present the information naturally without "
    "mentioning that you searched the web or citing sources explicitly."
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
                # При 503 пробуем следующий ключ
                continue
            raise

    raise Exception("Лимит запросов исчерпан. Попробуй через минуту.")

# ── Бесплатная модель ─────────────────────────────────────────────────────

def ask_gpt(messages):
    last_msg = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    if use_search:
        print(f"[Core] Web search enabled for: {last_msg[:60]}")
    return ask_gemini_with_keys(
        messages, GEMINI_FREE_KEYS, "gemini-2.5-flash",
        thinking=False, use_search=use_search
    )

# ── Платная модель ────────────────────────────────────────────────────────

def ask_gemini(messages):
    last_msg = messages[-1]["content"] if messages else ""
    use_search = needs_search(last_msg)
    if use_search:
        print(f"[Nova] Web search enabled for: {last_msg[:60]}")
    return ask_gemini_with_keys(
        messages, GEMINI_PRO_KEYS, "gemini-2.5-flash",
        thinking=True, use_search=use_search
    )

# ── Запрос с файлом ───────────────────────────────────────────────────────

def ask_with_file(file_bytes, mime_type, file_name, user_prompt, history, use_pro=False):
    """Отправляет файл + текст в Gemini для анализа."""
    keys = GEMINI_PRO_KEYS if use_pro else GEMINI_FREE_KEYS
    model = "gemini-2.5-flash"

    if not keys:
        raise Exception("NO_KEYS: ключи не настроены")

    shuffled = keys.copy()
    random.shuffle(shuffled)

    for key in shuffled:
        try:
            client = genai.Client(api_key=key)

            # Загружаем файл через Files API
            file_obj = client.files.upload(
                file=io.BytesIO(file_bytes),
                config={"mime_type": mime_type, "display_name": file_name}
            )

            # Строим контент: история + файл + запрос
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
            if use_pro:
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
