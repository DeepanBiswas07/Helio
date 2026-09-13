RISKY_ACTIONS = {
    # Opens something on the user's screen.
    "open_app",
    "open_file_index",
    # Destroys or overwrites something. A plan that reaches one of these has
    # inferred it rather than been told it, so it asks first — the list used
    # to cover only opening things, and nothing that throws work away.
    "forge_scrap",
    "forge_edit_file",
    "forget_my_activity",
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

    if action == "forge_scrap":
        which = str(step.get("which", "")).strip()
        if which.lower() in ("all", "everything"):
            return "scrap EVERYTHING Helio has built"
        return f"scrap the build '{which}'"

    if action == "forge_edit_file":
        return f"rewrite the file {step.get('path', '')}".strip()

    if action == "forget_my_activity":
        return "erase the whole activity log"

    if step.get("query"):
        return f"{action}: {step.get('query')}"

    return action
