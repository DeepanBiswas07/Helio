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
from routing.planner import plan_task, verify_outcome
from core.safety import describe_step, is_cancellation, is_confirmation, is_risky
from core.tool_result import from_legacy
from tools.file_ops.file_reader import is_valid_text
from tools.tool_executor import execute_tool

DEBUG = True
MAX_PLAN_ITERATIONS = 5
# How many times a failed plan may be reworked before giving up.
# Two is enough for 'that path was wrong, try the other one' and
# short enough that a genuinely impossible request fails quickly.
MAX_REPLANS = 2

CURRENT_PLAN = []
PLAN_STEP = 0
WAITING_CONFIRMATION = False
PENDING_STEP = None
PENDING_INTENT = None
LAST_SEARCH_SINGLE = False
LAST_TOOL_RESULT = None
# Set when the plan loop's critic has already passed the result, so
# the tail critic does not overturn a verdict just reached.
ALREADY_VERIFIED = False
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
    """
    Run one planned step. Returns the handled output, and records the raw tool
    result in LAST_TOOL_RESULT.

    The raw result is kept because the plan loop has to decide whether the step
    failed, and reading that back out of EXECUTION_CONTEXT was unreliable —
    update_context_from_result doesn't store every result shape, so a failing
    step could look like a clean one and the loop would never replan.
    """
    global PLAN_STEP
    global EXECUTION_CONTEXT
    global LAST_TOOL_RESULT

    PLAN_STEP += 1

    tool_result = execute_tool(step)
    LAST_TOOL_RESULT = tool_result
    result = handle_tool_result(user_query, tool_result)

    update_context_from_result(
        EXECUTION_CONTEXT,
        {"intent": "plan", "parameters": step},
        tool_result
    )

    return result


def run_branches(user_query, branches):
    """
    Run independent branches at the same time and return their outputs in order.

    "Sub-agents" for a desktop assistant means exactly this: two pieces of work
    that don't need each other's output shouldn't queue behind one another.
    Each branch gets its own thread and its own execution context, so a search
    in one can't clobber the numbered file list the other is reading from.

    Risky steps are never run this way — a confirmation gate can't ask two
    questions at once, so a branch containing one is refused here and left for
    the sequential path.
    """
    import concurrent.futures

    results = [None] * len(branches)

    def run_one(index, steps):
        local_context = create_execution_context()
        outputs = []
        for step in steps:
            try:
                tool_result = execute_tool(step)
                update_context_from_result(
                    local_context, {"intent": "branch", "parameters": step},
                    tool_result)
                produced = handle_tool_result(user_query, tool_result)
                if produced and step.get("action") != "search_files":
                    outputs.append(produced)
            except Exception as e:
                outputs.append(f"({step.get('action')} failed: {e})")
                break
        return index, outputs

    workers = min(len(branches), 4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one, i, steps)
                   for i, steps in enumerate(branches)]
        for future in concurrent.futures.as_completed(futures):
            try:
                index, outputs = future.result()
            except Exception as e:
                print(f"[Agent] Branch failed: {e}")
                continue
            results[index] = outputs

    merged = []
    for outputs in results:
        merged.extend(outputs or [])
    return merged


