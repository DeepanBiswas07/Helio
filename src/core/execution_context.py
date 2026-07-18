MAX_TOOL_RESULTS = 20


def create_execution_context():
    return {
        "conversation": {},
        "planner": {},
        "tool_results": {
            "items": [],
            "last": None
        },
        "file_search": {
            "last_results": [],
            "last_root": None,
            "last_query": None
        },
        "session": {}
    }


def ensure_context(context):
    if not isinstance(context, dict):
        return create_execution_context()

    base = create_execution_context()
    for key, value in context.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


def parse_numbered_file_results(result_text):
    if not isinstance(result_text, str):
        return []

    results = []
    for line in result_text.splitlines():
        stripped = line.strip()
        if "." not in stripped:
            continue

        number, value = stripped.split(".", 1)
        try:
            results.append({
                "index": int(number.strip()),
                "path": value.strip()
            })
        except ValueError:
            continue

    return results


def record_tool_result(context, intent_data, result):
    context = ensure_context(context)

    item = {
        "intent": intent_data.get("intent") if isinstance(intent_data, dict) else None,
        "parameters": intent_data.get("parameters", {}) if isinstance(intent_data, dict) else {},
        "result": result
    }

    context["tool_results"]["items"].append(item)
    context["tool_results"]["items"] = context["tool_results"]["items"][-MAX_TOOL_RESULTS:]
    context["tool_results"]["last"] = item
    context["last_result"] = result
    context["last_intent"] = intent_data

    numbered_results = parse_numbered_file_results(result)
    if numbered_results:
        context["file_search"]["last_results"] = numbered_results
        context["file_search"]["last_query"] = item["parameters"].get("query")
        context["file_search"]["last_root"] = item["parameters"].get("root")
        context["last_file_results"] = numbered_results

    return context


def get_context_value(context, path):
    current = ensure_context(context)
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current
