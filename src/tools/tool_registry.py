import importlib
import pkgutil
from pathlib import Path

TOOL_REGISTRY = {}
TOOL_SCHEMAS = {}
_DISCOVERED = False


def tool(name, description, parameters=None, required=None):
    def decorator(func):
        TOOL_SCHEMAS[name] = {
            "description": description,
            "parameters": parameters or {},
            "required": required or []
        }
        TOOL_REGISTRY[name] = func
        return func
    return decorator


def get_available_tools():
    auto_discover()
    return list(TOOL_SCHEMAS.keys())


def get_tool_schemas():
    auto_discover()
    return TOOL_SCHEMAS.copy()


def get_tool_function(name):
    return TOOL_REGISTRY.get(name)


def auto_discover():
    global _DISCOVERED
    if _DISCOVERED:
        return
    
    _DISCOVERED = True
    tools_path = Path(__file__).parent
    
    # Recursively find all .py files in the tools directory
    for py_file in tools_path.rglob("*.py"):
        # Get relative path from tools directory
        rel_path = py_file.relative_to(tools_path.parent)
        
        # Convert path to module name (e.g., tools/system/app_finder.py -> tools.system.app_finder)
        module_name = ".".join(rel_path.with_suffix("").parts)
        
        # Skip this file and __init__ files
        if module_name == "tools.tool_registry" or py_file.name == "__init__.py":
            continue
            
        try:
            importlib.import_module(module_name)
        except Exception:
            pass
