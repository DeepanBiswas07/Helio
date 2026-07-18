from tools.tool_registry import tool
from memory.semantic_memory import CATEGORIES, save_memory

_category_list = ", ".join(CATEGORIES.keys())

@tool(
    name="remember_fact",
    description=(
        "Save an important fact, user preference, personal detail, or workflow to "
        "long-term semantic memory. MUST be a complete, descriptive sentence "
        "(e.g. 'The user\\'s name is Deepan', NOT just 'Deepan'). "
        f"Optionally specify a category: {_category_list}. "
        "If no category is given it is auto-detected."
    ),
    parameters={"fact": "string", "category": "string"},
    required=["fact"]
)
def handle_remember_fact(action_data):
    fact = action_data.get("fact", "").strip()
    category = action_data.get("category", "").strip().lower() or None

    if not fact:
        return "No fact provided to remember."

    save_memory(fact, category)
    cat_label = category if category in CATEGORIES else "auto-classified"
    return f"Committed to long-term memory [{cat_label}]: {fact}"
