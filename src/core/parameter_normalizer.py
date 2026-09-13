import json

from core.llm import generate

NORMALIZABLE_KEYS = ("query", "target", "mode", "path", "root", "app")

# Every other parameter name any tool actually declares (see tools/*/*.py @tool
# decorators). Keep in sync with routing/intent_detector.py::PARAMETER_KEYS.
# Anything outside this set is dropped rather than passed through unchecked —
# an LLM cleanup pass that over-generates should not be able to inject arbitrary
# keys (e.g. an echoed-back "parameters"/"intent"/"confidence") into a tool call.
EXTRA_ALLOWED_KEYS = (
    "fact", "category", "name", "steps", "purpose", "title", "summary", "topics",
    "when", "message", "which", "question",
    "what", "kind", "duration", "repeats", "range", "choice", "skipped", "context",
)


def clean_text(value):
    if isinstance(value, list):
        value = ", ".join(str(item).strip() for item in value if str(item).strip())

    if not isinstance(value, str):
        return ""

    return " ".join(value.strip().split())


def normalize_index(value):
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None

    return index if index > 0 else None


def normalize_parameters_basic(parameters):
    if not isinstance(parameters, dict):
        return {}

    cleaned = {}
    for key, value in parameters.items():
        if key == "index":
            index = normalize_index(value)
            if index is not None:
                cleaned[key] = index
            continue

        if key in NORMALIZABLE_KEYS or key in EXTRA_ALLOWED_KEYS or key == "action":
            text = clean_text(value)
            if text:
                cleaned[key] = text
            continue

    return cleaned


def normalize_parameters_semantic(user_query, intent_data, tool_schema=None):
    intent_data = intent_data if isinstance(intent_data, dict) else {}
    parameters = normalize_parameters_basic(intent_data.get("parameters", {}))

    if intent_data.get("intent") == "chat":
        return parameters

    prompt = f"""
You normalize parameters for a local AI assistant.

Return ONLY JSON:
{{"parameters": {{}}}}

Rules:
- Remove conversational filler from query-like parameters.
- Preserve names, entities, file types, app names, and paths.
- Do not invent missing values.
- Do not change action names.
- Normalize app names to the common app name.
- Normalize file queries without removing useful file-type words.

Examples:
Input query: "search me latest ai news"
Output parameters: {{"query":"latest ai news"}}

Input query: "open youtube and search funny cats"
Output parameters: {{"query":"funny cats","app":"youtube"}}

Input query: "find my resume pdf"
Output parameters: {{"query":"resume pdf"}}

User query:
{user_query}

Intent:
{json.dumps(intent_data, indent=2)}

Tool schema:
{json.dumps(tool_schema or {}, indent=2)}
"""

    try:
        response = generate(prompt, role="structured_json")
        start = response.find("{")
        end = response.rfind("}")
        if start == -1 or end < start:
            return parameters

        data = json.loads(response[start:end + 1])
        normalized = normalize_parameters_basic(data.get("parameters", {}))
    except Exception:
        return parameters

    merged = parameters.copy()
    merged.update({
        key: value for key, value in normalized.items()
        if value not in ("", None)
    })
    return merged
