from tools.tool_registry import tool
from memory.semantic_memory import (
    save_workflow,
    get_workflow,
    list_workflows,
    delete_workflow,
    touch_workflow,
)

# A step is a natural-language instruction, so running one means going back
# through the agent — which can reach run_workflow again. One level deep is
# enough for any real routine; beyond that it's a loop, not a workflow.
_RUN_DEPTH = 0
_MAX_RUN_DEPTH = 1


def run_step(step: str) -> str:
    """
    Execute a single workflow step and return a one-line transcript of what
    happened. "open X" goes straight to the launcher — it's the most common
    step by far and doesn't need a model. Everything else is handed to the
    agent so steps like "check my CPU" or "search for my resume" actually run.
    """
    step = (step or "").strip()
    if not step:
        return ""

    if step.lower().startswith("open "):
        from tools.system.system_control import open_app_smart
        try:
            result = open_app_smart(step[5:].strip())
        except Exception as e:
            return f"✗ {step} → {e}"
        return f"✓ {step} → {result}"

    # Imported here, not at module scope: agent imports tool_executor, which
    # imports this module, so a top-level import would be circular.
    try:
        from agent import run_agent
    except ImportError:
        return f"• {step} (skipped — agent unavailable)"

    try:
        result = run_agent(step)
    except Exception as e:
        return f"✗ {step} → {e}"

    result = (result or "").strip().replace("\n", " ")
    if len(result) > 160:
        result = result[:157] + "..."
    return f"✓ {step} → {result}" if result else f"✓ {step}"


@tool(
    name="remember_workflow",
    description=(
        "Save a named multi-step routine to workflow memory so Helio can run it later. "
        "Use this when the user describes a repeated sequence of tasks they do together "
        "(e.g. 'my work setup is: open vscode, open terminal, run server'). "
        "'name' is a short label (e.g. 'work setup'). "
        "'steps' is a comma-separated list of actions in order. "
        "'purpose' is an optional reason (e.g. 'start coding session')."
    ),
    parameters={
        "name": "string",
        "steps": "string",
        "purpose": "string",
    },
    required=["name", "steps"]
)
def handle_remember_workflow(action_data):
    name = action_data.get("name", "").strip()
    raw_steps = action_data.get("steps", "")
    purpose = action_data.get("purpose", "").strip()

    if not name:
        return "Please provide a name for this workflow."

    # Accept comma-separated or newline-separated steps
    if isinstance(raw_steps, list):
        steps = raw_steps
    else:
        steps = [s.strip() for s in raw_steps.replace("\n", ",").split(",") if s.strip()]

    if not steps:
        return "Please provide at least one step for the workflow."

    save_workflow(name, steps, purpose)

    formatted = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))
    purpose_line = f"\nPurpose: {purpose}" if purpose else ""
    return (
        f"Workflow '{name}' saved to memory!\n"
        f"Steps:\n{formatted}"
        f"{purpose_line}"
    )


@tool(
    name="run_workflow",
    description=(
        "Execute a previously saved workflow by name. "
        "Helio will run each step in sequence. "
        "Use this when the user says something like 'run my work setup' or 'do my morning routine'."
    ),
    parameters={"name": "string"},
    required=["name"]
)
def handle_run_workflow(action_data):
    name = action_data.get("name", "").strip()
    if not name:
        return "Please specify which workflow to run."

    workflow = get_workflow(name)
    if not workflow:
        # Try fuzzy match against all workflow names
        from rapidfuzz import process, fuzz
        all_workflows = list_workflows()
        if all_workflows:
            matches = process.extract(
                name, all_workflows.keys(), scorer=fuzz.token_set_ratio, limit=1
            )
            if matches and matches[0][1] >= 70:
                best = matches[0][0]
                workflow = all_workflows[best]
                name = best
            else:
                return f"No workflow named '{name}' found. Try 'list my workflows' to see what's saved."
        else:
            return "No workflows saved yet. Describe a routine to me and I'll remember it."

    steps = workflow.get("steps", [])
    purpose = workflow.get("purpose", "")

    if not steps:
        return f"Workflow '{name}' has no steps."

    global _RUN_DEPTH
    if _RUN_DEPTH >= _MAX_RUN_DEPTH:
        return f"Skipped '{name}' — a workflow can't run another workflow."

    _RUN_DEPTH += 1
    try:
        results = [run_step(step) for step in steps]
    finally:
        _RUN_DEPTH -= 1

    touch_workflow(name)

    summary = "\n".join(results)
    purpose_line = f"\nPurpose: {purpose}" if purpose else ""
    return f"Running workflow: '{name}'{purpose_line}\n\n{summary}"


@tool(
    name="list_workflows",
    description="List all saved workflows by name and their steps.",
    parameters={},
    required=[]
)
def handle_list_workflows(action_data):
    workflows = list_workflows()
    if not workflows:
        return "No workflows saved yet. Describe a routine to me and I'll remember it."

    lines = []
    for name, data in workflows.items():
        steps = data.get("steps", [])
        purpose = data.get("purpose", "")
        step_list = ", ".join(steps)
        purpose_str = f" — {purpose}" if purpose else ""
        lines.append(f"• {name}{purpose_str}\n  Steps: {step_list}")

    return "Saved Workflows:\n\n" + "\n\n".join(lines)


@tool(
    name="forget_workflow",
    description="Delete a saved workflow by name.",
    parameters={"name": "string"},
    required=["name"]
)
def handle_forget_workflow(action_data):
    name = action_data.get("name", "").strip()
    if not name:
        return "Please specify which workflow to forget."

    removed = delete_workflow(name)
    if removed:
        return f"Workflow '{name}' has been removed from memory."
    return f"No workflow named '{name}' was found."
