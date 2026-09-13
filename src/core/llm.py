import json
import requests
import time
from core.identity import build_chat_prompt
from core.model_roles import (
    mode_for_role, model_for_role, FAST_MODEL, HEAVY_MODEL, VISION_MODEL
)

import os
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")

LOCAL_MODEL = os.getenv("LOCAL_MODEL", "llama3:8b")  # Ultimate offline fallback

DEBUG = False
TIMEOUT = 60
STREAM_TIMEOUT = 240  # a whole page is a long generation
VISION_TIMEOUT = 120   # image uploads are slower than a text prompt
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


def generate_vision(prompt, images, model=None):
    """
    Ask a multimodal model about one or more images.

    `images` is a list of base64-encoded JPEG/PNG strings, matching Ollama's
    /api/generate `images` field.

    Unlike generate(), there is no local fallback: LOCAL_MODEL is text-only, so
    retrying there would just produce a confident hallucination about an image
    it never saw. When the cloud is unreachable we say so instead.
    """
    model = model or VISION_MODEL

    if not is_online():
        return (
            "I can't see your screen right now — screen vision runs on the cloud "
            "model, and I'm offline."
        )

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "images": images,
                "stream": False,
            },
            timeout=VISION_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()

    except Exception as e:
        if DEBUG:
            print(f"[LLM] Vision model failed: {e}")
        return f"LLM Error: {e}"


def _pick_model(mode, role, online):
    """Same selection generate() uses, pulled out so streaming shares it."""
    if not online:
        return LOCAL_MODEL
    if role:
        return model_for_role(role)
    return FAST_MODEL if mode == "fast" else HEAVY_MODEL


def generate_stream(prompt, mode="smart", role=None, timeout=None):
    """
    Yield the model's answer in fragments as it is produced.

    The Forge needs this: a page taking twenty seconds to write is dead air
    if you only see the result, and a build you can watch land line by line
    is the whole point of the panel. Falls back to the local model on
    failure, and finally yields whatever generate() returns so a caller can
    always finish with something rather than an empty stream.
    """
    online = is_online()
    model = _pick_model(mode, role, online)
    limit = timeout or STREAM_TIMEOUT

    for candidate in (model, LOCAL_MODEL):
        produced = False
        try:
            response = requests.post(
                OLLAMA_URL,
                json={"model": candidate, "prompt": prompt, "stream": True},
                stream=True,
                timeout=limit,
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    packet = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                fragment = packet.get("response", "")
                if fragment:
                    produced = True
                    yield fragment
                if packet.get("done"):
                    return
            if produced:
                return
        except Exception as e:
            if DEBUG:
                print(f"[LLM] Stream via {candidate} failed: {e}")
            # A model that already emitted text then died has produced a
            # partial answer; retrying the other one would duplicate it.
            if produced:
                return

    whole = generate(prompt, mode=mode, role=role)
    if whole:
        yield whole
