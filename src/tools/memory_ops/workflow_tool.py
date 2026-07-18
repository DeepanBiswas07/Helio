from tools.tool_registry import tool
from memory.semantic_memory import (
    save_workflow,
    get_workflow,
    list_workflows,
    delete_workflow,
)


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
    from tools.system.system_control import open_app_smart

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

    results = []
    for step in steps:
        step_lower = step.lower()
        # Route the step through the app launcher for "open X" steps
        if step_lower.startswith("open "):
            app = step[5:].strip()
            result = open_app_smart(app)
            results.append(f"✓ {step} → {result}")
        else:
            # For non-app steps, just acknowledge (can be extended)
            results.append(f"• {step}")

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
