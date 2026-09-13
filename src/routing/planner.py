"""
planner.py — turning a request into steps, and adapting when a step fails.

Three things changed here from the first version, each fixing something real:

*Parameters are no longer dropped.* clean_step used to keep only query / app /
path / root / index. Every other parameter was silently stripped, so of the 38
registered tools most could not be planned at all — ask_documents lost its
question, add_event lost its date, read_webpage lost its URL. Allowed keys now
come from each tool's own schema, so a new tool is plannable the moment it is
registered.

*Failure is information.* plan_task takes what already went wrong and what has
already been tried, and says so in the prompt. Without that the planner would
propose the same broken step forever, which is why the old loop simply stopped
on the first failure.

*Work that branches can fan out.* A plan may return independent branches
instead of one sequence. The executor runs them concurrently and merges the
results, so "check my schedule and look up the weather" doesn't serialise two
unrelated waits.
"""
import json

from core.llm import generate
from tools.tool_registry import get_tool_schemas

# Parameters every step may carry regardless of the tool, plus the planner's
# own bookkeeping.
_ALWAYS_ALLOWED = ("query", "path", "root", "index")
_INT_PARAMS = ("index", "duration")


def extract_json(response):
    try:
        start = response.find("{")
        end = response.rfind("}")

        if start != -1 and end >= start:
            return json.loads(response[start:end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    # Some models return a bare JSON array of steps instead of the requested
    # {"steps": [...]} wrapper — normalize it rather than silently returning no plan.
    try:
        start = response.find("[")
        end = response.rfind("]")

        if start != -1 and end >= start:
            parsed = json.loads(response[start:end + 1])
            if isinstance(parsed, list):
                return {"steps": parsed}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    return None


def clean_step(step):
    """
    Keep a step only if it names a real tool, and keep every parameter that
    tool actually declares.

    The allowed keys are read from the live registry rather than hardcoded.
    A hardcoded list is what caused most of this project's silent-failure bugs:
    the tool exists, the router picks it, and the one parameter it needs is
    dropped on the way in.
    """
    if not isinstance(step, dict):
        return None

    schemas = get_tool_schemas()
    action = step.get("action")
    if action not in schemas:
        return None

    allowed = set(_ALWAYS_ALLOWED) | set(schemas[action].get("parameters", {}) or {})
    cleaned = {"action": action}

    for key in allowed:
        if key not in step:
            continue
        value = step[key]

        if key in _INT_PARAMS:
            try:
                cleaned[key] = int(value)
            except (TypeError, ValueError):
                # A required int that won't parse makes the step unusable.
                if key in schemas[action].get("required", []):
                    return None
            continue

        if isinstance(value, (int, float, bool)):
            cleaned[key] = value
        elif isinstance(value, list):
            joined = ", ".join(str(v).strip() for v in value if str(v).strip())
            if joined:
                cleaned[key] = joined
        elif isinstance(value, str) and value.strip():
            cleaned[key] = value.strip()

    # A step missing a required parameter will fail at execution; drop it here
    # so the planner gets told and can try something else.
    for key in schemas[action].get("required", []):
        if key not in cleaned:
            return None

    return cleaned


def sanitize_plan(plan):
    """
    Normalise into {"steps": [...], "branches": [[...], ...]}.

    Branches are independent sequences. Anything the model returns that isn't a
    usable step is dropped rather than passed through — an unknown action would
    fail at execution and waste a replan cycle.
    """
    if not isinstance(plan, dict):
        return {"steps": [], "branches": []}

    steps = [s for s in (clean_step(x) for x in plan.get("steps", []) or []) if s]

    branches = []
    for raw in plan.get("branches", []) or []:
        if not isinstance(raw, list):
            continue
        branch = [s for s in (clean_step(x) for x in raw) if s]
        if branch:
            branches.append(branch)

    # A single branch is just a sequence; don't spin up a thread for it.
    if len(branches) == 1 and not steps:
        steps, branches = branches[0], []

    return {"steps": steps, "branches": branches}


def _failure_block(failure, attempted):
    """What the planner needs to know so it doesn't repeat itself."""
    if not failure and not attempted:
        return ""

    lines = ["", "IMPORTANT — a previous attempt did not work:"]
    if failure:
        lines.append(f"- The step {json.dumps(failure.get('step', {}))} failed.")
        detail = str(failure.get("error", "")).strip()
        if detail:
            lines.append(f"- What came back: {detail[:400]}")
    if attempted:
        lines.append("- Already tried (do not repeat these verbatim): "
                     + json.dumps(attempted[-6:]))
    lines.append("- Plan a DIFFERENT approach. If the request cannot be done "
                 "with the available tools, return an empty steps list rather "
                 "than guessing.")
    return "\n".join(lines)


def _made_block():
    """
    What Helio has built lately, with real filenames.

    "The page", "that picture", "the site you built" are references to these.
    Without them the planner cannot resolve the reference and falls back to
    searching the whole machine for something it made itself.
    """
    from pathlib import Path as _Path

    lines = []
    try:
        from tools.system.forge_tool import list_forge_history, _picture_shelf
        for record in list_forge_history()[:6]:
            path = record.get("file_path", "")
            if not path:
                continue
            lines.append('- {} [{}] path: {}'.format(
                record.get("title", "untitled"),
                record.get("type", "build"), path))
        for picture in _picture_shelf()[:5]:
            lines.append(f"- picture [image] path: {picture}")
    except Exception:
        return ""

    if not lines:
        return ""
    return (
        "\nThings Helio has already made (newest first). When the request "
        "refers to 'the page', 'that picture', 'the site you built' or "
        "similar, it means one of these — use the path directly and do NOT "
        "plan a search_files step to go looking for it:\n"
        + "\n".join(lines) + "\n")


def plan_task(user_query, execution_context=None, failure=None, attempted=None):
    tool_schema = json.dumps(get_tool_schemas(), indent=2, sort_keys=True)
    context_text = json.dumps(execution_context or {}, indent=2)

    prompt = f"""
You are a careful task planner for Helio, a local desktop assistant.

Break the user request into safe tool steps.

Available tool schema:
{tool_schema}

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
- Include EVERY parameter the tool's schema marks as required. A step missing a required parameter is discarded.
- If the request contains two or more INDEPENDENT tasks that do not need each
  other's output (for example "what's on my schedule and look up the weather"),
  return them as separate entries in "branches" instead of one long "steps"
  list. They will run at the same time. Use "steps" when each step depends on
  the one before it.

Return this shape (use "steps" OR "branches", not both):
{{
  "steps": [
    {{"action": "...", "query": "..."}}
  ]
}}

or, for independent work:
{{
  "branches": [
    [{{"action": "...", "..." : "..."}}],
    [{{"action": "...", "..." : "..."}}]
  ]
}}

Execution context:
{context_text}
{_made_block()}{_failure_block(failure, attempted)}

User: {user_query}
"""
    response = generate(prompt, role="planning")
    return sanitize_plan(extract_json(response))


def verify_outcome(user_query, outputs):
    """
    Did the plan actually answer the request?

    Cheap critic: one short call comparing the request against what came back.
    Returns (satisfied: bool, reason: str). On any doubt it returns True —
    a false "not satisfied" would send the agent replanning a job it already
    finished, which is worse than letting a mediocre answer stand.
    """
    joined = "\n\n".join(str(o) for o in outputs if o).strip()
    if not joined:
        return False, "nothing came back"

    prompt = (
        "A desktop assistant was asked to do something and produced the result "
        "below. Decide whether the result actually addresses the request.\n\n"
        f"Request: {user_query}\n\n"
        f"Result:\n{joined[:2000]}\n\n"
        "The result is what the assistant's tools reported after running. A "
        "line like 'Built X', 'Wrote X', 'Found X', 'Saved X' or 'Put it on "
        "the workshop' is a tool confirming something it actually did and a "
        "file that now exists — treat it as done, not as a promise. You are "
        "not being shown the file itself.\n\n"
        "Answer with exactly one line:\n"
        "OK — if the result addresses the request, even partially.\n"
        "RETRY: <what is missing> — only if the result clearly fails to address "
        "it, is an error message, or answers a different question."
    )

    try:
        verdict = (generate(prompt, role="routing") or "").strip()
    except Exception:
        return True, ""

    if verdict.upper().startswith("RETRY"):
        reason = verdict.split(":", 1)[-1].strip() if ":" in verdict else verdict
        return False, reason[:200]
    return True, ""
