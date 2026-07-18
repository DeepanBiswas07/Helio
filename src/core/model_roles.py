import os
FAST_MODEL = os.getenv("FAST_MODEL", "qwen3-next:80b-cloud")
HEAVY_MODEL = os.getenv("HEAVY_MODEL", "qwen3-coder-next:cloud")

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
