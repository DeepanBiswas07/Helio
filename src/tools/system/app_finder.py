import os
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE_PATH = PROJECT_ROOT / "data" / "app_cache.json"

SEARCH_PATHS = [
    os.path.expanduser("~\\Desktop"),
    os.path.expanduser("~\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs"),
    "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs"
]


def clean_name(file):
    name = file.lower().replace(".lnk", "").replace(".exe", "")
    name = name.replace("-", " ").replace("_", " ")

    junk = ["shortcut", "launcher", "app", "application"]
    for j in junk:
        name = name.replace(j, "")

    return name.strip()


def scan_apps():
    apps = {}

    try:
        import subprocess
        cmd = 'powershell -NoProfile -Command "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Depth 2"'
        output = subprocess.check_output(cmd, shell=True, text=True, encoding='utf-8')
        
        apps_list = json.loads(output)
        if isinstance(apps_list, dict):
            apps_list = [apps_list]
            
        for app in apps_list:
            name = app.get("Name", "")
            app_id = app.get("AppID", "")
            
            if not name or not app_id:
                continue
                
            clean = clean_name(name)
            if "uninstall" in clean:
                continue
                
            apps[clean] = f"shell:AppsFolder\\{app_id}"
            
    except Exception:
        pass

    for base_path in SEARCH_PATHS:
        if not os.path.exists(base_path):
            continue

        for root, dirs, files in os.walk(base_path):
            for file in files:
                if not file.lower().endswith((".lnk", ".exe")):
                    continue

                name = clean_name(file)
                if "uninstall" in name or name in apps:
                    continue

                apps[name] = os.path.join(root, file)

    return apps


def load_apps(force=False):
    if not force and CACHE_PATH.exists():
        try:
            with CACHE_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

    apps = scan_apps()

    try:
        with CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(apps, f, indent=2)
    except OSError:
        pass

    return apps
