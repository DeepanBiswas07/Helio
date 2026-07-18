import os
import subprocess

from tools.web_ops.browser import open_google_search, open_youtube_search
from tools.file_ops.file_context import (
    format_results,
    get_by_index,
    get_next_page,
    set_results,
)
from tools.file_ops.file_reader import read_pdf, read_text
from tools.file_ops.file_search import extract_search_scope, search_files, strip_search_scope
from tools.system.shortcut_resolver import resolve_lnk
from tools.system.system_control import open_app_smart


def open_file_path(path):
    if not path:
        return "Missing file path."

    try:
        subprocess.Popen(f'start "" "{path}"', shell=True)
        return True
    except Exception as e:
        return str(e)


def resolve_recent_path(path):
    if not path:
        return None

    if "windows\\recent" not in path.lower():
        return path

    filename = os.path.basename(path).replace(".lnk", "")
    matches = search_files(filename)

    if matches:
        return matches[0]

    return None


def read_path(path):
    path = resolve_recent_path(path) or path

    if path.lower().endswith(".lnk"):
        path = resolve_lnk(path) or path

    ext = path.lower()

    if ext.endswith(".pdf"):
        return read_pdf(path)

    if ext.endswith(".txt"):
        return read_text(path)

    if ext.endswith(".docx"):
        from tools.file_ops.file_reader import read_docx
        return read_docx(path)

    if ext.endswith(".pptx"):
        from tools.file_ops.file_reader import read_pptx
        return read_pptx(path)

    if ext.endswith((".xls", ".xlsx")):
        from tools.file_ops.file_reader import read_excel
        return read_excel(path)

    return read_text(path)


from tools.tool_registry import tool, get_tool_function, auto_discover


@tool(
    name="open_app",
    description="Open an installed desktop application.",
    parameters={"app": "string"},
    required=["app"]
)
def handle_open_app(action_data):
    return open_app_smart(action_data.get("app", ""))


@tool(
    name="web_search",
    description="Open a Google search in the browser.",
    parameters={"query": "string"},
    required=["query"]
)
def handle_web_search(action_data):
    return open_google_search(action_data.get("query", ""))


@tool(
    name="youtube_search",
    description="Open a YouTube search.",
    parameters={"query": "string"},
    required=["query"]
)
def handle_youtube_search(action_data):
    return open_youtube_search(action_data.get("query", ""))


@tool(
    name="read_pdf",
    description="Read a direct PDF path.",
    parameters={"path": "string"},
    required=["path"]
)
def handle_read_pdf(action_data):
    path = action_data.get("path", "")
    return {
        "type": "llm",
        "content": read_path(path),
        "metadata": {"path": path}
    }


@tool(
    name="read_file",
    description="Read a direct text file path.",
    parameters={"path": "string"},
    required=["path"]
)
def handle_read_file(action_data):
    path = action_data.get("path", "")
    return {
        "type": "llm",
        "content": read_path(path),
        "metadata": {"path": path}
    }


@tool(
    name="search_files",
    description="Search local document files, optionally limited to a root path.",
    parameters={"query": "string", "root": "string"},
    required=["query"]
)
def handle_search_files(action_data):
    raw_query = action_data.get("query", "")
    root = action_data.get("root")

    if not raw_query:
        return "What file should I search for?"

    if not root:
        root = extract_search_scope(raw_query)

    clean_query = strip_search_scope(raw_query) or raw_query
    results = search_files(clean_query, root=root)

    if not results:
        if root:
            return f"No files found in {root}."
        return "No files found."

    set_results(results)
    page, start = get_next_page()

    return format_results(page, start)


@tool(
    name="next_files",
    description="Show the next page of local file search results.",
    parameters={},
    required=[]
)
def handle_next_files(action_data):
    page, start = get_next_page()

    if not page:
        return "No more results."

    return format_results(page, start)


@tool(
    name="open_file_index",
    description="Open a numbered file from the latest search results.",
    parameters={"index": "integer"},
    required=["index"]
)
def handle_open_file_index(action_data):
    idx = int(action_data.get("index", 1)) - 1
    path = get_by_index(idx)

    if not path:
        return "Invalid file number."

    path = resolve_recent_path(path) or path

    if path.lower().endswith(".lnk"):
        path = resolve_lnk(path) or path

    result = open_file_path(path)

    if result is True:
        return f"Opening file {idx + 1}"

    return f"Failed to open file: {result}"


@tool(
    name="read_file_index",
    description="Read a numbered file from the latest search results.",
    parameters={"index": "integer"},
    required=["index"]
)
def handle_read_file_index(action_data):
    idx = int(action_data.get("index", 1)) - 1
    path = get_by_index(idx)

    if not path:
        return "Invalid file number."

    path = resolve_recent_path(path) or path

    if path.lower().endswith(".lnk"):
        resolved = resolve_lnk(path)
        if resolved:
            path = resolved
        else:
            return "Could not resolve shortcut."

    if not os.path.exists(path):
        return "File no longer exists."

    return {
        "type": "llm",
        "content": read_path(path),
        "metadata": {"path": path}
    }


ON_TOOL_EXECUTE_CALLBACK = None

def execute_tool(action_data):
    if not isinstance(action_data, dict):
        return None

    action = action_data.get("action")
    if not action or action == "none":
        return None
        
    # Trigger the callback immediately if a visual window-blocking application/search is launched
    if action in ["open_app", "web_search", "youtube_search", "open_file_index"]:
        global ON_TOOL_EXECUTE_CALLBACK
        if ON_TOOL_EXECUTE_CALLBACK:
            try:
                ON_TOOL_EXECUTE_CALLBACK()
            except Exception as e:
                print(f"[Tool Executor] Callback invocation failed: {e}")

    auto_discover()
    
    func = get_tool_function(action)
    if func:
        return func(action_data)
        
    return None
