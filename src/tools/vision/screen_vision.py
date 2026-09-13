"""
"What's on my screen?" — capture the display and ask a multimodal model.

Note on privacy: this is the one Helio capability that leaves the machine. The
screenshot is sent to VISION_MODEL (a cloud model by default) because no local
model installed here can read an image. Every call logs what it is sending and
where, so it is never silent about it. Point VISION_MODEL at a local vision
model in .env and the traffic stops without touching this file.
"""
import base64
import io
import time

from tools.tool_registry import tool
from core.llm import generate_vision
from core.model_roles import VISION_MODEL

# 1280x720 at JPEG q70 lands around 130 KB of base64 — small enough to upload
# in well under a second, large enough that on-screen text stays legible.
MAX_SIZE = (1280, 720)
JPEG_QUALITY = 70

DEFAULT_QUESTION = (
    "Describe what is on this screen in two or three sentences. "
    "Name any application, window or website you can identify, and mention any "
    "error message or important text you can read."
)


def capture_screen():
    """
    Grab the primary display and return (base64_jpeg, width, height, seconds).
    Returns None if the screen could not be captured.
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return None

    start = time.time()
    try:
        image = ImageGrab.grab()
    except Exception as e:
        print(f"[Vision] Screen capture failed: {e}")
        return None

    width, height = image.width, image.height
    image.thumbnail(MAX_SIZE)

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
    encoded = base64.b64encode(buffer.getvalue()).decode()

    return encoded, width, height, time.time() - start


@tool(
    name="describe_screen",
    description=(
        "Look at the user's screen and describe or answer a question about what is "
        "displayed. Use this whenever the user asks 'what's on my screen', "
        "'what am I looking at', 'look at this', 'read this error for me', "
        "'what does this say', or refers to something visible on their display. "
        "'question' is what they want to know about the screen — leave it empty "
        "for a general description."
    ),
    parameters={"question": "string"},
    required=[],
)
def handle_describe_screen(action_data):
    question = str(action_data.get("question", "") or "").strip()

    captured = capture_screen()
    if captured is None:
        return "I couldn't capture your screen. Pillow may not be installed."

    encoded, width, height, elapsed = captured
    print(
        f"[Vision] Captured {width}x{height} in {elapsed:.2f}s — "
        f"sending {len(encoded) // 1024} KB to {VISION_MODEL}"
    )

    prompt = DEFAULT_QUESTION if not question else (
        f"Look at this screenshot of the user's screen and answer their question.\n"
        f"Question: {question}\n"
        f"Answer in two or three sentences, based only on what you can actually see."
    )

    answer = generate_vision(prompt, [encoded])

    if not answer:
        return "I looked at your screen but couldn't make anything out."
    if answer.startswith("LLM Error:"):
        return "I couldn't get a look at your screen — the vision model didn't respond."

    # Vision models sometimes narrate their own process; the user asked what is
    # on screen, not how the model went about looking.
    for prefix in ("Here is a description of the screen:", "Sure!", "Certainly!"):
        if answer.startswith(prefix):
            answer = answer[len(prefix):].strip()

    return answer
