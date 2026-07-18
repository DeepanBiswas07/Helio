def make_tool_result(status, result_type, content="", metadata=None):
    return {
        "status": status,
        "type": result_type,
        "content": content,
        "metadata": metadata or {}
    }


def success(result_type, content="", metadata=None):
    return make_tool_result("success", result_type, content, metadata)


def error(message, metadata=None):
    return make_tool_result("error", "text", message, metadata)


def is_tool_result(value):
    return (
        isinstance(value, dict)
        and value.get("status") in {"success", "error"}
        and "type" in value
    )


def from_legacy(value):
    if is_tool_result(value):
        return value

    if isinstance(value, dict) and value.get("type") == "llm":
        return success("llm_input", value.get("content", ""), value.get("metadata", {}))

    if isinstance(value, str):
        status = "error" if value.lower().startswith(("error", "failed", "invalid")) else "success"
        return make_tool_result(status, "text", value)

    if value is True:
        return success("action", "Done.")

    if value is None:
        return error("No result.")

    return success("text", str(value))


def to_legacy(value):
    result = from_legacy(value)

    if result["type"] == "llm_input":
        return {
            "type": "llm",
            "content": result.get("content", ""),
            "metadata": result.get("metadata", {})
        }

    return result.get("content", "")
