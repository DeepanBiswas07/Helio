import re

from core.execution_context import create_execution_context
from core.identity import build_document_prompt
from routing.intent_detector import detect_intent, detect_intents
from routing.intent_router import (
    MIN_CONFIDENCE,
    prioritize_intents,
    route_intent,
    route_intents,
    update_context_from_result,
)
from core.llm import generate, generate_chat
from memory.chat_memory import add_message, get_context
from routing.planner import plan_task
from core.safety import describe_step, is_cancellation, is_confirmation, is_risky
from core.tool_result import from_legacy
from tools.file_ops.file_reader import is_valid_text
from tools.tool_executor import execute_tool

DEBUG = True
MAX_PLAN_ITERATIONS = 5

CURRENT_PLAN = []
PLAN_STEP = 0
WAITING_CONFIRMATION = False
PENDING_STEP = None
PENDING_INTENT = None
LAST_SEARCH_SINGLE = False
EXECUTION_CONTEXT = create_execution_context()


def remember(user_query, response):
    add_message("user", user_query)
    add_message("assistant", response)
    return response


def summarize_content(user_query, content, file_path=None):
    if not is_valid_text(content):
        return "I couldn't properly read the file. It may be scanned or unsupported."

    response = generate(
        build_document_prompt(user_query, content),
        role="chat"
    )

    # Auto-index the file into knowledge memory after summarizing
    if file_path:
        try:
            from memory.semantic_memory import index_file_knowledge
            index_file_knowledge(file_path, response[:400])
        except Exception:
            pass

    return response


def handle_tool_result(user_query, tool_result):
    normalized = from_legacy(tool_result)

    if normalized.get("type") == "llm_input":
        file_path = normalized.get("metadata", {}).get("path")
        return summarize_content(user_query, normalized.get("content", ""), file_path=file_path)

    content = normalized.get("content", "")
    if content:
        return content

    return None


def reset_plan():
    global CURRENT_PLAN, PLAN_STEP, WAITING_CONFIRMATION, PENDING_STEP
    global PENDING_INTENT, LAST_SEARCH_SINGLE

    CURRENT_PLAN = []
    PLAN_STEP = 0
    WAITING_CONFIRMATION = False
    PENDING_STEP = None
    PENDING_INTENT = None
    LAST_SEARCH_SINGLE = False


def confirmation_message(step):
    return f"This planned step needs confirmation: {describe_step(step)}. Continue?"


def is_single_search_result(result):
    if not result:
        return False

    result_text = from_legacy(result).get("content", result)
    if not isinstance(result_text, str):
        return False

    numbered_lines = [
        line for line in result_text.splitlines()
        if re.match(r"^\d+\.\s+", line.strip())
    ]

    return len(numbered_lines) == 1


def result_failed(result):
    if result is None:
        return False

    normalized = from_legacy(result)
    if normalized.get("status") == "error":
        return True

    content = normalized.get("content", "")
    return isinstance(content, str) and (
        "no files found" in content.lower()
        or "invalid" in content.lower()
        or "failed" in content.lower()
    )


def result_contains_file_list(result):
    content = from_legacy(result).get("content", "")
    return isinstance(content, str) and re.search(r"^\d+\.\s+", content, re.MULTILINE)


def execute_planned_step(user_query, step):
    global PLAN_STEP
    global EXECUTION_CONTEXT

    PLAN_STEP += 1

    tool_result = execute_tool(step)
    result = handle_tool_result(user_query, tool_result)

    update_context_from_result(
        EXECUTION_CONTEXT,
        {"intent": "plan", "parameters": step},
        tool_result
    )

    return result