def run_plan(user_query):
    global CURRENT_PLAN, PLAN_STEP, WAITING_CONFIRMATION, PENDING_STEP
    global EXECUTION_CONTEXT, ALREADY_VERIFIED

    ALREADY_VERIFIED = False

    outputs = []
    attempted = []          # steps already tried, so a replan doesn't repeat them
    failure = None          # what went wrong last, fed back to the planner
    replans = 0
    iterations = 0

    while True:
        if iterations > MAX_PLAN_ITERATIONS:
            break
        iterations += 1

        if PLAN_STEP >= len(CURRENT_PLAN):
            # A finished plan that went wrong nowhere may already BE the
            # answer. Ask the critic before planning again — without this the
            # loop spent its whole budget re-solving a solved request, and the
            # extra passes could fire side effects (a browser tab) nobody
            # asked for.
            if outputs and failure is None and iterations > 1:
                done, why = verify_outcome(user_query, outputs)
                if done:
                    print("[Agent] Request already answered; stopping at "
                          f"{iterations - 1} pass"
                          + ("" if iterations == 2 else "es") + ".")
                    ALREADY_VERIFIED = True
                    break
                # Not satisfied: tell the planner what is still missing rather
                # than letting it guess the same route again.
                failure = {"step": attempted[-1] if attempted else {},
                           "error": why or "the result did not address the request"}

            new_plan = plan_task(user_query, EXECUTION_CONTEXT,
                                 failure=failure, attempted=attempted)

            # Independent work fans out instead of queueing.
            branches = new_plan.get("branches", [])
            if branches and not any(is_risky(step.get("action"))
                                    for branch in branches for step in branch):
                print(f"[Agent] Running {len(branches)} branches in parallel.")
                outputs.extend(run_branches(user_query, branches))
                break
            if branches:
                # A risky step can't run unattended; flatten and go sequential
                # so the confirmation gate still gets its chance to ask.
                new_plan = {"steps": [s for branch in branches for s in branch]}

            steps = new_plan.get("steps", [])
            if not steps or steps == CURRENT_PLAN:
                break

            CURRENT_PLAN = steps
            PLAN_STEP = 0
            # `failure` is deliberately NOT cleared here. One run_plan call is
            # one task, and a route that failed at the start is still a route
            # not to take at the end — clearing it let the planner circle back
            # to the approach it had already been told didn't work.

        step = CURRENT_PLAN[PLAN_STEP]

        if is_risky(step.get("action")):
            WAITING_CONFIRMATION = True
            PENDING_STEP = step
            outputs.append(confirmation_message(step))
            return remember(user_query, "\n\n".join(outputs))

        attempted.append(step)
        result = execute_planned_step(user_query, step)

        if result and step.get("action") != "search_files":
            outputs.append(result)

        if WAITING_CONFIRMATION:
            break

        # Judge the step by what it actually returned, not by what happened to
        # land in the execution context.
        last_result = LAST_TOOL_RESULT

        # A failed step used to end the plan silently, leaving the user with
        # whatever happened to have succeeded first. Hand the failure back to
        # the planner instead and let it try a different route.
        if result_failed(last_result):
            failure = {
                "step": step,
                "error": from_legacy(last_result).get("content", "")[:400],
            }
            if replans >= MAX_REPLANS:
                print("[Agent] Out of replan attempts.")
                break
            replans += 1
            print(f"[Agent] Step failed, replanning ({replans}/{MAX_REPLANS}).")
            CURRENT_PLAN = []
            PLAN_STEP = 0
            continue

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

    if not outputs:
        return None

    # A multi-step plan used to return only outputs[-1], throwing away
    # everything the earlier steps produced. Keep them all, in order, and drop
    # exact repeats so a retried step doesn't say the same thing twice.
    seen, kept = set(), []
    for text in outputs:
        marker = str(text).strip()
        if marker and marker not in seen:
            seen.add(marker)
            kept.append(str(text))

    # The critic. A plan can run every step without error and still answer the
    # wrong question — "did it work" and "did it help" are different checks.
    # One bounded second pass, then the answer stands either way; looping on a
    # critic that keeps saying no is how an agent burns a minute saying nothing.
    if kept and replans < MAX_REPLANS and not ALREADY_VERIFIED:
        satisfied, reason = verify_outcome(user_query, kept)
        if not satisfied:
            print(f"[Agent] Result doesn't address the request ({reason}). "
                  "One more pass.")
            retry = plan_task(
                user_query, EXECUTION_CONTEXT,
                failure={"step": attempted[-1] if attempted else {},
                         "error": reason},
                attempted=attempted)
            extra_steps = retry.get("steps") or [
                s for branch in retry.get("branches", []) for s in branch]
            for step in extra_steps[:3]:
                if is_risky(step.get("action")) or step in attempted:
                    continue
                produced = execute_planned_step(user_query, step)
                if produced and str(produced).strip() not in seen:
                    seen.add(str(produced).strip())
                    kept.append(str(produced))

    final_output = "\n\n".join(kept)

    if len(executed_plan) > 1 and not result_failed(EXECUTION_CONTEXT.get("last_result")):
        EXECUTION_CONTEXT["last_successful_plan"] = executed_plan

    return remember(user_query, final_output)


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


def auto_read_if_single_result(user_query, result):
    """
    If `result` is exactly one numbered local-file search hit, auto-read that
    file and return its content instead of the raw listing. Returns `result`
    unchanged otherwise (including when the read itself yields nothing usable).
    """
    if not result_contains_file_list(result) or not is_single_search_result(result):
        return result

    auto_step = {"action": "read_file_index", "index": 1}
    tool_result = execute_tool(auto_step)

    update_context_from_result(
        EXECUTION_CONTEXT,
        {"intent": "auto_read", "parameters": auto_step},
        tool_result
    )

    return handle_tool_result(user_query, tool_result)


