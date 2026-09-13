"""
workshop_tool.py — Helio's own hands on the workshop surface.

These are what let "find me a cat photo and put it up" or "open the workshop"
work as spoken instructions rather than things you have to click. Every one of
them runs on the agent's worker thread, so none of them touches a widget: they
call a callback the UI registered, and the UI turns that into a signal on its
own thread.

Auto-discovered by tool_registry.auto_discover().
"""
import os
from pathlib import Path

from tools.tool_registry import tool

# Set by WorkshopCanvas while it is open — request(action, **kwargs).
ON_WORKSHOP_REQUEST = None

# Set by the main window for the whole session. Opening a top-level window has
# to happen on the GUI thread, so this is a signal emitter, not a direct call.
ON_WORKSHOP_OPEN = None

# Same again for swinging the planet ring round. Takes a slot index.
ON_PLANET_OPEN = None

# The ring, in the order the planets actually orbit. Slot 0 and the last are
# still unbuilt, so they are not offered.
PLANETS = {
    "setup": 1, "assemblies": 1, "routines": 1, "workflows": 1,
    "memory": 2, "facts": 2,
    "chat": 3, "conversation": 3,
    "files": 4, "documents": 4, "file": 4,
    "system": 5, "machine": 5, "stats": 5,
    "schedule": 6, "calendar": 6, "diary": 6, "agenda": 6,
    "forge": 7, "bench": 7, "builds": 7,
}

# What is on the surface right now, published by the canvas whenever it
# changes. Plain dicts, replaced wholesale rather than mutated, so the agent's
# worker thread can read it without touching a widget or taking a lock.
SURFACE = []


def on_surface(kind=None):
    """What is up on the workshop, newest last. Optionally one kind only."""
    items = list(SURFACE)
    if kind:
        wanted = {kind} if isinstance(kind, str) else set(kind)
        items = [i for i in items if i.get("kind") in wanted]
    return items


def picture_on_screen():
    """The picture the user means when they say 'the one on screen'."""
    pictures = on_surface("image")
    return pictures[-1] if pictures else None


# Things Helio asked to place while the surface was shut. Drained when it opens
# so "put this up" followed by opening the workshop does not lose the thing.
_PENDING = []
MAX_PENDING = 12


def is_open():
    return ON_WORKSHOP_REQUEST is not None


def request(action, **kwargs):
    """Ask the surface to do something, or hold it until there is one."""
    if ON_WORKSHOP_REQUEST is not None:
        try:
            ON_WORKSHOP_REQUEST(action, **kwargs)
            return True
        except Exception as e:
            print(f"[Workshop] request failed: {e}")
            return False
    if action == "place":
        _PENDING.append(kwargs)
        del _PENDING[:-MAX_PENDING]
    return False


def drain_pending():
    """Called by the canvas once it is up and listening."""
    held, _PENDING[:] = list(_PENDING), []
    for kwargs in held:
        request("place", **kwargs)
    return len(held)


def open_surface():
    """Raise the workshop. Returns True when the UI could be reached."""
    if ON_WORKSHOP_OPEN is None:
        return False
    try:
        ON_WORKSHOP_OPEN()
        return True
    except Exception as e:
        print(f"[Workshop] could not open: {e}")
        return False


def _resolve_made(reference):
    """Find one of Helio's own artefacts from a bare or partial filename."""
    name = Path(str(reference or "")).name.lower()
    if not name:
        return None
    root = Path(__file__).resolve().parents[3] / "data" / "forge"
    if not root.exists():
        return None

    candidates = [f for f in root.rglob("*") if f.is_file()]
    for path in candidates:
        if path.name.lower() == name:
            return str(path)
    stem = Path(name).stem
    hits = [f for f in candidates if stem and stem in f.stem.lower()]
    if hits:
        return str(max(hits, key=lambda f: f.stat().st_mtime))
    return None


def _kind_for(target):
    """What sort of slab a path or URL wants."""
    target = str(target or "")
    if target.startswith(("http://", "https://")):
        return "web"
    suffix = Path(target).suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico"):
        return "image"
    if suffix in (".html", ".htm"):
        return "web"
    if suffix in (".py", ".js", ".ts", ".css", ".json", ".java", ".c", ".cpp",
                  ".cs", ".go", ".rs", ".rb", ".sh", ".ps1", ".sql", ".yml",
                  ".yaml", ".toml", ".ini"):
        return "code"
    if suffix in (".md", ".txt", ".log", ".csv"):
        return "document"
    return "text"