def run_plan(user_query):
    global CURRENT_PLAN, PLAN_STEP, WAITING_CONFIRMATION, PENDING_STEP
    global EXECUTION_CONTEXT

    outputs = []
    iterations = 0

    while True:
        if iterations > MAX_PLAN_ITERATIONS:
            break
        iterations += 1

        last_result = EXECUTION_CONTEXT.get("last_result")
        if result_failed(last_result):
            break

        if PLAN_STEP >= len(CURRENT_PLAN):
            new_plan = plan_task(user_query, EXECUTION_CONTEXT)
            steps = new_plan.get("steps", [])

            if not steps or steps == CURRENT_PLAN:
                break

            CURRENT_PLAN = steps
            PLAN_STEP = 0

        step = CURRENT_PLAN[PLAN_STEP]

        if is_risky(step.get("action")):
            WAITING_CONFIRMATION = True
            PENDING_STEP = step
            outputs.append(confirmation_message(step))
            return remember(user_query, "\n\n".join(outputs))

        result = execute_planned_step(user_query, step)

        if result and step.get("action") != "search_files":
            outputs.append(result)

        if WAITING_CONFIRMATION:
            break

        last_result = EXECUTION_CONTEXT.get("last_result")
        if result_contains_file_list(last_result):
            if is_single_search_result(last_result):
                auto_step = {
                    "action": "read_file_index",
                    "index": 1
                }

                tool_result = execute_tool(auto_step)

                update_context_from_result(
                    EXECUTION_CONTEXT,
                    {"intent": "auto_read", "parameters": auto_step},
                    tool_result
                )

                result = handle_tool_result(user_query, tool_result)

                if result:
                    outputs.append(result)

                continue

            # General Multi-Step: If the planner has more steps queued up, let it execute them!
            if PLAN_STEP < len(CURRENT_PLAN):
                continue

            break

    executed_plan = CURRENT_PLAN.copy()
    reset_plan()

    if outputs:
        final_output = outputs[-1]

        # Reflection Layer: Adaptive Workflow Learning
        if len(executed_plan) > 1 and not result_failed(EXECUTION_CONTEXT.get("last_result")):
            EXECUTION_CONTEXT["last_successful_plan"] = executed_plan
            
            # Format the steps for the user
            step_desc = []
            for s in executed_plan:
                if "app" in s:
                    step_desc.append(f"open {s['app']}")
                elif "query" in s:
                    step_desc.append(f"search {s['query']}")
                else:
                    step_desc.append(s.get("action", str(s)))

            final_output += (
                "\n\n---\n"
                "**Reflection:**\n"
                f"I just executed a multi-step workflow: {', '.join(step_desc)}.\n"
                "Did this workflow succeed? Should I remember this sequence for the future?"
            )

        return remember(user_query, final_output)

    return None


def handle_confirmation(user_query):
    global WAITING_CONFIRMATION, PENDING_STEP
    global EXECUTION_CONTEXT

    if not WAITING_CONFIRMATION:
        return None

    intent_data = detect_intent(user_query, EXECUTION_CONTEXT)

    if intent_data.get("intent") == "tool":
        parameters = intent_data.get("parameters", {})
        action = parameters.get("action", "")

        if action in ("read_file_index", "open_file_index") and parameters.get("index") is not None:
            reset_plan()

            result = route_intent(intent_data, user_query, EXECUTION_CONTEXT)
            return handle_tool_result(user_query, result)

    if is_cancellation(user_query):
        reset_plan()
        return remember(user_query, "Okay, cancelled.")

    if not is_confirmation(user_query):
        return remember(user_query, "Please say yes to continue, or no to cancel.")

    if PENDING_STEP is None:
        return remember(user_query, "Please choose, open, or read a result number.")

    step = PENDING_STEP
    WAITING_CONFIRMATION = False
    PENDING_STEP = None

    result = execute_planned_step(user_query, step)

    if result:
        return result

    return remember(user_query, "Done.")


def maybe_start_plan(user_query, intent_data):
    global CURRENT_PLAN, PLAN_STEP
    global PENDING_INTENT, LAST_SEARCH_SINGLE

    plan = plan_task(user_query, EXECUTION_CONTEXT)
    steps = plan.get("steps", [])

    if not steps:
        return None

    CURRENT_PLAN = steps
    PLAN_STEP = 0

    result = run_plan(user_query)

    LAST_SEARCH_SINGLE = is_single_search_result(result)

    if LAST_SEARCH_SINGLE:
        auto_step = {
            "action": "read_file_index",
            "index": 1
        }

        tool_result = execute_tool(auto_step)

        update_context_from_result(
            EXECUTION_CONTEXT,
            {"intent": "auto_read", "parameters": auto_step},
            tool_result
        )

        return handle_tool_result(user_query, tool_result)

    return result


def fallback_chat(user_query):
    memory_context = get_context()

    try:
        from memory.semantic_memory import CATEGORIES, retrieve_memories, list_workflows, search_conversations

        # Flat category memories (facts, preferences, execution)
        flat_categories = [c for c in CATEGORIES if c != "workflows"]
        sections = []
        for cat in flat_categories:
            entries = retrieve_memories(user_query, category=cat)
            if entries:
                label = cat.capitalize()
                lines = "\n".join(f"  - {e}" for e in entries)
                sections.append(f"{label}:\n{lines}")

        # Structured workflows
        workflows = list_workflows()
        if workflows:
            wf_lines = []
            for name, data in workflows.items():
                steps = data.get("steps", [])
                purpose = data.get("purpose", "")
                step_str = " → ".join(steps)
                purpose_str = f" ({purpose})" if purpose else ""
                wf_lines.append(f"  • {name}{purpose_str}: {step_str}")
            sections.append("Workflows:\n" + "\n".join(wf_lines))

        # Past conversations — only surfaced when relevant to current query
        past = search_conversations(user_query, top_k=3)
        if past:
            conv_lines = []
            for c in past:
                ts = c.get("timestamp", "")
                title = c.get("title", "")
                summary = c.get("summary", "")
                ts_str = f" [{ts}]" if ts else ""
                conv_lines.append(f"  • {title}{ts_str}: {summary}")
            sections.append("Past Conversations:\n" + "\n".join(conv_lines))

        # File knowledge — surface matching indexed documents
        from memory.semantic_memory import search_file_knowledge
        file_hits = search_file_knowledge(user_query, top_k=3)
        if file_hits:
            fk_lines = []
            for fk in file_hits:
                fname = fk.get("filename", "")
                topics = fk.get("topics", [])
                summary = fk.get("summary", "")[:150]
                topic_str = f" [{', '.join(topics)}]" if topics else ""
                fk_lines.append(f"  • {fname}{topic_str}: {summary}")
            sections.append("Indexed Files:\n" + "\n".join(fk_lines))

        if sections:
            memory_context += "\n\nLong-Term Memory:\n" + "\n".join(sections)
    except Exception:
        pass

    return generate_chat(user_query, memory_context)




