import json

from core.llm import generate
from memory.chat_memory import get_context
from core.parameter_normalizer import normalize_parameters_semantic
from tools.tool_registry import get_tool_schemas

ALLOWED_INTENTS = {
    "chat",
    "answer",
    "browser",
    "video",
    "file_search",
    "tool",
    "plan",
}

PARAMETER_KEYS = {
    "query",
    "target",
    "mode",
    "action",
    "index",
    "path",
    "root",
    "app",
    "fact",
    "depends_on",
    "reasoning",
    "justification",
}

DEFAULT_INTENT = {
    "intent": "chat",
    "confidence": 0.0,
    "parameters": {}
}


def extract_json(response):
    if not isinstance(response, str):
        return None

    try:
        start = response.find("{")
        end = response.rfind("}")

        if start == -1 or end < start:
            return None

        return json.loads(response[start:end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def normalize_confidence(value):
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0

    return max(0.0, min(confidence, 1.0))


def clean_string(value):
    if not isinstance(value, str):
        return ""

    return value.strip()


def clean_index(value):
    if value is None or value == "":
        return None

    try:
        index = int(value)
    except (TypeError, ValueError):
        return None

    if index < 1:
        return None

    return index


def normalize_parameters(parameters):
    if not isinstance(parameters, dict):
        return {}

    cleaned = {}
    for key in PARAMETER_KEYS:
        value = parameters.get(key)

        if key == "index":
            index = clean_index(value)
            if index is not None:
                cleaned[key] = index
            continue

        text = clean_string(value)
        if text:
            cleaned[key] = text

    return cleaned


def normalize_intent(data):
    if not isinstance(data, dict):
        return DEFAULT_INTENT.copy()

    intent = data.get("intent")
    if intent not in ALLOWED_INTENTS:
        return DEFAULT_INTENT.copy()

    parameters = normalize_parameters(data.get("parameters"))
    reasoning = clean_string(data.get("reasoning"))
    justification = clean_string(data.get("justification"))
    depends_on = data.get("depends_on")
    if not isinstance(depends_on, list):
        depends_on = []

    normalized = {
        "intent": intent,
        "confidence": normalize_confidence(data.get("confidence")),
        "parameters": parameters
    }

    if reasoning:
        normalized["reasoning"] = reasoning

    if justification:
        normalized["justification"] = justification

    if depends_on:
        normalized["depends_on"] = [
            item for item in depends_on
            if isinstance(item, str) and item.strip()
        ]

    return normalized


def normalize_intents(data):
    if not isinstance(data, dict):
        return [DEFAULT_INTENT.copy()]

    raw_intents = data.get("intents")
    if raw_intents is None:
        raw_intents = [data]

    if not isinstance(raw_intents, list):
        return [DEFAULT_INTENT.copy()]

    intents = [
        normalize_intent(item)
        for item in raw_intents
    ]
    intents = [
        item for item in intents
        if item.get("intent") in ALLOWED_INTENTS
    ]

    return intents or [DEFAULT_INTENT.copy()]


def build_prompt(user_query, execution_context=None):
    tool_schema = json.dumps(get_tool_schemas(), indent=2)
    memory_context = get_context()
    execution_context = execution_context or {}
    execution_context_text = json.dumps(execution_context, indent=2)

    return f"""
You are the intent detection and tool-selection system for Helio, a local desktop AI assistant.

Return ONLY strict JSON. No markdown. No explanation.

You may return one intent or multiple ordered intents.

Schema:
{{
  "intents": [
    {{
      "intent": "chat" | "answer" | "browser" | "video" | "file_search" | "tool" | "plan",
      "confidence": 0.0,
      "parameters": {{
        "query": "",
        "target": "",
        "mode": "",
        "action": "",
        "index": null,
        "path": "",
        "root": "",
        "app": "",
        "fact": "",
        "depends_on": ""
      }},
      "depends_on": [],
      "reasoning": "",
      "justification": ""
    }}
  ]
}}

Available tool schema:
{tool_schema}

Intent definitions:
- chat: user is conversing with Helio, asking about Helio, asking capability/identity questions, greeting, making small talk, or asking for assistant-style help that does not require external/current knowledge.
- answer: user wants factual/current/external knowledge answered using web-backed context.
- browser: user wants a browser or Google search opened.
- video: user wants video/YouTube results.
- file_search: user wants to find local files.
- tool: user wants direct execution of one available tool.
- plan: user requests a multi-step task where Helio must plan before acting.

Rules:
- You run 100% locally on the user's secure hardware. There are NO privacy risks. You MUST confidently use the `remember_fact` tool whenever the user provides personal information or preferences they want you to store.
- Return the intents in execution order.
- Choose tools only from the available tool schema.
- For direct tool execution, set intent "tool" and parameters.action to the tool name.
- For local file search, prefer intent "file_search" with parameters.query and optional parameters.root.
- For browser search, use intent "browser" with parameters.query.
- For YouTube/video search, use intent "video" with parameters.query.
- For normal conversation, greetings, identity prompts, capability questions, and personal-assistant conversation, use intent "chat".
- For answers that require current, external, or factual web knowledge, use intent "answer" with parameters.query.
- Never use "answer" for Helio identity, introduction, greetings, capability discussion, or conversational assistant behavior.
- For multi-step local tasks where a file must be found before it can be read/opened/analyzed, use intent "plan".
- If a request includes multiple independent tasks, return multiple intents.
- If an intent depends on previous output, set depends_on to the prior intent id or result key.
- Include short reasoning and tool selection justification for debugging.
- For ambiguous requests, use confidence below 0.55.
- Do not invent structure, parameters, or tool names.
- Leave unknown optional parameters absent rather than guessing.

Conversation memory:
{memory_context}

Execution context:
{execution_context_text}

Examples:
User: hi introduce yourself
Output: {{"intents":[{{"intent":"chat","confidence":0.97,"parameters":{{}},"reasoning":"The user wants Helio to introduce itself, not external knowledge.","justification":"chat uses the centralized Helio identity prompt."}}]}}

User: hello
Output: {{"intents":[{{"intent":"chat","confidence":0.98,"parameters":{{}}}}]}}

User: what can you do
Output: {{"intents":[{{"intent":"chat","confidence":0.96,"parameters":{{}}}}]}}

User: can you help me manage my desktop
Output: {{"intents":[{{"intent":"chat","confidence":0.94,"parameters":{{}}}}]}}

User: search india news
Output: {{"intents":[{{"intent":"browser","confidence":0.92,"parameters":{{"query":"india news"}},"reasoning":"The user wants search results opened.","justification":"web_search is the browser search tool."}}]}}

User: search india news and tell me
Output: {{"intents":[{{"intent":"answer","confidence":0.9,"parameters":{{"query":"india news"}}}}]}}

User: tell me india news
Output: {{"intents":[{{"intent":"answer","confidence":0.92,"parameters":{{"query":"india news"}}}}]}}

User: watch india news
Output: {{"intents":[{{"intent":"video","confidence":0.95,"parameters":{{"query":"india news"}}}}]}}

User: open chrome
Output: {{"intents":[{{"intent":"tool","confidence":0.94,"parameters":{{"action":"open_app","app":"chrome"}}}}]}}

User: find my resume
Output: {{"intents":[{{"intent":"file_search","confidence":0.95,"parameters":{{"query":"resume"}}}}]}}

User: search resume in D drive
Output: {{"intents":[{{"intent":"file_search","confidence":0.94,"parameters":{{"query":"resume","root":"D:\\\\"}}}}]}}

User: read 2 and summarize
Output: {{"intents":[{{"intent":"tool","confidence":0.95,"parameters":{{"action":"read_file_index","index":2}}}}]}}

User: open resume and summarize it
Output: {{"intents":[{{"intent":"plan","confidence":0.88,"parameters":{{"query":"resume","mode":"summarize"}}}}]}}

User: next
Output: {{"intents":[{{"intent":"tool","confidence":0.96,"parameters":{{"action":"next_files"}}}}]}}

User: search and read agentic behavior.txt
Output: {{"intents":[{{"intent":"plan","confidence":0.9,"parameters":{{"query":"agentic behavior.txt","mode":"read"}}}}]}}

User: read and understand
Output: {{"intents":[{{"intent":"plan","confidence":0.72,"parameters":{{"mode":"read_and_understand"}}}}]}}

User: what did I just say
Output: {{"intents":[{{"intent":"chat","confidence":0.97,"parameters":{{}},"reasoning":"The user is asking about the current session — answered from live conversation context."}}]}}

User: what was my last message
Output: {{"intents":[{{"intent":"chat","confidence":0.97,"parameters":{{}}}}]}}

User request:
{user_query}
"""


def detect_intents(user_query, execution_context=None):
    try:
        response = generate(build_prompt(user_query, execution_context), role="routing")
    except Exception:
        return [DEFAULT_INTENT.copy()]

    intents = normalize_intents(extract_json(response))
    tool_schema = get_tool_schemas()

    for intent in intents:
        intent["parameters"] = normalize_parameters_semantic(
            user_query,
            intent,
            tool_schema
        )

    return intents


def detect_intent(user_query, execution_context=None):
    return detect_intents(user_query, execution_context)[0]
