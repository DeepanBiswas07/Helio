from tools.tool_executor import execute_tool
from routing.intent_router import update_context_from_result

def execute_fast_path(task_class, extracted_data, user_query, execution_context):
    """
    Executes a tool directly bypassing the LLM intent detection and planner layers.
    Returns the raw tool result or None if routing failed.
    """
    action_data = None
    
    if task_class == "SIMPLE_ACTION":
        action_data = {
            "action": "open_app",
            "app": extracted_data.get("app")
        }
        
    elif task_class == "FILE_ACTION":
        action = extracted_data.get("action")
        if action == "next":
            action_data = {"action": "next_files"}
        elif action == "open":
            action_data = {"action": "open_file_index", "index": extracted_data.get("index")}
        elif action == "read":
            action_data = {"action": "read_file_index", "index": extracted_data.get("index")}
            
    elif task_class == "FILE_SEARCH_ACTION":
        query = extracted_data.get("query", "")
        root = extracted_data.get("root")
        action_data = {"action": "search_files", "query": query}
        if root:
            action_data["root"] = root

    elif task_class == "TIMER_ACTION":
        action = extracted_data.get("action")
        if action == "list":
            action_data = {"action": "list_reminders"}
        elif action == "cancel":
            action_data = {"action": "cancel_reminder", "which": extracted_data.get("which", "")}
        else:
            action_data = {"action": "set_reminder", "when": extracted_data.get("when", "")}
            if extracted_data.get("message"):
                action_data["message"] = extracted_data["message"]

    elif task_class == "SCHEDULE_ACTION":
        action = extracted_data.get("action")
        if action == "list":
            action_data = {"action": "list_schedule",
                           "range": extracted_data.get("range", "today")}
        elif action == "suggest":
            action_data = {"action": "suggest_activity",
                           "context": user_query}
        elif action == "pick":
            action_data = {"action": "accept_suggestion",
                           "choice": extracted_data.get("choice", "")}
        elif action == "add":
            action_data = {"action": "add_event",
                           "what": extracted_data.get("what", ""),
                           "when": extracted_data.get("when", "")}

    elif task_class == "DOC_ACTION":
        action = extracted_data.get("action")
        if action == "ask":
            action_data = {"action": "ask_documents",
                           "question": extracted_data.get("question", "")}
        elif action == "study":
            action_data = {"action": "study_file",
                           "path": extracted_data.get("path", "")}
        elif action == "list":
            action_data = {"action": "list_studied_documents"}

    elif task_class == "SYSINFO_ACTION":
        action_data = {"action": "get_system_info",
                       "what": extracted_data.get("what", "")}

    elif task_class == "WEBREAD_ACTION":
        if extracted_data.get("action") == "read":
            action_data = {"action": "read_webpage",
                           "url": extracted_data.get("url", ""),
                           "question": extracted_data.get("question", "")}
        else:
            action_data = {"action": "search_and_read",
                           "query": extracted_data.get("query", "")}

    elif task_class == "LEARNING_ACTION":
        action = extracted_data.get("action")
        if action == "list":
            action_data = {"action": "what_have_you_learned"}
        elif action == "wipe":
            action_data = {"action": "forget_my_activity"}
        elif action == "forget":
            action_data = {"action": "forget_observation",
                           "which": extracted_data.get("which", "")}

    elif task_class == "MEMEDIT_ACTION":
        action = extracted_data.get("action")
        if action == "forget":
            action_data = {"action": "forget_fact",
                           "what": extracted_data.get("what", "")}
        elif action == "history":
            action_data = {"action": "what_did_you_used_to_think"}
        elif action == "expire":
            action_data = {"action": "check_expired_memories"}

    elif task_class == "SCREEN_ACTION":
        action_data = {"action": "describe_screen", "question": extracted_data.get("question", "")}

    elif task_class == "FORGE_WEBSITE":
        action_data = {
            "action": "forge_create_website",
            "topic": extracted_data.get("topic", ""),
            "title": extracted_data.get("title", "")
        }

    elif task_class == "WORKSHOP_ACTION":
        action_data = {"action": extracted_data.get("action", "open_workshop")}

    elif task_class == "FORGE_OPEN":
        action_data = {"action": "forge_open",
                       "which": extracted_data.get("which", "")}

    elif task_class == "FORGE_SCRAP":
        action_data = {"action": "forge_scrap",
                       "which": extracted_data.get("which", "")}

    elif task_class == "PLANET_ACTION":
        action_data = {"action": "open_planet",
                       "planet": extracted_data.get("planet", "")}

    elif task_class == "FORGE_CODE":
        action_data = {"action": "forge_create_code",
                       "brief": extracted_data.get("brief", ""),
                       "language": extracted_data.get("language", "python")}

    elif task_class == "IMAGE_ACTION":
        action_data = {"action": "find_image",
                       "query": extracted_data.get("query", "")}

    elif task_class == "SURFACE_ACTION":
        action_data = {"action": "whats_on_the_workshop"}

    elif task_class == "FORGE_REVISE":
        action_data = {
            "action": "forge_revise",
            "change": extracted_data.get("change", ""),
            "which": extracted_data.get("which", ""),
        }

    elif task_class == "SEARCH_ACTION":
        action = extracted_data.get("action")
        if action == "youtube":
            action_data = {"action": "youtube_search", "query": extracted_data.get("query")}
        elif action == "web":
            action_data = {"action": "web_search", "query": extracted_data.get("query")}
            
    if not action_data:
        return None

    # Execute tool immediately
    result = execute_tool(action_data)

    # A bare "open <word>" that doesn't match any installed app is often really a
    # file/document reference (e.g. "open resume") rather than a mistyped app name.
    # Fall through to the full LLM router instead of surfacing this fast-path
    # failure as if it were the final answer.
    if (
        task_class == "SIMPLE_ACTION"
        and isinstance(result, str)
        and result.startswith("Could not find a confident match")
    ):
        return None

    # Mock an intent_data dictionary so we can update the execution context properly
    intent_data = {
        "intent": "tool",
        "parameters": action_data
    }
    
    update_context_from_result(execution_context, intent_data, result)
    
    return result