def handle_intent_result(user_query, result):
    handled = handle_tool_result(user_query, result)

    if handled:
        return handled

    return result if isinstance(result, str) and result else None


def execute_intents(user_query, intents):
    global EXECUTION_CONTEXT

    try:
        from memory.semantic_memory import retrieve_memories
        memories = retrieve_memories(user_query)
    except Exception:
        memories = []

    EXECUTION_CONTEXT["session"]["user_query"] = user_query
    EXECUTION_CONTEXT["session"]["semantic_memories"] = memories

    
    prioritized = prioritize_intents(intents)
    
    # If a plan is confidently detected, execute ONLY the plan to avoid double-execution
    for intent_data in prioritized:
        if intent_data.get("intent") == "plan" and intent_data.get("confidence", 0.0) >= MIN_CONFIDENCE:
            result = maybe_start_plan(user_query, intent_data)
            return handle_intent_result(user_query, result)

    outputs = []

    for intent_data in prioritized:
        if DEBUG:
            print(f"[INTENT] {intent_data.get('intent')} (confidence: {intent_data.get('confidence')})")

        if intent_data.get("confidence", 0.0) < MIN_CONFIDENCE:
            continue

        if intent_data.get("intent") == "chat":
            return fallback_chat(user_query)

        routed_results = route_intents([intent_data], user_query, EXECUTION_CONTEXT)
        result = routed_results[-1] if routed_results else None

        handled = handle_intent_result(user_query, result)

        if handled:
            outputs.append(handled)

    if outputs:
        return outputs[-1]

    return None


def clean_agent_query(query: str) -> str:
    """
    Cleans the agent query by removing leading wake words (like "Hey Helio", "A Helio", 
    "And Helio", "Okay Helio", "Helio") and returning a clean query with the first letter capitalized.
    """
    cleaned = re.sub(r"^(?:[a-zA-Z]{1,4}\b[\s,;!]*)*helio[\s,.;!]*", "", query, flags=re.IGNORECASE).strip()
    if cleaned:
        return cleaned[0].upper() + cleaned[1:]
    return query


def run_agent(user_query):
    # ── Clean Query: Strip leading wake words (e.g. "Hey Helio. What's the time?" -> "What's the time?") ──
    user_query = clean_agent_query(user_query)
    print(f"[Agent] Cleansed query for processing: \"{user_query}\"")

    confirmation_result = handle_confirmation(user_query)
    if confirmation_result:
        return confirmation_result

    from routing.task_classifier import classify_task
    from routing.fast_router import execute_fast_path

    task_class, extracted_data = classify_task(user_query)
    if task_class in ("SIMPLE_ACTION", "FILE_ACTION", "SEARCH_ACTION", "FILE_SEARCH_ACTION"):
        tool_result = execute_fast_path(task_class, extracted_data, user_query, EXECUTION_CONTEXT)
        if tool_result is not None:
            handled = handle_tool_result(user_query, tool_result)
            
            # Auto-read if only one file found
            if task_class == "FILE_SEARCH_ACTION" and not handled and result_contains_file_list(tool_result):
                if is_single_search_result(tool_result):
                    auto_step = {"action": "read_file_index", "index": 1}
                    read_result = execute_tool(auto_step)
                    update_context_from_result(EXECUTION_CONTEXT, {"intent": "auto_read", "parameters": auto_step}, read_result)
                    handled = handle_tool_result(user_query, read_result)
            elif task_class == "FILE_SEARCH_ACTION" and result_contains_file_list(tool_result):
                if is_single_search_result(tool_result):
                    auto_step = {"action": "read_file_index", "index": 1}
                    read_result = execute_tool(auto_step)
                    update_context_from_result(EXECUTION_CONTEXT, {"intent": "auto_read", "parameters": auto_step}, read_result)
                    handled = handle_tool_result(user_query, read_result)
            
            if not handled:
                handled = fallback_chat(user_query)
            return remember(user_query, handled)

    handled = execute_intents(user_query, detect_intents(user_query, EXECUTION_CONTEXT))
    if not handled:
        handled = fallback_chat(user_query)

    return remember(user_query, handled)


