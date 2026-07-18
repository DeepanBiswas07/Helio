import json
from core.llm import generate
from tools.tool_registry import get_tool_schemas

def extract_json(response):
    try:
        start = response.find("{")
        end = response.rfind("}")

        if start == -1 or end < start:
            return None

        return json.loads(response[start:end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def clean_step(step):
    if not isinstance(step, dict):
        return None

    action = step.get("action")
    if action not in get_tool_schemas():
        return None

    cleaned = {"action": action}

    for key in ("query", "app", "path", "root"):
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            cleaned[key] = value.strip()

    if action in ("open_file_index", "read_file_index"):
        try:
            cleaned["index"] = int(step.get("index"))
        except (TypeError, ValueError):
            return None

    return cleaned


def sanitize_plan(plan):
    if not isinstance(plan, dict):
        return {"steps": []}

    steps = []
    for step in plan.get("steps", []):
        cleaned = clean_step(step)
        if cleaned:
            steps.append(cleaned)

    return {"steps": steps}


def plan_task(user_query, execution_context=None):
    tool_schema = json.dumps(get_tool_schemas(), indent=2)
    context_text = json.dumps(execution_context or {}, indent=2)

    prompt = f"""
You are a careful task planner for Helio, a local desktop assistant.

Break the user request into safe tool steps.

Available tool schema:
{tool_schema}

Execution context:
{context_text}

Rules:
- Return ONLY JSON.
- If a task involves unknown local files, ALWAYS plan a `search_files` step first.
- A plan can have multiple steps chained together. Think logically about the sequence of tools required to solve the user's prompt.
- For example, if asked to find something and then act on it (open/read), you should chain `search_files` and then `open_file_index`/`read_file_index` in the same plan.
- If the user implies selecting the "best", "latest", or "first" result from a search, you may assume index 1 for the next step.
- When you use `read_file_index` or `read_file`, the system automatically analyzes the content to answer the user's query. You do not need to invent separate tools for extraction or summarization.
- Preserve useful location limits such as D drive or D:\\deepan as "root" for search.
- ALWAYS remove conversational phrases like "and summarize it", "and read it", "then open it" from your search queries.

- Extract ONLY the file-related keywords for search.

Return this shape:
{{
  "steps": [
    {{"action": "...", "query": "..."}}
  ]
}}

User: {user_query}
"""
    response = generate(prompt, role="planning")
    return sanitize_plan(extract_json(response))
