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
    
    # Mock an intent_data dictionary so we can update the execution context properly
    intent_data = {
        "intent": "tool",
        "parameters": action_data
    }
    
    update_context_from_result(execution_context, intent_data, result)
    
    return result
