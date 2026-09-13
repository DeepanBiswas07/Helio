import os

# Load .env before reading any of it. Without this, importing the agent from
# anywhere except ui/app.py fell through to the defaults below — and those
# used to name two models that have since been retired, so every call failed
# and limped to the local fallback.
from core.env import load_env
load_env()

# Defaults are the models measured working and fastest on the free tier
# (gpt-oss:120b answered a routing prompt in 2.0s; nemotron-3-nano took 3.1s).
# .env still wins.
FAST_MODEL = os.getenv("FAST_MODEL", "gpt-oss:120b-cloud")
HEAVY_MODEL = os.getenv("HEAVY_MODEL", "gpt-oss:120b-cloud")

# Multimodal model used for "what's on my screen?". Kept separate from the
# role table because vision is the one capability the local fallback cannot
# cover — llama3:8b has no image input. Swapping this to a local vision
# model (e.g. qwen2.5vl:3b) is a one-line .env change, no code edit.
VISION_MODEL = os.getenv("VISION_MODEL", "gemma4:cloud")

ROLE_TO_MODEL = {
    # FAST TASKS (qwen3-next:80b-cloud)
    "chat": FAST_MODEL,
    "routing": FAST_MODEL,
    "memory": FAST_MODEL,
    "ui_interactions": FAST_MODEL,
    "reasoning": FAST_MODEL,

    # HEAVY TASKS (qwen3-coder-next:cloud)
    "planning": HEAVY_MODEL,
    "code": HEAVY_MODEL,
    "architecture": HEAVY_MODEL,
    "task_decomposition": HEAVY_MODEL,
    "structured_json": HEAVY_MODEL,
    "debugging": HEAVY_MODEL,
}

def model_for_role(role, default=HEAVY_MODEL):
    """Returns the precise model for a given cognitive role."""
    return ROLE_TO_MODEL.get(role, default)

def mode_for_role(role, default="smart"):
    """Legacy compatibility function, keeping it just in case."""
    return default