@tool(
    name="open_workshop",
    description=(
        "Open Helio's full-screen workshop — the making surface where builds, "
        "images, code and notes sit side by side and can be moved around. "
        "Use for 'open the workshop', 'go full screen', 'open build mode', "
        "'let's build something', 'open my workbench'."
    ),
    parameters={},
    required=[],
)
def handle_open_workshop(action_data):
    if open_surface():
        return "Workshop's up. Tell me what to make."
    return ("I couldn't open the workshop — the interface isn't running. "
            "Open Helio's window first.")


@tool(
    name="show_in_workshop",
    description=(
        "Put something on the workshop surface so it can be seen and moved: a "
        "file on this machine, an image, a web page, or a note. "
        "Use for 'put that on the workshop', 'show me that file', "
        "'bring it up on screen', 'open this on the surface'. "
        "'target' is a file path or URL; 'note' is text to pin up instead."
    ),
    parameters={"target": "string", "title": "string", "note": "string"},
    required=[],
)
def handle_show_in_workshop(action_data):
    target = str(action_data.get("target", "") or "").strip()
    note = str(action_data.get("note", "") or "").strip()
    title = str(action_data.get("title", "") or "").strip()

    if not target and not note:
        return "What should I put up?"

    if not is_open():
        open_surface()

    if note and not target:
        request("place", kind="text", title=title or "Note", payload=note)
        return "Pinned it up on the workshop."

    if not target.startswith(("http://", "https://")) and not os.path.exists(target):
        # Helio refers to its own work by bare filename ("the sourdough photo"
        # becomes sourdough_bread_1788.jpg), so look through what it has made
        # before giving up.
        found = _resolve_made(target)
        if found is None:
            return f"I can't find '{target}' to put up."
        target = found

    kind = _kind_for(target)
    request("place", kind=kind, title=title or Path(target).name or target,
            payload=target)

    where = "the workshop" if is_open() else "the workshop for when it opens"
    return f"Put it on {where}."


@tool(
    name="clear_workshop",
    description=(
        "Take everything off the workshop surface. "
        "Use for 'clear the workshop', 'clean the surface', 'start fresh', "
        "'take it all down'."
    ),
    parameters={},
    required=[],
)
def handle_clear_workshop(action_data):
    if not is_open():
        return "The workshop isn't open, so there's nothing on it."
    request("clear")
    return "Cleared the surface."


@tool(
    name="open_planet",
    description=(
        "Swing Helio's ring round to one of its planets and open it: setup, "
        "memory, chat, files, system, schedule or forge. "
        "Use for 'open my memory', 'show me the schedule', 'go to files', "
        "'open the forge', 'bring up system'. "
        "'planet' is which one."
    ),
    parameters={"planet": "string"},
    required=["planet"],
)
def handle_open_planet(action_data):
    wanted = str(action_data.get("planet", "") or "").strip().lower()
    if not wanted:
        return "Which one? Setup, memory, chat, files, system, schedule or forge."

    slot = PLANETS.get(wanted)
    if slot is None:
        # "open my schedule planet" and the like — find the name inside it.
        for name, index in PLANETS.items():
            if name in wanted:
                slot, wanted = index, name
                break

    if slot is None:
        return (f"I don't have a '{wanted}' planet. There's setup, memory, "
                "chat, files, system, schedule and forge.")

    if ON_PLANET_OPEN is None:
        return "The interface isn't running, so I can't open that."

    try:
        ON_PLANET_OPEN(slot)
    except Exception as e:
        return f"I couldn't open that — {e}"
    return f"Opening {wanted}."


@tool(
    name="whats_on_the_workshop",
    description=(
        "Report what is currently up on the workshop surface — which pages, "
        "pictures, code and notes are open. "
        "Use for 'what's on the workshop', 'what have you got open', "
        "'what picture is on screen', 'what am I looking at'."
    ),
    parameters={},
    required=[],
)
def handle_whats_on_the_workshop(action_data):
    if not is_open():
        return "The workshop isn't open."

    items = on_surface()
    if not items:
        return "The workshop is open but the surface is clear."

    words = {"image": "picture", "web": "page", "code": "source",
             "chart": "chart", "document": "document", "text": "note",
             "rack": "the build rack"}
    lines = []
    for item in items:
        what = words.get(item.get("kind"), item.get("kind", "thing"))
        name = Path(str(item.get("path", ""))).name
        lines.append(f"  - {what}: {item.get('title', 'untitled')}"
                     + (f"  ({name})" if name else ""))

    total = len(items)
    return (f"{total} thing" + ("" if total == 1 else "s")
            + " on the workshop:\n" + "\n".join(lines))
