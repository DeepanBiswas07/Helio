RISKY_ACTIONS = {
    "open_app",
    "open_file_index",
}

CONFIRM_WORDS = {
    "yes", "y", "continue", "ok", "okay",
    "do it", "proceed", "go ahead"
}
CANCEL_WORDS = {"no", "n", "cancel", "stop", "abort", "never mind"}


def is_risky(action):
    return action in RISKY_ACTIONS


def is_confirmation(text):
    return text.lower().strip() in CONFIRM_WORDS


def is_cancellation(text):
    return text.lower().strip() in CANCEL_WORDS


def describe_step(step):
    action = step.get("action", "unknown")

    if action in ("read_file_index", "open_file_index"):
        return f"{action} #{step.get('index')}"

    if action in ("read_pdf", "read_file"):
        return f"{action} {step.get('path', '')}".strip()

    if action == "open_app":
        return f"open app: {step.get('app', '')}".strip()

    if step.get("query"):
        return f"{action}: {step.get('query')}"

    return action
