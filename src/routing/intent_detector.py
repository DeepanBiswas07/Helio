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

# Keep in sync with the per-tool "parameters" in tools/*/*.py (@tool decorators)
# and with the flat key list in routing/intent_router.py::tool_action_from_intent.
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
    "category",
    "name",
    "steps",
    "purpose",
    "title",
    "summary",
    "topics",
    "when",
    "message",
    "which",
    "question",
    "what",
    "kind",
    "duration",
    "repeats",
    "range",
    "choice",
    "skipped",
    "context",
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

        if start != -1 and end >= start:
            return json.loads(response[start:end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    # Some models return a bare JSON array of intents instead of the requested
    # {"intents": [...]} wrapper (this is common on multi-intent requests, where
    # slicing between the first "{" and last "}" spans multiple array elements
    # and produces invalid JSON). Normalize that shape rather than silently
    # falling back to a 0.0-confidence default.
    try:
        start = response.find("[")
        end = response.rfind("]")

        if start != -1 and end >= start:
            parsed = json.loads(response[start:end + 1])
            if isinstance(parsed, list):
                return {"intents": parsed}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

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

        # A few params (e.g. "steps") are naturally list-shaped when the model
        # emits them as a JSON array. Every tool that consumes them expects a
        # plain string (they split on commas themselves), so join rather than drop.
        if isinstance(value, list):
            joined = ", ".join(clean_string(item) for item in value if clean_string(item))
            if joined:
                cleaned[key] = joined
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
    # sort_keys so the block is byte-identical every call — the whole
    # point of putting it in the cacheable prefix.
    tool_schema = json.dumps(get_tool_schemas(), indent=2, sort_keys=True)
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
- For direct tool execution, set intent "tool" and parameters.action to the tool name. The "intent" field must ALWAYS be one of the 7 categories listed above — NEVER put a tool name (e.g. "get_datetime", "remember_fact") directly in the "intent" field. This applies to every utility tool, not just get_datetime.
- For local file search, prefer intent "file_search" with parameters.query and optional parameters.root.
- The tools "open_file_index" and "read_file_index" ONLY refer to a numbered list Helio has already shown in THIS conversation (check "Execution context" / "Conversation memory" for an actual prior numbered list before using them). If the user names a document (e.g. "open resume", "read my notes") and no such numbered list exists yet, use intent "file_search" instead — never guess index 1 or any other index.
- For browser search, use intent "browser" with parameters.query. The query should ONLY be the search term (e.g. "upwork"), not the conversational command (e.g. "search on google").
- For YouTube/video search, use intent "video" with parameters.query. The query should ONLY be the video subject.
- For normal conversation, greetings, identity prompts, capability questions, and personal-assistant conversation, use intent "chat".
- For answers that require current, external, or factual web knowledge, use intent "answer" with parameters.query.
- Never use "answer" for Helio identity, introduction, greetings, capability discussion, or conversational assistant behavior.
- For multi-step local tasks where a file must be found before it can be read/opened/analyzed, use intent "plan".
- If a request includes multiple independent tasks, return multiple intents.
- If an intent depends on previous output, set depends_on to the prior intent id or result key.
- Include short reasoning and tool selection justification for debugging.
- For ambiguous requests, use confidence below 0.55.
- For reminders and timers ("remind me...", "set a timer for...", "wake me in..."), use intent "tool" with parameters.action "set_reminder". Put the time phrase EXACTLY as the user said it in parameters.when (e.g. "in 20 minutes", "at 5pm", "tomorrow at 9am") and what to be reminded about in parameters.message. Never convert the time to a clock value yourself — pass the user's own words through.
- For questions about what is currently displayed ("what's on my screen", "what am I looking at", "read this error", "look at this"), use intent "tool" with parameters.action "describe_screen" and put what they want to know in parameters.question.
- For anything the user commits to at a time — a meeting, a class, an appointment, a birthday, a workout, a trip — use intent "tool" with parameters.action "add_event". Put the thing itself in parameters.what and the time phrase EXACTLY as the user said it in parameters.when. Set parameters.repeats to yearly for birthdays and anniversaries, weekdays/weekly/daily when they say so. Never convert the date yourself — pass their own words through.
- Reminders and the schedule are different tools. "remind me in 20 minutes" is set_reminder (a one-off alarm). "I have a dentist appointment on Friday" is add_event (a commitment with a date). If it has a calendar date or repeats, it is add_event.
- For reading the schedule back ("what's on today", "what does my week look like", "any birthdays coming up"), use parameters.action "list_schedule" with parameters.range of today/tomorrow/week/month.
- A yes/no question about ONE specific thing ("do I have a dentist appointment", "is the standup still on", "am I meeting Priya this week") is a lookup, not a listing: use intent "chat". The current state block already contains their schedule, so answer it directly with yes or no. Reserve list_schedule for requests to see the whole day, week or month.
- When the user has spare time and wants ideas ("I skipped the gym, what can I do", "I have an hour free", "what should I do now"), use parameters.action "suggest_activity" and put what they said in parameters.context.
- When they pick one of Helio's suggestions ("the second one", "do that"), use parameters.action "accept_suggestion" with parameters.choice.
- When they report having done or skipped something on the schedule ("I went to the gym", "I skipped my run"), use parameters.action "complete_event" with parameters.which, and parameters.skipped set to true if they missed it.
- When the user asks about something that would be INSIDE one of their own documents ("what did the contract say about payment terms", "what were the action items in my notes"), use parameters.action "ask_documents" with parameters.question. This searches the actual text of documents Helio has studied. Do not answer such questions from intent "chat" — chat cannot see document contents.
- "study this file" / "learn this document" means parameters.action "study_file" with parameters.path. That reads the whole document into searchable memory. It is different from index_file, which only stores a one-line summary.
- There are two web tools and they are not interchangeable. "search_and_read" fetches pages and ANSWERS from them — use it whenever the user wants to know something ('look up X', 'what's the latest on Y', 'how do I Z'). "web_search" only opens a browser tab and reads nothing — use it only when the user explicitly wants a browser opened ('google X', 'open a search for X').
- "read_webpage" takes a URL the user gave and answers from that page. Use it for 'summarise this link', 'what does this page say', or any message containing a URL they want read.
- For machine readouts — CPU, memory, disk space, battery, uptime — use parameters.action "get_system_info", optionally with parameters.what set to cpu/memory/disk/battery/uptime. Never answer these from chat; chat cannot see the machine.
- Do not invent structure, parameters, or tool names.
- Leave unknown optional parameters absent rather than guessing.


Examples:
User: hi introduce yourself
Output: {{"intents":[{{"intent":"chat","confidence":0.97,"parameters":{{}},"reasoning":"The user wants Helio to introduce itself, not external knowledge.","justification":"chat uses the centralized Helio identity prompt."}}]}}

User: hello
Output: {{"intents":[{{"intent":"chat","confidence":0.98,"parameters":{{}}}}]}}

User: what can you do
Output: {{"intents":[{{"intent":"chat","confidence":0.96,"parameters":{{}}}}]}}

User: can you help me manage my desktop
Output: {{"intents":[{{"intent":"chat","confidence":0.94,"parameters":{{}}}}]}}

User: what time is it
Output: {{"intents":[{{"intent":"tool","confidence":0.95,"parameters":{{"action":"get_datetime","query":"time"}},"reasoning":"The user wants the current time.","justification":"get_datetime is a direct utility tool, so intent stays \"tool\" with the tool name in parameters.action — never \"get_datetime\" itself."}}]}}

User: remember that I like dark mode
Output: {{"intents":[{{"intent":"tool","confidence":0.96,"parameters":{{"action":"remember_fact","fact":"The user likes dark mode."}},"reasoning":"The user is stating a preference to store.","justification":"remember_fact is a direct utility tool, so intent stays \"tool\", not \"remember_fact\"."}}]}}

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

User: open resume
Output: {{"intents":[{{"intent":"file_search","confidence":0.9,"parameters":{{"query":"resume"}},"reasoning":"No numbered file list exists yet in this conversation.","justification":"Without a prior search, \"resume\" cannot be a numbered-list index — file_search must run first."}}]}}

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

User: remind me in 20 minutes to check the oven
Output: {{"intents":[{{"intent":"tool","confidence":0.96,"parameters":{{"action":"set_reminder","when":"in 20 minutes","message":"check the oven"}},"reasoning":"The user wants to be reminded later.","justification":"set_reminder is a direct utility tool, so intent stays \"tool\" and the raw time phrase is passed through untouched."}}]}}

User: set a timer for 5 minutes
Output: {{"intents":[{{"intent":"tool","confidence":0.95,"parameters":{{"action":"set_reminder","when":"5 minutes"}}}}]}}

User: remind me at 6pm to call mom
Output: {{"intents":[{{"intent":"tool","confidence":0.95,"parameters":{{"action":"set_reminder","when":"at 6pm","message":"call mom"}}}}]}}

User: what reminders do I have
Output: {{"intents":[{{"intent":"tool","confidence":0.95,"parameters":{{"action":"list_reminders"}}}}]}}

User: cancel the oven reminder
Output: {{"intents":[{{"intent":"tool","confidence":0.93,"parameters":{{"action":"cancel_reminder","which":"oven"}}}}]}}

User: what's on my screen
Output: {{"intents":[{{"intent":"tool","confidence":0.96,"parameters":{{"action":"describe_screen"}},"reasoning":"The user is asking about their current display.","justification":"describe_screen captures the screen and reads it — intent stays \"tool\"."}}]}}

User: read this error for me
Output: {{"intents":[{{"intent":"tool","confidence":0.9,"parameters":{{"action":"describe_screen","question":"what is the error message shown"}}}}]}}

User: run my morning routine
Output: {{"intents":[{{"intent":"tool","confidence":0.94,"parameters":{{"action":"run_workflow","name":"morning routine"}}}}]}}

User: i have a dentist appointment on friday at 3
Output: {{"intents":[{{"intent":"tool","confidence":0.95,"parameters":{{"action":"add_event","what":"dentist appointment","when":"friday at 3","kind":"personal"}},"reasoning":"A commitment with a calendar date.","justification":"add_event, not set_reminder \u2014 it has a date rather than a countdown."}}]}}

User: arjun's birthday is on the 12th of march
Output: {{"intents":[{{"intent":"tool","confidence":0.96,"parameters":{{"action":"add_event","what":"Arjun's birthday","when":"12th of march","kind":"birthday","repeats":"yearly"}}}}]}}

User: i go to the gym at 7 every weekday
Output: {{"intents":[{{"intent":"tool","confidence":0.94,"parameters":{{"action":"add_event","what":"gym","when":"7am","kind":"workout","repeats":"weekdays","duration":60}}}}]}}

User: what does my week look like
Output: {{"intents":[{{"intent":"tool","confidence":0.95,"parameters":{{"action":"list_schedule","range":"week"}}}}]}}

User: do i have a dentist appointment booked
Output: {{"intents":[{{"intent":"chat","confidence":0.93,"parameters":{{}},"reasoning":"A yes/no lookup about one specific thing.","justification":"The current state block holds the schedule, so chat answers directly instead of listing the week."}}]}}

User: i didn't go to the gym today, what can i do instead
Output: {{"intents":[{{"intent":"tool","confidence":0.93,"parameters":{{"action":"suggest_activity","context":"skipped the gym, wants an alternative"}}}}]}}

User: the second one
Output: {{"intents":[{{"intent":"tool","confidence":0.9,"parameters":{{"action":"accept_suggestion","choice":"2"}}}}]}}

User: i went to the gym
Output: {{"intents":[{{"intent":"tool","confidence":0.92,"parameters":{{"action":"complete_event","which":"gym"}}}}]}}

User: what did the contract say about payment terms
Output: {{"intents":[{{"intent":"tool","confidence":0.95,"parameters":{{"action":"ask_documents","question":"what did the contract say about payment terms"}},"reasoning":"The answer lives inside a document the user owns.","justification":"ask_documents searches the real text; chat would answer from the model's weights."}}]}}

User: study D:\\notes\\meeting.docx
Output: {{"intents":[{{"intent":"tool","confidence":0.94,"parameters":{{"action":"study_file","path":"D:\\notes\\meeting.docx"}}}}]}}

User: look up what the model context protocol is
Output: {{"intents":[{{"intent":"tool","confidence":0.94,"parameters":{{"action":"search_and_read","query":"what the model context protocol is"}},"reasoning":"They want to know something, not to be handed a browser.","justification":"search_and_read fetches and answers; web_search would only open a tab."}}]}}

User: what's my cpu at
Output: {{"intents":[{{"intent":"tool","confidence":0.95,"parameters":{{"action":"get_system_info","what":"cpu"}}}}]}}

Conversation memory:
{memory_context}

Execution context:
{execution_context_text}

User request:
{user_query}
"""


def detect_intents(user_query, execution_context=None):
    prompt = build_prompt(user_query, execution_context)

    try:
        response = generate(prompt, role="routing")
    except Exception:
        return [DEFAULT_INTENT.copy()]

    parsed = extract_json(response)

    if parsed is None:
        # Smaller/local models occasionally return malformed JSON on a single
        # sample; one retry resolves this far more often than it costs, and is
        # cheap relative to silently degrading every such query to plain chat.
        try:
            response = generate(prompt, role="routing")
            parsed = extract_json(response)
        except Exception:
            parsed = None

    intents = normalize_intents(parsed)
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
