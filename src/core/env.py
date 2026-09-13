"""
env.py — load .env wherever Helio is imported from.

Until now only ui/app.py read the .env file. Anything else that imported the
agent — a test, a script, a tool run from the console — got the hardcoded
fallbacks in model_roles instead, which were two models that have since been
retired. The calls failed, retried, and limped to the local model, turning a
2-second reply into fifty. It looked like Helio was slow; it was actually
talking to models that no longer exist.

Loading here, at import time, means every entry point sees the same config.
Real environment variables always win, so nothing here overrides a value the
user set in their shell.
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"

_LOADED = False


def load_env(path=None, override=False):
    """Read KEY=VALUE lines into os.environ. Safe to call repeatedly."""
    global _LOADED
    path = Path(path) if path else ENV_PATH

    if _LOADED and path == ENV_PATH and not override:
        return False
    if not path.exists():
        _LOADED = True
        return False

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if override or key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass

    _LOADED = True
    return True


# Import-time load: core.model_roles and core.llm read os.environ at module
# scope, so this has to happen before they do.
load_env()
