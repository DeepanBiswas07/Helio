import json

from core.execution_context import get_context_value, record_tool_result
from core.identity import build_web_answer_prompt
from core.llm import generate
from core.parameter_normalizer import normalize_parameters_basic
from core.tool_result import from_legacy
from tools.tool_executor import execute_tool
from tools.tool_registry import get_tool_schemas
from tools.web_ops.web_search import search_web

MIN_CONFIDENCE = 0.55
INTENT_PRIORITY = {
    "tool": 90,
    "plan": 80,
    "file_search": 70,
    "answer": 60,
    "video": 50,
    "browser": 40,
    "chat": 10,
}
ACTIONABLE_INTENTS = {"tool", "plan", "file_search", "video", "browser"}
WEB_BACKED_INTENTS = {"answer", "browser"}


def clean_text(value):
    # Defensive: an upstream LLM cleanup pass can emit a list-shaped value
    # (e.g. "steps") even where a tool's declared schema expects a string.
    if isinstance(value, list):
        value = ", ".join(str(item).strip() for item in value if str(item).strip())
    if not isinstance(value, str):
        return ""
    return value.strip()


def clean_index(value):
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None

    if index < 1:
        return None

    return index


def resolve_references(value, context):
    if isinstance(value, str) and value.startswith("$"):
        resolved = get_context_value(context, value[1:])
        return resolved if resolved is not None else value

    if isinstance(value, dict):
        return {
            key: resolve_references(item, context)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            resolve_references(item, context)
            for item in value
        ]

    return value


def update_context_from_result(context, intent_data, result):
    normalized = from_legacy(result)
    return record_tool_result(context, intent_data, normalized.get("content", result))


def answer_with_web_knowledge(user_query, query, context=None):
    search_query = clean_text(query) or clean_text(user_query)
    if not search_query:
        return None

    results = search_web(search_query)

    if not results:
        return None

    context_text = "\n\n".join([
        f"{result.get('title', '')}\n{result.get('body', '')}"
        for result in results
    ])

    return generate(
        build_web_answer_prompt(user_query, context_text, context or {}),
        role="chat"
    )

def validate_tool_action(action_data):
    if not isinstance(action_data, dict):
        return None

    action_data = normalize_parameters_basic(action_data)
    schemas = get_tool_schemas()
    action = clean_text(action_data.get("action"))
    schema = schemas.get(action)

    if not schema:
        return None

    cleaned = {"action": action}
    parameter_schema = schema.get("parameters", {})

    for key, value_type in parameter_schema.items():
        value = action_data.get(key)

        if value_type == "integer":
            index = clean_index(value)
            if index is not None:
                cleaned[key] = index
            continue

        if value_type in ["object", "dict", "list"] or isinstance(value, (dict, list)):
            if value is not None:
                cleaned[key] = value
            continue

        text = clean_text(value)
        if text:
            cleaned[key] = text

    for key in schema.get("required", []):
        if key not in cleaned:
            return None

    return cleaned


def tool_action_from_intent(intent, parameters, user_query):
    parameters = parameters if isinstance(parameters, dict) else {}

    if intent == "browser":
        return {
            "action": "web_search",
            "query": clean_text(parameters.get("query")) or clean_text(user_query)
        }

    if intent == "video":
        return {
            "action": "youtube_search",
            "query": clean_text(parameters.get("query")) or clean_text(user_query)
        }

    if intent == "file_search":
        return {
            "action": "search_files",
            "query": clean_text(parameters.get("query")) or clean_text(user_query),
            "root": clean_text(parameters.get("root"))
        }

    if intent == "tool":
        action_data = {"action": clean_text(parameters.get("action"))}

        # Include standard string/text keys
        for key in (
            "query", "app", "path", "root", "mode", "target", "fact",
            "category", "name", "steps", "purpose", "title", "summary", "topics",
            "when", "message", "which", "question",
            "what", "kind", "duration", "repeats", "range", "choice", "skipped",
            "context",
            # Forge and maker keys:
            "chart_type", "x_label", "y_label", "html_code", "css_code", "js_code",
            "content", "file_type",
        ):
            text = clean_text(parameters.get(key))
            if text:
                action_data[key] = text

        # Preserve structured object/list parameters (e.g. data for forge_create_chart)
        for key in ("data", "options", "items", "metadata"):
            val = parameters.get(key)
            if isinstance(val, (dict, list)):
                action_data[key] = val

        if not action_data.get("query") and user_query:
            action_data["query"] = clean_text(user_query)

        if "index" in parameters:
            action_data["index"] = parameters.get("index")

        return action_data

    return None


