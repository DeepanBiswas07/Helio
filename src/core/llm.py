import requests
import time
from core.identity import build_chat_prompt
from core.model_roles import mode_for_role, model_for_role, FAST_MODEL, HEAVY_MODEL

import os
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")

LOCAL_MODEL = os.getenv("LOCAL_MODEL", "llama3:8b")  # Ultimate offline fallback

DEBUG = False
TIMEOUT = 60
ONLINE_CHECK_TTL = 30
_ONLINE_CACHE = {
    "checked_at": 0,
    "is_online": False
}


def is_online():
    now = time.time()
    if now - _ONLINE_CACHE["checked_at"] < ONLINE_CHECK_TTL:
        return _ONLINE_CACHE["is_online"]

    try:
        requests.head("https://1.1.1.1", timeout=0.5)
        online = True
    except requests.RequestException:
        online = False

    _ONLINE_CACHE["checked_at"] = now
    _ONLINE_CACHE["is_online"] = online
    return online


def generate(prompt, mode="smart", role=None):
    online = is_online()

    if not online:
        # Immediate offline bypass to local fallback
        model = LOCAL_MODEL
    else:
        # Determine which cloud model to use
        if role:
            model = model_for_role(role)
        elif mode == "fast":
            model = FAST_MODEL
        else:
            model = HEAVY_MODEL

    if DEBUG:
        print(f"[LLM] Mode: {mode} | Online: {online} | Model: {model}")

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()
        return response.json().get("response", "").strip()

    except Exception as e:
        if DEBUG:
            print(f"[LLM] Primary model failed: {e}")

    try:
        if DEBUG:
            print("[LLM] Falling back to local model...")

        response = requests.post(
            OLLAMA_URL,
            json={
                "model": LOCAL_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()
        return response.json().get("response", "").strip()

    except Exception as e:
        return f"LLM Error: {e}"


def generate_chat(user_input, memory_context):
    return generate(
        build_chat_prompt(user_input, memory_context),
        role="chat"
    )