def maybe_start_plan(user_query, intent_data):
    global CURRENT_PLAN, PLAN_STEP
    global PENDING_INTENT, LAST_SEARCH_SINGLE

    plan = plan_task(user_query, EXECUTION_CONTEXT)

    # Independent work comes back as branches, not steps. Reading only "steps"
    # made those plans look empty, so the whole plan path was skipped and the
    # request fell through to one-intent-at-a-time execution.
    branches = plan.get("branches", [])
    if branches and not any(is_risky(step.get("action"))
                            for branch in branches for step in branch):
        print(f"[Agent] Planning fanned out into {len(branches)} branches.")
        outputs = run_branches(user_query, branches)
        return "\n\n".join(str(o) for o in outputs if o) or None

    steps = plan.get("steps", []) or [s for branch in branches for s in branch]

    if not steps:
        return None

    CURRENT_PLAN = steps
    PLAN_STEP = 0

    result = run_plan(user_query)

    LAST_SEARCH_SINGLE = result_contains_file_list(result) and is_single_search_result(result)

    if LAST_SEARCH_SINGLE:
        return auto_read_if_single_result(user_query, result)

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

    reply = generate_chat(user_query, memory_context)

    # A connection traceback is not an answer. When the model can't be reached,
    # say so in a sentence — Helio's deterministic tools (schedule, reminders,
    # files, system) all still work without it, so point at that.
    if isinstance(reply, str) and reply.startswith("LLM Error:"):
        print(f"[Agent] {reply}")
        return (
            "I can't reach my language model right now — check that Ollama is "
            "running. Timers, your schedule, file search and system readouts "
            "still work in the meantime."
        )

    return reply




def handle_intent_result(user_query, result):
    handled = handle_tool_result(user_query, result)

    # Consistent with the fast-path and planner: a search that turned up exactly
    # one file gets auto-opened here too, regardless of which route found it.
    handled = auto_read_if_single_result(user_query, handled)

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

    if DEBUG:
        for intent_data in prioritized:
            print(f"[INTENT] {intent_data.get('intent')} (confidence: {intent_data.get('confidence')})")

    confident = [
        intent_data for intent_data in prioritized
        if intent_data.get("confidence", 0.0) >= MIN_CONFIDENCE
    ]

    if not confident:
        return None

    # A confident request that's entirely conversational is answered directly.
    if all(intent_data.get("intent") == "chat" for intent_data in confident):
        return fallback_chat(user_query)

    # Run every confident, actionable intent in one pass (letting route_intents'
    # own self-repair apply to each) and surface ALL of their results — not just
    # the last one — so independent multi-part requests like "open notepad and
    # search google for X" don't silently drop earlier confirmations even though
    # their side effects already happened. Standalone chat filler mixed into the
    # same batch is dropped in favor of the actionable results.
    actionable = [
        intent_data for intent_data in confident
        if intent_data.get("intent") != "chat"
    ]

    routed_results = route_intents(actionable, user_query, EXECUTION_CONTEXT)

    outputs = []
    for result in routed_results:
        handled = handle_intent_result(user_query, result)
        if handled:
            outputs.append(handled)

    if outputs:
        return "\n\n".join(outputs)

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

    # Note what was asked, so patterns have something to be found in. Cheap
    # and best-effort: a logging failure must never take a request down.
    try:
        from memory import observation_log
        observation_log.record(
            user_query, task_class=task_class,
            action=(extracted_data or {}).get("action") or "",
            detail=(extracted_data or {}).get("app") or "")
    except Exception:
        pass
    if task_class in (
        "SIMPLE_ACTION", "FILE_ACTION", "SEARCH_ACTION", "FILE_SEARCH_ACTION",
        "TIMER_ACTION", "SCREEN_ACTION", "SCHEDULE_ACTION", "DOC_ACTION",
        "SYSINFO_ACTION", "WEBREAD_ACTION", "LEARNING_ACTION",
        "MEMEDIT_ACTION", "FORGE_WEBSITE", "FORGE_REVISE",
        "WORKSHOP_ACTION", "IMAGE_ACTION", "PLANET_ACTION", "FORGE_CODE",
        "FORGE_SCRAP", "FORGE_OPEN", "SURFACE_ACTION",
    ):
        tool_result = execute_fast_path(task_class, extracted_data, user_query, EXECUTION_CONTEXT)
        if tool_result is not None:
            try:
                from memory import observation_log
                if task_class == "SIMPLE_ACTION":
                    observation_log.record(
                        user_query, task_class=task_class, action="open_app",
                        detail=(extracted_data or {}).get("app", ""))
            except Exception:
                pass
            handled = handle_tool_result(user_query, tool_result)

            # Auto-read if the search turned up exactly one file
            if task_class == "FILE_SEARCH_ACTION":
                handled = auto_read_if_single_result(user_query, tool_result)

            if not handled:
                handled = fallback_chat(user_query)
            return remember(user_query, handled)

    # A request the classifier judged compound is exactly what the planner's
    # branches are for — two independent jobs that shouldn't queue behind each
    # other. Try it there first; detect_intents still catches everything else,
    # and anything the planner can't shape falls through to it unchanged.
    if task_class == "COMPLEX_TASK":
        planned = maybe_start_plan(user_query, {"intent": "plan"})
        if planned:
            return remember(user_query, planned)

    handled = execute_intents(user_query, detect_intents(user_query, EXECUTION_CONTEXT))
    if not handled:
        handled = fallback_chat(user_query)

    return remember(user_query, handled)