def prioritize_intents(intents):
    if not isinstance(intents, list):
        return []

    if any(intent.get("depends_on") for intent in intents if isinstance(intent, dict)):
        return intents

    high_conf_chat = any(
        intent.get("intent") == "chat"
        and intent.get("confidence", 0.0) >= MIN_CONFIDENCE
        for intent in intents
        if isinstance(intent, dict)
    )
    has_actionable = any(
        intent.get("intent") in ACTIONABLE_INTENTS
        and intent.get("confidence", 0.0) >= MIN_CONFIDENCE
        for intent in intents
        if isinstance(intent, dict)
    )

    if high_conf_chat and not has_actionable:
        return [
            intent for intent in intents
            if intent.get("intent") not in WEB_BACKED_INTENTS
        ]

    indexed = list(enumerate(intents))
    indexed.sort(
        key=lambda item: (
            INTENT_PRIORITY.get(item[1].get("intent"), 0),
            item[1].get("confidence", 0.0)
        ),
        reverse=True
    )

    return [
        intent for _, intent in indexed
    ]


def resolve_intent(intent_data, context):
    if not isinstance(intent_data, dict):
        return {}

    resolved = intent_data.copy()
    resolved["parameters"] = resolve_references(
        resolved.get("parameters", {}),
        context
    )
    return resolved


def route_intent(intent_data, user_query, context=None):
    if not isinstance(intent_data, dict):
        return None

    context = context if isinstance(context, dict) else {}
    intent_data = resolve_intent(intent_data, context)

    intent = intent_data.get("intent")
    confidence = intent_data.get("confidence", 0.0)
    parameters = intent_data.get("parameters", {})

    if confidence < MIN_CONFIDENCE:
        return None

    if intent == "chat":
        return None

    if intent == "answer":
        return answer_with_web_knowledge(
            user_query,
            parameters.get("query", ""),
            context
        )

    action_data = tool_action_from_intent(intent, parameters, user_query)

    if isinstance(action_data, dict):
        if action_data.get("action") in ("read_file_index", "open_file_index"):
            if "index" not in action_data:
                last_results = context.get("file_search", {}).get(
                    "last_results",
                    context.get("last_file_results", [])
                )
                if last_results:
                    action_data["index"] = last_results[0]["index"]

    action_data = validate_tool_action(action_data)

    if not action_data:
        return None

    return from_legacy(execute_tool(action_data))


def diagnose_failure(user_query, intent_data, context):
    prompt = f"""
You are Helio's execution repair system.

The selected intent failed or returned no usable result.
Suggest one corrected intent using the available tool schema.

Return ONLY JSON with this shape:
{{"intent":"...","confidence":0.0,"parameters":{{}}}}

Available tool schema:
{json.dumps(get_tool_schemas(), indent=2)}

User request:
{user_query}

Failed intent:
{json.dumps(intent_data, indent=2)}

Execution context:
{json.dumps(context, indent=2)}
"""
    try:
        from routing.intent_detector import extract_json, normalize_intent
        return normalize_intent(extract_json(generate(prompt, role="debugging")))
    except Exception:
        return None


def route_intents(intents, user_query, context=None, retry=True):
    context = context if isinstance(context, dict) else {}
    results = []

    for intent_data in prioritize_intents(intents):
        result = route_intent(intent_data, user_query, context)

        if result is None and retry:
            repaired = diagnose_failure(user_query, intent_data, context)
            if repaired and repaired.get("confidence", 0.0) >= MIN_CONFIDENCE:
                result = route_intent(repaired, user_query, context)
                if result is not None:
                    intent_data = repaired

        if result is None:
            continue

        update_context_from_result(context, intent_data, result)
        results.append(result)

    return results
