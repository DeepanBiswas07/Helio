import subprocess
from rapidfuzz import process, fuzz
from tools.system.app_finder import load_apps

APP_CACHE = load_apps()


SYSTEM_APPS = {
    "file explorer": "explorer",
    "explorer": "explorer",
    "this pc": "explorer",
    "task manager": "taskmgr",
    "cmd": "start cmd",
    "powershell": "start powershell",
    "settings": "start ms-settings:",
    "photos": "start ms-photos:",
    "clock": "start ms-clock:",
    "calculator": "calc",
    "notepad": "notepad",
    "paint": "mspaint",
    "snipping tool": "snippingtool",
    "snip": "snippingtool"
}


ALIASES = {
    "vs code": "visual studio code",
    "code": "visual studio code",
    "browser": "chrome",
    "chrome browser": "chrome",
    "to do": "microsoft to do",
    "todo": "microsoft to do",
    "tasks": "microsoft to do",
    "notes": "notepad",
    "note": "notepad",
    "noteapp": "notepad",
    "text editor": "notepad",
    "snip": "snipping tool",
    "screenshot tool": "snipping tool",
}


def launch_path(path):
    try:
        if path.lower().endswith(".lnk"):
            subprocess.Popen(f'start "" "{path}"', shell=True)
        elif path.lower().startswith("shell:"):
            subprocess.Popen(f'explorer "{path}"', shell=True)
        else:
            subprocess.Popen(path)
        return True
    except Exception as e:
        return str(e)


def open_app_smart(query):
    global APP_CACHE
    query = query.lower().strip()

    if len(query) < 3:
        return "Please specify a valid app name."

    query = ALIASES.get(query, query)

    if query in SYSTEM_APPS:
        try:
            subprocess.Popen(SYSTEM_APPS[query], shell=True)
            return f"Opening {query}..."
        except Exception as e:
            return f"Failed to open {query}: {e}"

    # First pass: try to find the app in the existing cache
    for app_name, path in APP_CACHE.items():
        if query in app_name:
            result = launch_path(path)
            if result is True:
                return f"Opening {app_name}..."
            else:
                return f"Failed to open {app_name}: {result}"

    if APP_CACHE:
        matches = process.extract(
            query,
            APP_CACHE.keys(),
            scorer=fuzz.WRatio,
            limit=5
        )
        if matches:
            best_match, score, _ = matches[0]
            if score >= 80 and len(best_match) >= 3:
                path = APP_CACHE[best_match]
                result = launch_path(path)
                if result is True:
                    return f"Opening {best_match}..."
                else:
                    return f"Failed to open {best_match}: {result}"

    # If not found, force refresh the cache and try one more time
    APP_CACHE = load_apps(force=True)

    for app_name, path in APP_CACHE.items():
        if query in app_name:
            result = launch_path(path)
            if result is True:
                return f"Opening {app_name}..."
            else:
                return f"Failed to open {app_name}: {result}"

    if APP_CACHE:
        matches = process.extract(
            query,
            APP_CACHE.keys(),
            scorer=fuzz.WRatio,
            limit=5
        )
        if matches:
            best_match, score, _ = matches[0]
            if score >= 80 and len(best_match) >= 3:
                path = APP_CACHE[best_match]
                result = launch_path(path)
                if result is True:
                    return f"Opening {best_match}..."
                else:
                    return f"Failed to open {best_match}: {result}"

    return f"Could not find a confident match for '{query}'"
