"""
forge_tool.py — Helio's maker bench: the tools that turn a spoken request
into something you can actually look at.

Three kinds of thing come out of here:

  charts     — real matplotlib renders of real numbers
  pages      — complete standalone HTML, written for the thing you asked for
  documents  — notes, reports and scripts you can edit in place

The pages are *generated*, not stamped out of a template. An earlier version
of this file carried a 700-line hardcoded HTML string and pasted your topic
into two of its labels, so "a site for my mum's bakery" and "a crypto
dashboard" came out as the same neon control room. Everything below exists to
make the output actually follow the brief.

Auto-discovered by tool_registry.auto_discover().
"""
import base64
import difflib
import os
import json
import time
import re
from pathlib import Path
from datetime import datetime
from tools.tool_registry import tool

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "forge"
CHARTS_DIR = DATA_DIR / "charts"
APPS_DIR = DATA_DIR / "apps"
DOCS_DIR = DATA_DIR / "docs"
ACTIVE_ARTIFACT_PATH = DATA_DIR / "active_artifact.json"
HISTORY_PATH = DATA_DIR / "history.json"

# Set by the Forge panel so a finished build lands on the canvas.
ON_FORGE_UPDATED_CALLBACK = None

# Set by the Forge panel so a build can be *watched*. Called with
# (event, payload) where event is one of "start" / "chunk" / "done" / "error".
# A page takes twenty-odd seconds to write; without this the panel shows
# nothing at all until it is over, which is the whole thing the Forge is for.
ON_FORGE_STREAM_CALLBACK = None


def _ensure_dirs():
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", (text or "artifact").lower())
    return re.sub(r"[-\s]+", "_", slug).strip("_")[:40] or "artifact"


def get_active_artifact() -> dict:
    _ensure_dirs()
    if not ACTIVE_ARTIFACT_PATH.exists():
        return {}
    try:
        with open(ACTIVE_ARTIFACT_PATH, "r", encoding="utf-8") as f:
            artifact = json.load(f)
    except Exception:
        return {}
    if not isinstance(artifact, dict):
        return {}
    # Its file may have been deleted from outside Helio since.
    path = artifact.get("file_path")
    if path and not Path(path).exists():
        try:
            ACTIVE_ARTIFACT_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        return {}
    return artifact


def list_forge_history(prune=True) -> list:
    """
    The rack, with builds whose files have gone dropped from it.

    Deleting a build in Explorer used to leave it listed here forever, and
    putting it on the bench then did nothing. The file is the build; if it
    isn't there, neither is the build.
    """
    _ensure_dirs()
    if not HISTORY_PATH.exists():
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    if not prune:
        return data

    kept = [r for r in data
            if r.get("file_path") and Path(r["file_path"]).exists()]
    if len(kept) != len(data):
        try:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(kept, f, indent=2, ensure_ascii=False)
            print(f"[Forge] dropped {len(data) - len(kept)} build(s) whose "
                  "files are gone")
        except Exception:
            pass
    return kept


# More than one surface can be watching the same build — the Forge panel and
# the workshop canvas both do. A single callback slot meant the last one to
# register silently unhooked the other.
_STREAM_LISTENERS = []
_ARTIFACT_LISTENERS = []


def add_stream_listener(fn):
    if fn is not None and fn not in _STREAM_LISTENERS:
        _STREAM_LISTENERS.append(fn)


def remove_stream_listener(fn):
    if fn in _STREAM_LISTENERS:
        _STREAM_LISTENERS.remove(fn)


def add_artifact_listener(fn):
    if fn is not None and fn not in _ARTIFACT_LISTENERS:
        _ARTIFACT_LISTENERS.append(fn)


def remove_artifact_listener(fn):
    if fn in _ARTIFACT_LISTENERS:
        _ARTIFACT_LISTENERS.remove(fn)


def _listeners(registered, legacy):
    """Registered listeners plus the legacy single-slot callback, if set."""
    everyone = list(registered)
    if legacy is not None and legacy not in everyone:
        everyone.append(legacy)
    return everyone


def _emit(event: str, payload=None):
    """Tell every watching surface how a build is going.

    A fault in one must not stop the others, and must never kill the build.
    """
    for listener in _listeners(_STREAM_LISTENERS, ON_FORGE_STREAM_CALLBACK):
        try:
            listener(event, payload)
        except Exception as e:
            print(f"[Forge] stream listener error: {e}")


def _set_active_artifact(artifact: dict):
    _ensure_dirs()
    try:
        with open(ACTIVE_ARTIFACT_PATH, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Forge] Error saving active artifact: {e}")

    # Append to history
    history = list_forge_history()
    # Filter out duplicate of same id/path
    history = [h for h in history if h.get("file_path") != artifact.get("file_path")]
    history.insert(0, artifact)
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history[:50], f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    # Notify every watching surface.
    for listener in _listeners(_ARTIFACT_LISTENERS, ON_FORGE_UPDATED_CALLBACK):
        try:
            listener(artifact)
        except Exception as e:
            print(f"[Forge] artifact listener error: {e}")


SCRAPPED_DIR = DATA_DIR / "scrapped"


def delete_artifact(file_path: str) -> bool:
    """
    Take one build off the rack.

    The file moves to data/forge/scrapped rather than being unlinked. Scrap
    is one word away from a build command by voice, and 45 builds is a lot to
    lose to a mishearing — a folder move is recoverable, rm is not.
    """
    history = [h for h in list_forge_history() if h.get("file_path") != file_path]
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception:
        return False

    active = get_active_artifact()
    if active.get("file_path") == file_path:
        try:
            ACTIVE_ARTIFACT_PATH.unlink(missing_ok=True)
        except Exception:
            pass

    # A multi-file build is a folder; move the whole thing, or scrapping it
    # takes index.html away and leaves the css and js orphaned.
    root = None
    for record in list_forge_history(prune=False):
        if record.get("file_path") == file_path and record.get("root"):
            root = Path(record["root"])
            break
    if root is None:
        for record in (get_active_artifact(),):
            if record.get("file_path") == file_path and record.get("root"):
                root = Path(record["root"])

    source = root if (root is not None and root.exists()) else Path(file_path)
    if source.exists():
        try:
            SCRAPPED_DIR.mkdir(parents=True, exist_ok=True)
            target = SCRAPPED_DIR / source.name
            if target.exists():
                stamp = int(time.time())
                target = SCRAPPED_DIR / (
                    f"{source.stem}_{stamp}{source.suffix}" if source.is_file()
                    else f"{source.name}_{stamp}")
            source.replace(target)
        except Exception as e:
            print(f"[Forge] could not scrap {source.name}: {e}")
    return True


@tool(
    name="forge_create_chart",
    description=(
        "Generate a futuristic dark-themed visual chart and display it on The Forge canvas. "
        "chart_type can be 'bar', 'line', 'pie', 'doughnut', or 'horizontal_bar'. "
        "data should be a dictionary of {label: number} or a list of [label, number] pairs."
    ),
    parameters={
        "title": "string",
        "chart_type": "string",
        "data": "object",
        "x_label": "string",
        "y_label": "string"
    },
    required=["title", "chart_type", "data"]
)
def handle_forge_create_chart(action_data):
    _ensure_dirs()
    title = str(action_data.get("title", "Visual Chart")).strip()
    chart_type = str(action_data.get("chart_type", "bar")).lower().strip()
    raw_data = action_data.get("data", {})
    x_label = str(action_data.get("x_label", "")).strip()
    y_label = str(action_data.get("y_label", "")).strip()

    # Parse data into (labels, values)
    labels = []
    values = []
    if isinstance(raw_data, dict):
        for k, v in raw_data.items():
            labels.append(str(k))
            try:
                values.append(float(v))
            except (ValueError, TypeError):
                values.append(0.0)
    elif isinstance(raw_data, list):
        for item in raw_data:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                labels.append(str(item[0]))
                try:
                    values.append(float(item[1]))
                except (ValueError, TypeError):
                    values.append(0.0)
            elif isinstance(item, dict):
                k = item.get("label") or item.get("name") or str(len(labels)+1)
                v = item.get("value") or item.get("count") or 0.0
                labels.append(str(k))
                try:
                    values.append(float(v))
                except (ValueError, TypeError):
                    values.append(0.0)

    if not labels or not values:
        return "Failed to create chart: no valid labels and values found in data."

    import matplotlib
    matplotlib.use("Agg")  # Non-GUI thread-safe backend
    import matplotlib.pyplot as plt

    # Futuristic Cyberpunk Theme Colors
    BG_COLOR = "#0D0B18"
    PANEL_BG = "#130F24"
    TEXT_COLOR = "#ECEFF4"
    GRID_COLOR = "#2E244B"
    CYBER_COLORS = ["#00E5FF", "#FF007F", "#00E676", "#FFD600", "#D500F9", "#FF6D00", "#7C4DFF"]

    fig, ax = plt.subplots(figsize=(8.5, 5.0), dpi=140)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(PANEL_BG)

    # Style borders
    for spine in ax.spines.values():
        spine.set_color("#3F3266")
        spine.set_linewidth(1.2)

    colors = [CYBER_COLORS[i % len(CYBER_COLORS)] for i in range(len(labels))]

    if chart_type in ["pie", "doughnut", "donut"]:
        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            colors=colors,
            autopct="%1.1f%%",
            startangle=140,
            wedgeprops={"edgecolor": BG_COLOR, "linewidth": 2, "antialiased": True},
            pctdistance=0.75 if chart_type in ["doughnut", "donut"] else 0.6
        )
        if chart_type in ["doughnut", "donut"]:
            center_circle = plt.Circle((0, 0), 0.48, fc=PANEL_BG)
            ax.add_artist(center_circle)

        for t in texts:
            t.set_color(TEXT_COLOR)
            t.set_fontsize(10)
        for at in autotexts:
            at.set_color("#FFFFFF")
            at.set_weight("bold")
            at.set_fontsize(9)
        ax.axis("equal")

    elif chart_type in ["horizontal_bar", "barh"]:
        y_pos = range(len(labels))
        bars = ax.barh(y_pos, values, color=colors, edgecolor="#FFFFFF", linewidth=0.6, height=0.6)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, color=TEXT_COLOR, fontsize=10)
        ax.tick_params(colors=TEXT_COLOR)
        ax.grid(axis="x", color=GRID_COLOR, linestyle="--", alpha=0.6)
        # Value tags on end of bars
        for bar in bars:
            w = bar.get_width()
            ax.text(w + (max(values) * 0.015), bar.get_y() + bar.get_height()/2,
                    f"{w:g}", va="center", ha="left", color="#FFFFFF", fontsize=9, fontweight="bold")

    elif chart_type in ["line", "trend"]:
        x_pos = range(len(labels))
        ax.plot(x_pos, values, color="#00E5FF", linewidth=2.8, marker="o",
                markersize=6, markerfacecolor="#FFFFFF", markeredgecolor="#00E5FF", markeredgewidth=2)
        ax.fill_between(x_pos, values, color="#00E5FF", alpha=0.18)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, color=TEXT_COLOR, fontsize=10, rotation=15 if len(labels) > 6 else 0)
        ax.tick_params(colors=TEXT_COLOR)
        ax.grid(color=GRID_COLOR, linestyle="--", alpha=0.6)

    else:  # Default 'bar'
        x_pos = range(len(labels))
        bars = ax.bar(x_pos, values, color=colors, edgecolor="#FFFFFF", linewidth=0.6, width=0.55)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, color=TEXT_COLOR, fontsize=10, rotation=15 if len(labels) > 6 else 0)
        ax.tick_params(colors=TEXT_COLOR)
        ax.grid(axis="y", color=GRID_COLOR, linestyle="--", alpha=0.6)
        # Value tags on top of bars
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, h + (max(values) * 0.015),
                    f"{h:g}", ha="center", va="bottom", color="#FFFFFF", fontsize=9, fontweight="bold")

    ax.set_title(title, color="#FFFFFF", fontsize=13, fontweight="bold", pad=15)
    if x_label and chart_type not in ["pie", "doughnut", "donut"]:
        ax.set_xlabel(x_label, color="#A0AEC0", fontsize=10)
    if y_label and chart_type not in ["pie", "doughnut", "donut"]:
        ax.set_ylabel(y_label, color="#A0AEC0", fontsize=10)

    plt.tight_layout()

    filename = f"{_slugify(title)}_{int(time.time())}.png"
    filepath = CHARTS_DIR / filename
    plt.savefig(filepath, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)

    artifact = {
        "id": f"chart_{int(time.time())}",
        "title": title,
        "type": "chart",
        "file_path": str(filepath),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chart_type": chart_type,
        "labels": labels,
        "values": values
    }
    _set_active_artifact(artifact)

    return f"I've generated the {chart_type} chart \"{title}\" and placed it on The Forge canvas."

# ═══════════════════════════════════════════════════════════════════════════
#  PAGE GENERATION
#
#  One brief in, one complete standalone HTML file out — written for the
#  thing that was actually asked for.
# ═══════════════════════════════════════════════════════════════════════════


# ── local pictures ──────────────────────────────────────────────────────────

IMAGES_DIR = DATA_DIR / "images"
_IMG_SRC = re.compile(r"""(<img\b[^>]*?(?<![-\w])src\s*=\s*)(["'])(.*?)\2""",
                      re.IGNORECASE | re.DOTALL)
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
         ".svg": "image/svg+xml"}

# Big enough for a photo, small enough that a page stays openable.
MAX_EMBED_BYTES = 4 * 1024 * 1024
# A page past this is already carrying its pictures; adding more
# is how a 6KB page became a megabyte and then ran out of memory.
MAX_PAGE_BYTES = 12 * 1024 * 1024


def _picture_shelf():
    """Every picture Helio has fetched, newest first."""
    if not IMAGES_DIR.exists():
        return []
    files = [f for f in IMAGES_DIR.iterdir()
             if f.is_file() and f.suffix.lower() in _MIME]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def _find_picture(reference):
    """Resolve whatever the model wrote in a src back to a file on disk."""
    reference = (reference or "").strip()
    if not reference or reference.startswith(("data:", "http://", "https://")):
        return None

    candidate = Path(reference.replace("file:///", "").replace("file://", ""))
    if candidate.is_absolute() and candidate.exists():
        return candidate

    name = candidate.name.lower()
    if not name:
        return None

    # It may be open on the workshop from somewhere outside the shelf.
    try:
        from tools.system import workshop_tool
        for item in workshop_tool.on_surface("image"):
            here = Path(str(item.get("path", "")))
            if here.name.lower() == name and here.exists():
                return here
    except Exception:
        pass

    shelf = _picture_shelf()
    for path in shelf:
        if path.name.lower() == name:
            return path
    # A near miss — the model shortened or reworded the filename.
    stem = candidate.stem.lower()
    for path in shelf:
        if stem and (stem in path.stem.lower() or path.stem.lower() in stem):
            return path
    return None


def _mark(head, name):
    """
    Put data-helio-src inside the <img> tag, not in front of it.

    `head` is everything from "<img" up to and including "src=", so the
    attribute belongs immediately after the tag name.
    """
    if "data-helio-src" in head.lower():
        return head
    return head[:4] + ' data-helio-src="' + name + '"' + head[4:]


def _embed_local_images(html):
    """
    Turn every local <img src> into an inline data URI.

    A page is written to data/forge/apps and the pictures live in
    data/forge/images, so a bare filename resolves to nothing and the page
    shows a broken icon. Inlining also means the finished page still has its
    pictures when it is moved, opened from anywhere, or sent to someone.
    """
    if "<img" not in html.lower() and "url(" not in html.lower():
        return html, 0
    if len(html) > MAX_PAGE_BYTES:
        print(f"[Forge] page is {len(html):,} chars — not embedding further")
        return html, 0

    embedded = [0]

    def swap(match):
        head, quote, reference = match.group(1), match.group(2), match.group(3)
        path = _find_picture(reference)
        if path is None:
            return match.group(0)
        try:
            blob = path.read_bytes()
        except Exception:
            return match.group(0)
        if len(blob) > MAX_EMBED_BYTES:
            # Too heavy to inline; an absolute URL at least renders locally.
            return f"{head}{quote}file:///" + str(path).replace("\\", "/") + quote
        mime = _MIME.get(path.suffix.lower(), "image/jpeg")
        embedded[0] += 1
        # The filename rides along INSIDE the tag, so the page can be handed
        # back to the model later without a megabyte of base64 going with it.
        # It has to go after "<img" — put it before, and the browser renders
        # the attribute as text sitting above the picture.
        return (_mark(head, path.name) + quote + "data:" + mime + ";base64,"
                + base64.b64encode(blob).decode("ascii") + quote)

    html = _IMG_SRC.sub(swap, html)

    def swap_css(match):
        reference = match.group("ref")
        path = _find_picture(reference)
        if path is None:
            return match.group(0)
        try:
            blob = path.read_bytes()
        except Exception:
            return match.group(0)
        if len(blob) > MAX_EMBED_BYTES:
            return "url('file:///" + str(path).replace("\\", "/") + "')"
        mime = _MIME.get(path.suffix.lower(), "image/jpeg")
        embedded[0] += 1
        return ("url('data:" + mime + ";base64,"
                + base64.b64encode(blob).decode("ascii") + "')")

    return _CSS_URL.sub(swap_css, html), embedded[0]



_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_NAME_ATTR = re.compile(r"""data-helio-src\s*=\s*(["'])(?P<name>.*?)\1""",
                        re.IGNORECASE)
_SRC_DATA = re.compile(r"""(?P<head>(?<![-\w])src\s*=\s*)(["'])data:[^"']*\2""",
                       re.IGNORECASE)
# Pictures referenced from CSS rather than an <img> tag.
_CSS_URL = re.compile(
    r"""url\(\s*(["']?)(?P<ref>(?!data:|https?:|file:)[^)"']+)\1\s*\)""",
    re.IGNORECASE)
_BARE_DATA = re.compile(
    r"""(?P<head><img\b[^>]*?(?<![-\w])src\s*=\s*)(["'])"""
    r"""data:image/[a-z+]+;base64,(?P<blob>[A-Za-z0-9+/=\s]{200,})\2""",
    re.IGNORECASE)
# Pages written while the marker was being emitted in the wrong place carry it
# as loose text in front of the tag, where a browser draws it above the image.
_STRAY_MARK = re.compile(
    r"""data-helio-src\s*=\s*(["']).*?\1\s*(?=<img\b)""", re.IGNORECASE)


def _identify(blob_b64):
    """Which picture on the shelf is this? Matched on its actual bytes."""
    try:
        head = base64.b64decode(
            re.sub(r"\s+", "", blob_b64)[:344] + "==", validate=False)[:200]
    except Exception:
        return None
    if not head:
        return None
    for path in _picture_shelf():
        try:
            if path.open("rb").read(200) == head:
                return path.name
        except Exception:
            continue
    return None


def _dehydrate(html):
    """
    Swap embedded pictures back for their filenames.

    Revising a page means handing it to the model as context. A megabyte of
    base64 in that prompt is slow, expensive, and comes back echoed and
    re-embedded until the page is unusable — so the model only ever sees
    src="sourdough.jpg".

    Works tag by tag rather than on a fixed attribute order, because a model
    rewriting a page will happily reshuffle the attributes.
    """
    html = _STRAY_MARK.sub("", html or "")

    def shrink(match):
        tag = match.group(0)
        named = _NAME_ATTR.search(tag)
        if not named:
            return tag
        name = named.group("name")
        tag = _SRC_DATA.sub(
            lambda hit: hit.group("head") + '"' + name + '"', tag)
        # Drop the marker too: the filename is back in src, and leaving it
        # means the next embed adds a second one and the tag grows each pass.
        return _NAME_ATTR.sub("", tag).replace("<img  ", "<img ")

    html = _IMG_TAG.sub(shrink, html)

    # A CSS background carries no marker to name it, so match it by bytes.
    def shrink_css(match):
        name = _identify(match.group("blob"))
        return f"url('{name}')" if name else match.group(0)

    html = re.sub(
        r"""url\(\s*["']?data:image/[a-z+]+;base64,"""
        r"""(?P<blob>[A-Za-z0-9+/=\s]{200,}?)["']?\s*\)""",
        shrink_css, html, flags=re.IGNORECASE)

    # Pages built before the marker existed have a bare data URI and nothing
    # saying where it came from. Identify those by their bytes.
    def recover(match):
        name = _identify(match.group("blob"))
        if name is None:
            return match.group(0)
        return _mark(match.group("head"), name) + '"' + name + '"'

    return _BARE_DATA.sub(recover, html)


# Words that mean the request is actually about a picture.
_WANTS_PICTURE = re.compile(
    r"\b(image|images|picture|pictures|pic|pics|photo|photos|photograph|"
    r"logo|icon|banner|illustration|artwork|screenshot|thumbnail|gallery|"
    r"hero\s+shot)\b", re.IGNORECASE)

# "use the one on screen" refers to a picture without naming it as one.
_MEANS_ON_SCREEN = re.compile(
    r"\b(on\s+(?:the\s+)?screen|on\s+(?:the\s+)?workshop|on\s+(?:the\s+)?surface|"
    r"up\s+there|that\s+one|the\s+one\s+(?:i|you)\s+(?:opened|put\s+up)|"
    r"currently\s+open|already\s+open)\b", re.IGNORECASE)


def _picture_on_screen():
    """The filename of the picture open on the workshop, if there is one."""
    try:
        from tools.system import workshop_tool
        item = workshop_tool.picture_on_screen()
    except Exception:
        return ""
    if not item:
        return ""
    path = Path(str(item.get("path", "")))
    return path.name if path.name else ""


def _picture_block(brief=""):
    """
    The one picture the user is pointing at, if they are pointing at one.

    NOT the answer to "a page with pictures" — that is what a helio: search
    is for. Offering a list of whatever happens to be on disk alongside it
    meant the model picked from the list, and a portfolio came out with a cat,
    an espresso and a croissant on it.
    """
    shelf = _picture_shelf()[:12]
    on_screen = _picture_on_screen()
    if not shelf and not on_screen:
        return ""

    text = str(brief or "")
    lowered = text.lower()

    # Either "the one on screen", or a filename said out loud.
    pointed = bool(on_screen and _MEANS_ON_SCREEN.search(text))
    if not pointed:
        pointed = any(f.name.lower() in lowered or
                      (len(f.stem) > 6 and f.stem.lower() in lowered)
                      for f in shelf)
    if not pointed:
        return ""

    rows = []
    if on_screen:
        rows.append(f"  {on_screen}   <- THIS ONE is open on the workshop "
                    "right now; it is the one meant by \"on screen\", "
                    "\"that picture\" or \"the one you opened\"")
    rows += [f"  {f.name}" for f in shelf if f.name != on_screen]

    return (
        "\n\nTHE PICTURE THEY MEAN\n"
        "The request points at a picture already on this machine. Use it by\n"
        "writing its filename exactly as given into an <img src=\"...\">.\n"
        "This is only for the picture being referred to — every OTHER photo\n"
        "on the page should still be a helio: search.\n"
        + "\n".join(rows))


_FENCE = re.compile(r"^\s*```(?:html|HTML)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)

_PAGE_BRIEF = """You are Helio, building something on the user's machine.

THE REQUEST
{brief}

Build it properly. It will be opened in a real browser with a working
internet connection.

FILES
- One page: output the HTML file on its own, starting <!DOCTYPE html>.
- Anything bigger: split it into real files and mark each one like this,
  each marker on its own line:

--- FILE: index.html ---
...the file...
--- FILE: css/style.css ---
...the file...
--- FILE: js/app.js ---
...the file...

  Use several files when the thing deserves them: a site with more than one
  page, or enough CSS and JavaScript that one file becomes unreadable. The
  entry point must be index.html and must link the others by relative path.
  Do not split a small page for the sake of it.
- Output files and nothing else. No explanation, no markdown fences.

YOU ARE ONLINE — USE IT
- Load libraries from a CDN when they genuinely help. Use EXACTLY these URLs
  — a guessed one 404s and the page renders unstyled:
    Tailwind    <script src="https://cdn.tailwindcss.com"></script>
                (a SCRIPT, not a stylesheet — there is no prebuilt
                 tailwind.min.css on npm and linking one gives you nothing)
    Bootstrap   https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css
    Bootstrap JS https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js
    Font Awesome https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css
    Chart.js    https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js
    Alpine.js   https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js
    GSAP        https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js
    Bulma       https://cdn.jsdelivr.net/npm/bulma@1.0.2/css/bulma.min.css
  If you want a library not on this list, write the CSS yourself instead of
  guessing a URL. Plain hand-written CSS is always a safe choice.
- Use Google Fonts. Pick a typeface that suits the subject.
- For photographs, ask Helio for them:
    <img src="helio:espresso cup on a wooden counter" alt="Espresso">
  Everything after "helio:" is a search — Helio finds a real photo of exactly
  that and puts it in the page before it is saved. Describe each one properly
  and differently ("barista pulling a shot", "flaky butter croissant"), not
  the same word every time. Up to 8 per page.
  For faces and logos: https://ui-avatars.com/api/?name=First+Last&size=256
  For abstract texture where the subject genuinely does not matter:
    https://picsum.photos/seed/SEED/WIDTH/HEIGHT
  Never invent a URL to a specific file on some site; it will 404.
- A helio: image needs no fallback — Helio resolves it before saving. Any
  OTHER remote <img> gets one, because a photo host will occasionally 500 and
  a broken icon ruins the page:
    <img src="https://example.com/x.jpg" alt="X"
         onerror="this.onerror=null;this.src='https://picsum.photos/seed/x/400/300'">
- Inline SVG is still the right answer for icons, diagrams and logos.
- Do NOT invent links to real sites. A project card must not link to
  github.com/someone/some-repo unless the user gave you that URL — it will
  404. Use href="#" for anything you do not have a real address for.

MAKE IT REAL
- Build what was asked for, not a placeholder. A bakery gets bakery copy,
  real-sounding products and prices. A calculator's buttons must calculate.
  A dashboard invents plausible data and renders it. No "Lorem ipsum", no
  "Your content here", no dead links, no buttons that do nothing.
- Wire up the interactions in JavaScript so they genuinely work.

MAKE IT LOOK RIGHT
- Let the subject choose the look. A bakery is warm and light; a trading
  dashboard is dense and dark; a kid's game is bright. Do NOT default to
  neon-on-black cyberpunk unless the request actually calls for it.
- A real type scale and a deliberate palette. Enough content that it feels
  finished rather than a demo.
- Responsive: it is viewed in a panel around 900px wide and also full screen.

Begin now.{pictures}"""

_REVISE_BRIEF = """You are Helio, editing something you built on the user's machine.

THE CHANGE THEY ASKED FOR
{change}

WHAT IS THERE NOW
{current}

Apply the change and output the COMPLETE result.

- Output files and nothing else. No explanation, no markdown fences.
- If what you were given is a single HTML file, return a single HTML file.
  If it came as several marked files, return every file the same way, each
  one whole:

--- FILE: index.html ---
...the file...

- Change what was asked and leave the rest alone.
- You are online: CDN libraries, Google Fonts and real image URLs are fine,
  as are the ones already in the file.{pictures}"""


def _clean_html(raw: str) -> str:
    """
    Pull the HTML file out of whatever the model wrapped it in.

    Models fence code, and smaller ones sometimes chat before and after it.
    Everything outside <!DOCTYPE ...> ... </html> is dropped.
    """
    text = (raw or "").strip()

    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    else:
        # An unterminated fence — the stream was cut off mid-file.
        text = re.sub(r"^\s*```(?:html|HTML)?\s*\n", "", text)
        text = re.sub(r"\n\s*```\s*$", "", text)

    lower = text.lower()
    start = lower.find("<!doctype")
    if start == -1:
        start = lower.find("<html")
    if start > 0:
        text = text[start:]
        lower = text.lower()

    end = lower.rfind("</html>")
    if end != -1:
        text = text[:end + len("</html>")]

    return text.strip()



# ── projects of more than one file ──────────────────────────────────────────

_FILE_MARK = re.compile(
    r"^[ \t]*[-=]{2,}[ \t]*FILE[ \t]*:[ \t]*(?P<path>[^\n<>:\"|?*]+?)[ \t]*"
    r"[-=]{2,}[ \t]*$",
    re.MULTILINE)

MAX_PROJECT_FILES = 24


def _safe_relpath(raw):
    """
    A relative path inside the project folder, or None.

    The model writes these, so an absolute path or a walk up out of the
    folder has to be refused rather than trusted.
    """
    cleaned = str(raw or "").strip().strip("/\\").replace("\\", "/")
    if not cleaned or cleaned.startswith("/"):
        return None
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    if Path(cleaned).is_absolute() or ":" in parts[0]:
        return None
    return "/".join(parts)[:120]


def _split_files(text):
    """
    Break the model's output into (relative path, content) pairs.

    No markers means it wrote a single page, which is the common case and
    stays a single file on disk.
    """
    text = (text or "").strip()
    marks = list(_FILE_MARK.finditer(text))
    if not marks:
        return []

    files = []
    for index, mark in enumerate(marks):
        stop = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        body = text[mark.end():stop].strip("\n")
        body = _strip_fence(body)
        path = _safe_relpath(mark.group("path"))
        if path and body.strip():
            files.append((path, body))
    return files[:MAX_PROJECT_FILES]


def _strip_fence(body):
    """Models fence individual files even when told not to."""
    body = body.strip()
    fenced = re.match(r"^```[\w+#./-]*\s*\n(.*?)\n?```$", body, re.DOTALL)
    return fenced.group(1) if fenced else body


def _entry_file(files):
    """Which file the browser should open."""
    for path, _ in files:
        if path.lower() == "index.html":
            return path
    for path, _ in files:
        if path.lower().endswith((".html", ".htm")):
            return path
    return files[0][0] if files else ""


def _save_project(files, title, brief, kind="website"):
    """Write a multi-file build into its own folder under apps/."""
    _ensure_dirs()
    root = APPS_DIR / f"{_slugify(title)}_{int(time.time())}"
    root.mkdir(parents=True, exist_ok=True)

    written = []
    for relpath, body in files:
        target = root / relpath
        # Belt and braces: the resolved path must still be inside the folder.
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() in (".html", ".htm"):
            body, fetched = _resolve_helio_images(body)
            for note in fetched:
                print(f"[Forge] {relpath}: {note}")
            body = _embed_local_images(body)[0]
            body, repairs = verify_assets(body)
            for note in repairs:
                print(f"[Forge] {relpath}: {note}")
        target.write_text(body, encoding="utf-8")
        written.append(relpath)

    entry = _entry_file([(p, "") for p in written])
    artifact = {
        "id": f"project_{int(time.time())}",
        "title": title,
        "type": kind,
        "brief": brief,
        "root": str(root),
        "files": written,
        "file_path": str(root / entry) if entry else str(root),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _set_active_artifact(artifact)
    return artifact


def read_project(artifact):
    """A project rendered back into the marked format the model expects."""
    root = artifact.get("root")
    files = artifact.get("files") or []
    if not root or not files:
        return ""
    chunks = []
    for relpath in files:
        path = Path(root) / relpath
        if not path.exists():
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        chunks.append(f"--- FILE: {relpath} ---\n{_dehydrate(body)}")
    return "\n\n".join(chunks)



# ── the assets a page depends on ────────────────────────────────────────────

# Verified working, so a dead link to one of these can be repaired rather than
# just dropped. Keyed by the library name as it appears in a URL.
KNOWN_CDN = {
    "bootstrap.min.css":
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css",
    "bootstrap.bundle.min.js":
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js",
    "font-awesome":
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css",
    "chart.js":
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js",
    "alpinejs":
        "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js",
    "gsap":
        "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js",
    "bulma":
        "https://cdn.jsdelivr.net/npm/bulma@1.0.2/css/bulma.min.css",
}

TAILWIND_CDN = "https://cdn.tailwindcss.com"

_STYLESHEET = re.compile(
    r"""<link\b[^>]*?\bhref\s*=\s*(["'])(?P<url>https?://[^"']+)\1[^>]*>""",
    re.IGNORECASE)
_SCRIPT_SRC = re.compile(
    r"""<script\b[^>]*?\bsrc\s*=\s*(["'])(?P<url>https?://[^"']+)\1[^>]*>"""
    r"""\s*</script>""",
    re.IGNORECASE)

ASSET_TIMEOUT = 8
MAX_ASSET_CHECKS = 12


def _asset_alive(url):
    """Does this actually serve something? HEAD first, GET if HEAD is refused."""
    import requests
    headers = {"User-Agent": "Mozilla/5.0 Chrome/124.0 Safari/537.36"}
    try:
        response = requests.head(url, headers=headers, timeout=ASSET_TIMEOUT,
                                 allow_redirects=True)
        if response.status_code in (403, 405, 501):
            response = requests.get(url, headers=headers,
                                    timeout=ASSET_TIMEOUT, stream=True)
        return response.status_code < 400
    except Exception:
        return False


def _known_replacement(url):
    """A verified URL for the same library, if this is one we know."""
    lowered = url.lower()
    if "tailwind" in lowered:
        return TAILWIND_CDN
    for key, good in KNOWN_CDN.items():
        if key.lower() in lowered:
            return good
    return None


def verify_assets(html):
    """
    Make sure the stylesheets and scripts a page loads actually exist.

    A 404 on a stylesheet does not fail loudly — the page just renders with
    browser defaults, which is exactly how a portfolio built entirely out of
    Tailwind classes came out looking like plain text. Repair what we know,
    drop what we cannot, and say what happened.
    """
    import concurrent.futures

    tags = []
    for match in _STYLESHEET.finditer(html):
        if "stylesheet" in match.group(0).lower():
            tags.append((match.group(0), match.group("url"), "style"))
    for match in _SCRIPT_SRC.finditer(html):
        tags.append((match.group(0), match.group("url"), "script"))

    # preconnect/dns-prefetch hints are not resources; skip them.
    tags = [t for t in tags
            if not re.search(r'rel\s*=\s*["\']?(?:preconnect|dns-prefetch)',
                             t[0], re.IGNORECASE)][:MAX_ASSET_CHECKS]
    if not tags:
        return html, []

    with concurrent.futures.ThreadPoolExecutor(min(6, len(tags))) as pool:
        alive = list(pool.map(lambda t: _asset_alive(t[1]), tags))

    notes = []
    for (tag, url, kind), ok in zip(tags, alive):
        if ok:
            continue
        replacement = _known_replacement(url)
        if replacement:
            # Tailwind's CDN is a script; a <link> to it does nothing.
            if replacement == TAILWIND_CDN:
                new_tag = f'<script src="{TAILWIND_CDN}"></script>'
            elif kind == "style":
                new_tag = f'<link rel="stylesheet" href="{replacement}">'
            else:
                new_tag = f'<script src="{replacement}"></script>'
            html = html.replace(tag, new_tag, 1)
            notes.append(f"replaced a dead {kind} ({url.split('/')[-1][:34]})")
        else:
            html = html.replace(tag, "", 1)
            notes.append(f"dropped a dead {kind} ({url.split('/')[-1][:34]})")
    return html, notes



# ── photographs Helio fetches for itself ────────────────────────────────────

_HELIO_IMG = re.compile(
    r"""(?P<head><img\b[^>]*?(?<![-\w])src\s*=\s*)(["'])helio:\s*"""
    r"""(?P<query>[^"']{2,80})\2""",
    re.IGNORECASE)

# A hero is usually a CSS background rather than an <img>, so the same
# reference has to work inside url() too.
_HELIO_CSS = re.compile(
    r"""url\(\s*(["']?)helio:\s*(?P<query>[^)"']{2,80}?)\s*\1\s*\)""",
    re.IGNORECASE)

MAX_FETCHED_IMAGES = 8


def _resolve_helio_images(html):
    """
    Turn <img src="helio:a thing"> into a real photo of that thing.

    The alternative was a third-party keyword host, which returns 500 for most
    keywords at any given moment; every failure fell back to a random photo,
    so a headshot came out as a forest. Helio's own image search actually
    matches the subject.
    """
    import concurrent.futures

    wanted = []
    for pattern in (_HELIO_IMG, _HELIO_CSS):
        for match in pattern.finditer(html):
            query = " ".join(match.group("query").split())
            if query and query not in wanted:
                wanted.append(query)
    if not wanted:
        return html, []

    wanted = wanted[:MAX_FETCHED_IMAGES]

    def grab(query):
        try:
            from tools.web_ops.image_finder import fetch_image
            path, _title, _source = fetch_image(query)
            return query, path
        except Exception as e:
            print(f"[Forge] could not fetch '{query}': {e}")
            return query, None

    with concurrent.futures.ThreadPoolExecutor(min(4, len(wanted))) as pool:
        found = dict(pool.map(grab, wanted))

    notes = []

    def swap(match):
        query = " ".join(match.group("query").split())
        path = found.get(query)
        if not path:
            # Nothing found: a seeded placeholder still renders.
            seed = re.sub(r"\W+", "-", query)[:40] or "photo"
            notes.append(f"no photo found for '{query[:30]}'")
            return (match.group("head") + '"https://picsum.photos/seed/'
                    + seed + '/800/600"')
        notes.append(f"fetched a photo of '{query[:30]}'")
        return match.group("head") + '"' + Path(path).name + '"'

    def swap_css(match):
        query = " ".join(match.group("query").split())
        path = found.get(query)
        if not path:
            seed = re.sub(r"\W+", "-", query)[:40] or "photo"
            notes.append(f"no photo found for '{query[:30]}'")
            return f"url('https://picsum.photos/seed/{seed}/1600/900')"
        notes.append(f"fetched a background of '{query[:30]}'")
        return f"url('{Path(path).name}')"

    html = _HELIO_IMG.sub(swap, html)
    return _HELIO_CSS.sub(swap_css, html), notes


def _fallback_page(brief: str, note: str) -> str:
    """
    Shown when generation fails outright. Deliberately plain — a apologetic
    note that says what happened beats a fake finished page.
    """
    safe = (brief or "your request").replace("<", "&lt;").replace(">", "&gt;")
    reason = (note or "").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Build failed</title>
<style>
  body {{ background:#0B0A08; color:#E8E2D4; margin:0;
         font-family:Consolas,"Courier New",monospace; padding:48px; }}
  .rule {{ height:1px; background:#4A4032; margin:22px 0; }}
  h1 {{ font-size:15px; letter-spacing:2px; text-transform:uppercase;
        color:#FFB900; font-weight:600; margin:0; }}
  p {{ font-size:13px; line-height:1.7; color:#9A917E; max-width:60ch; }}
  code {{ color:#E8E2D4; }}
</style>
</head>
<body>
  <h1>Forge — build failed</h1>
  <div class="rule"></div>
  <p>Helio could not write this one.</p>
  <p>Asked for: <code>{safe}</code></p>
  <p>{reason}</p>
  <p>The model may be unreachable or overloaded. Ask again and it will retry.</p>
</body>
</html>"""


_LAST_FAILURE = {"why": ""}


def _why_failed():
    """The reason the last generation failed, in a sentence."""
    why = _LAST_FAILURE.get("why") or ""
    if why:
        return why if why.endswith((".", "!", "?")) else why + "."
    return "the model didn't return anything usable."


def _generate_page(brief: str, prompt: str = None) -> tuple:
    """
    Stream a build out of the model.

    Returns (result, ok) where result is either one HTML string or a list of
    (path, content) pairs when the model split the work into real files.
    Fragments are pushed to the panel as they arrive so the build can be
    watched rather than waited on.
    """
    from core.llm import generate_stream

    _emit("start", {"brief": brief})

    collected = []
    try:
        for fragment in generate_stream(
            prompt or _PAGE_BRIEF.format(brief=brief,
                                         pictures=_picture_block(brief)),
            role="code"
        ):
            collected.append(fragment)
            _emit("chunk", fragment)
    except Exception as e:
        _LAST_FAILURE["why"] = f"generation failed: {e}"
        _emit("error", str(e))
        return _fallback_page(brief, f"Generation failed: {e}"), False

    raw = "".join(collected)

    # generate_stream falls back to generate(), which returns its error as a
    # string. Without this the reply reads "the model didn't return anything
    # usable", which sounds like bad output rather than a model that is not
    # running at all.
    if raw.lstrip().startswith("LLM Error:"):
        detail = raw.strip()[len("LLM Error:"):].strip()
        unreachable = ("refused" in detail or "Max retries" in detail
                       or "NewConnectionError" in detail)
        note = ("Ollama isn't reachable — check it's running "
                "(`ollama serve`)." if unreachable else detail[:160])
        _LAST_FAILURE["why"] = note
        _emit("error", note)
        return _fallback_page(brief, note), False

    # Several marked files, or one page. A project is checked on its entry
    # file, since only that one has to be HTML.
    files = _split_files(raw)
    if files:
        entry = _entry_file(files)
        body = dict(files).get(entry, "")
        if len(body) < 120 or "<html" not in body.lower():
            _LAST_FAILURE["why"] = (
                "the files it returned had no usable index.html")
            _emit("error", "the project has no usable entry page")
            return _fallback_page(
                brief, "The model returned files, but no usable index.html."), False
        _emit("done", body)
        return files, True

    html = _clean_html(raw)

    # A model that answered with prose, or died two lines in, leaves
    # something that is not a page. Better to say so than to save it.
    if len(html) < 200 or "<html" not in html.lower():
        _LAST_FAILURE["why"] = (
            "the model replied, but not with a usable page")
        _emit("error", "model did not return a page")
        return _fallback_page(
            brief, "The model replied, but not with a usable HTML file."), False

    _LAST_FAILURE["why"] = ""
    _emit("done", html)
    return html, True


def _save_page(html: str, title: str, brief: str, kind: str = "website") -> dict:
    _ensure_dirs()
    html, fetched = _resolve_helio_images(html)
    for note in fetched:
        print(f"[Forge] {note}")
    html, embedded = _embed_local_images(html)
    if embedded:
        print(f"[Forge] embedded {embedded} local picture(s) into the page")
    html, repairs = verify_assets(html)
    for note in repairs:
        print(f"[Forge] {note}")
    filename = f"{_slugify(title)}_{int(time.time())}.html"
    filepath = APPS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    artifact = {
        "id": f"page_{int(time.time())}",
        "title": title,
        "type": kind,
        "brief": brief,
        "file_path": str(filepath),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": html,
    }
    _set_active_artifact(artifact)
    return artifact


_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _title_from_page(html: str) -> str:
    """
    The name the page gave itself.

    The model writes a proper <title> for the thing it just built — "Rose &
    Rye Bakery" — which beats anything derived from the raw brief, where a
    request like "my mum's bakery called Rose & Rye, in Coimbatore - show
    the breads and prices" truncates into nonsense.
    """
    match = _TITLE_TAG.search(html or "")
    if not match:
        return ""
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    # Entities the model may have escaped in the tag.
    for entity, char in (("&amp;", "&"), ("&#39;", "'"), ("&quot;", '"'),
                         ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " ")):
        title = title.replace(entity, char)
    return title.strip()[:70]


def _built_line(title, count):
    """How a finished build reads back. "1 files" is not a sentence."""
    if count > 1:
        return (f"Built \"{title}\" — {count} files, live on the Forge canvas. "
                "Open it, edit the source, or tell me what to change.")
    return (f"Built \"{title}\" — it's live on the Forge canvas. "
            "You can open it, edit the source, or tell me what to change.")


def _title_from_brief(brief: str) -> str:
    """Fallback name, used when the page carries no usable <title>."""
    text = re.sub(
        r"^\s*(?:a|an|the)\s+", "", (brief or "").strip(), flags=re.IGNORECASE)
    # Stop at the first clause break — the rest of a spoken brief is detail,
    # not a name.
    text = re.split(r"\s*[,;:]\s*|\s+[-–—]\s+", text)[0]
    text = re.sub(r"\s+", " ", text).strip(" .!?")
    if not text:
        return "Untitled build"
    return (text[:1].upper() + text[1:])[:70]


@tool(
    name="forge_create_website",
    description=(
        "Build a real, working web page or website about anything, and show it "
        "live in The Forge. Helio writes the whole page itself — a bakery site "
        "looks like a bakery, a dashboard looks like a dashboard. "
        "Use for 'make me a website for X', 'build a page about X', "
        "'create a site for X'. "
        "'topic' is what the page should actually be, in the user's own words."
    ),
    parameters={
        "topic": "string",
        "title": "string",
    },
    required=["topic"],
)
def handle_forge_create_website(action_data):
    topic = str(action_data.get("topic", "") or "").strip()
    if not topic:
        return "What should the page be about?"

    given = str(action_data.get("title", "") or "").strip()

    result, ok = _generate_page(topic)

    if isinstance(result, list):
        entry_body = dict(result).get(_entry_file(result), "")
        title = given or _title_from_page(entry_body) or _title_from_brief(topic)
        artifact = _save_project(result, title, topic, kind="website")
        return _built_line(title, len(artifact.get("files", [])))

    # The page names itself better than the brief does; an explicit title
    # from the caller still wins. A failed build gets the brief's name — the
    # fallback page is titled "Build failed", which is not what the user asked
    # for and is not what should appear on the rack.
    title = given or (_title_from_page(result) if ok else "")         or _title_from_brief(topic)
    _save_page(result, title, topic, kind="website")

    if not ok:
        return (f"I couldn't build \"{title}\" — {_why_failed()} "
                "It's on the Forge canvas with the reason.")

    return (f"Built \"{title}\" — it's live on the Forge canvas. "
            "You can open it, edit the source, or tell me what to change.")


@tool(
    name="forge_create_app",
    description=(
        "Build a small interactive tool or app as a working web page — a timer, "
        "a calculator, a tracker, a game. Shows live in The Forge. "
        "Use for 'make me a pomodoro timer', 'build a tip calculator'. "
        "'title' names it; 'brief' says what it should do. Only pass "
        "html_code/css_code/js_code when writing the code yourself."
    ),
    parameters={
        "title": "string",
        "brief": "string",
        "html_code": "string",
        "css_code": "string",
        "js_code": "string",
    },
    required=["title"],
)
def handle_forge_create_app(action_data):
    _ensure_dirs()
    title = str(action_data.get("title", "Web Tool") or "Web Tool").strip()
    brief = str(action_data.get("brief", "") or "").strip() or title
    html_code = str(action_data.get("html_code", "") or "").strip()
    css_code = str(action_data.get("css_code", "") or "").strip()
    js_code = str(action_data.get("js_code", "") or "").strip()

    # No hand-written code: build it properly rather than falling back to a
    # canned page. (The previous version threw away real code here whenever
    # the title happened to contain the word "website".)
    if not html_code:
        result, ok = _generate_page(f"{title}. {brief}".strip())
        if isinstance(result, list):
            artifact = _save_project(result, title, brief, kind="app")
            return _built_line(title, len(artifact.get("files", [])))
        _save_page(result, title, brief, kind="app")
        if not ok:
            return (f"I couldn't build \"{title}\" — the model didn't return a "
                    "usable page. Ask again and I'll retry.")
        return (f"Built \"{title}\" — it's live on the Forge canvas and it "
                "works. Open it, edit it, or tell me what to change.")

    if "<!DOCTYPE html>" in html_code or "<html" in html_code:
        full_content = html_code
    else:
        full_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background:#12100C; color:#E8E2D4; min-height:100vh;
          font-family:"Segoe UI",system-ui,sans-serif;
          display:flex; align-items:center; justify-content:center; padding:24px; }}
  .bench {{ background:#191510; border:1px solid #4A4032;
            padding:32px; max-width:680px; width:100%; }}
  h1 {{ font-size:14px; font-weight:600; letter-spacing:2px;
        text-transform:uppercase; color:#FFB900; margin-bottom:22px; }}
  button {{ background:transparent; color:#FFB900; border:1px solid #6B5B3E;
            padding:9px 18px; font-size:13px; cursor:pointer; }}
  button:hover {{ border-color:#FFB900; background:rgba(255,185,0,0.10); }}
  input, textarea, select {{ background:#0F0D09; border:1px solid #4A4032;
            color:#E8E2D4; padding:9px 12px; font-size:14px; width:100%;
            margin-bottom:14px; outline:none; }}
  input:focus, textarea:focus {{ border-color:#FFB900; }}
{css_code}
</style>
</head>
<body>
  <div class="bench">
    <h1>{title}</h1>
{html_code}
  </div>
  <script>
{js_code}
  </script>
</body>
</html>"""

    artifact = _save_page(full_content, title, brief, kind="app")
    return (f"Created \"{title}\". It's on the Forge canvas — open it, edit the "
            "source, or tell me what to change.")


@tool(
    name="forge_revise",
    description=(
        "Change the thing currently open in The Forge — restyle it, add a "
        "section, fix something, change the colours. "
        "Use for 'make the buttons blue', 'add a contact form', "
        "'change it to dark mode', 'make the text bigger', "
        "'put the picture on screen into the portfolio you made'. "
        "'change' is what to alter, in the user's own words. "
        "'which' names an earlier build to change instead of the current one."
    ),
    parameters={"change": "string", "which": "string"},
    required=["change"],
)
def handle_forge_revise(action_data):
    change = str(action_data.get("change", "") or "").strip()
    if not change:
        return "What should I change about it?"

    # "in the last website you made" is not necessarily what is on the bench.
    which = str(action_data.get("which", "") or "").strip()
    active = find_build(which) if which else get_active_artifact()
    if which and active is None:
        return (f"I couldn't find a build matching '{which}'. "
                "Ask what I've built if you want the list.")
    active = active or {}

    path = active.get("file_path", "")
    if not active or not path:
        return "There's nothing open in the Forge yet. Ask me to build something first."

    if active.get("type") == "chart":
        return ("That's a chart, so there's no source to edit — tell me the new "
                "numbers or a different chart type and I'll rebuild it.")

    # A project goes to the model as all of its files; a page as itself.
    current = read_project(active)
    if not current:
        try:
            with open(path, "r", encoding="utf-8") as f:
                current = _dehydrate(f.read())
        except Exception:
            current = active.get("code", "")

    if not current.strip():
        return "I can't read the current build's source to change it."

    title = active.get("title", "the build")
    result, ok = _generate_page(
        change, prompt=_REVISE_BRIEF.format(
            change=change, current=current,
            pictures=_picture_block(change)))

    if not ok:
        return (f"I couldn't change \"{title}\" — {_why_failed()} "
                "The original is untouched.")

    brief = f"{active.get('brief', '')} + {change}".strip(" +")
    if isinstance(result, list):
        artifact = _save_project(result, title, brief,
                                 kind=active.get("type", "website"))
        count = len(artifact.get("files", []))
        detail = f" {count} files," if count > 1 else ""
        return f"Updated \"{title}\" — {change}.{detail} Reloaded on the canvas."

    _save_page(result, title, brief, kind=active.get("type", "website"))
    return f"Updated \"{title}\" — {change}. It's reloaded on the canvas."


@tool(
    name="forge_list_artifacts",
    description=(
        "List everything Helio has built in The Forge — charts, pages, apps, "
        "documents. Use for 'what have you built', 'show me my forge', "
        "'what's in the forge', 'list what you made'."
    ),
    parameters={},
    required=[],
)
def handle_forge_list_artifacts(action_data):
    history = list_forge_history()
    if not history:
        return ("The Forge is empty. Ask me to build something — a chart, a "
                "page, a tool — and it'll show up there.")

    lines = []
    for index, item in enumerate(history[:12], 1):
        kind = str(item.get("type", "build")).upper()
        lines.append("  {}. [{}] {}  — {}".format(
            index, kind, item.get("title", "Untitled"),
            item.get("created_at", "")))

    total = len(history)
    head = f"I've built {total} thing" + ("" if total == 1 else "s") + ":"
    if total > 12:
        return head + "\n" + "\n".join(lines) + f"\n  …and {total - 12} more."
    return head + "\n" + "\n".join(lines)


@tool(
    name="forge_create_document",
    description="Create an editable document, report, or script in The Forge.",
    parameters={
        "title": "string",
        "content": "string",
        "file_type": "string"
    },
    required=["title", "content"]
)
def handle_forge_create_document(action_data):
    _ensure_dirs()
    title = str(action_data.get("title", "Document")).strip()
    content = str(action_data.get("content", "")).strip()
    file_type = str(action_data.get("file_type", "markdown")).lower()

    ext = ".md" if file_type in ["markdown", "md"] else (".py" if file_type == "python" else ".txt")
    filename = f"{_slugify(title)}_{int(time.time())}{ext}"
    filepath = DOCS_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    artifact = {
        "id": f"doc_{int(time.time())}",
        "title": title,
        "type": "document",
        "file_path": str(filepath),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": content
    }
    _set_active_artifact(artifact)

    return f"Created document \"{title}\". Available to view and edit in The Forge."


# ═══════════════════════════════════════════════════════════════════════════
#  CODE
#
#  Writing programs, and changing ones that already exist on this machine.
# ═══════════════════════════════════════════════════════════════════════════

CODE_DIR = DATA_DIR / "code"

_LANG_SUFFIX = {
    "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
    "typescript": ".ts", "ts": ".ts", "html": ".html", "css": ".css",
    "java": ".java", "c": ".c", "c++": ".cpp", "cpp": ".cpp", "c#": ".cs",
    "csharp": ".cs", "go": ".go", "rust": ".rs", "rs": ".rs", "ruby": ".rb",
    "php": ".php", "sql": ".sql", "bash": ".sh", "shell": ".sh",
    "powershell": ".ps1", "json": ".json", "yaml": ".yaml", "batch": ".bat",
}

_CODE_FENCE = re.compile(r"^\s*```[\w+#-]*\s*\n(.*?)\n\s*```\s*$", re.DOTALL)

_CODE_BRIEF = """You are Helio, writing a program on the user's own machine.

THE REQUEST
{brief}

LANGUAGE
{language}

Write the complete, working program.

HARD RULES
- Output the source and nothing else. No explanation before or after, no
  markdown fences.
- It must run as written. No pseudocode, no "..." placeholders, no TODO
  stubs, no imaginary helper functions you never define.
- Prefer the standard library. Only import a third-party package when the
  task genuinely needs it, and if you do, say so in a comment at the top.
- Handle the obvious failure cases rather than letting it crash on the first
  bad input.
- Comment the parts where the reasoning is not obvious from the code. Do not
  narrate the obvious.

Begin the source now."""

_EDIT_BRIEF = """You are Helio, editing a file on the user's machine.

THE CHANGE THEY ASKED FOR
{change}

THE FILE ({path})
{current}

Apply the change and output the COMPLETE updated file.

HARD RULES
- Output the file and nothing else. No explanation, no markdown fences.
- Change what was asked and leave everything else byte-for-byte alone.
- Keep the existing style: indentation, quoting, naming, comment voice.
- If the change cannot be made safely, output the file completely unchanged.

Begin the file now."""


def _clean_code(raw):
    """Strip fences and any chat the model wrapped the source in."""
    text = (raw or "").strip()
    fenced = _CODE_FENCE.match(text)
    if fenced:
        return fenced.group(1).strip()
    text = re.sub(r"^\s*```[\w+#-]*\s*\n", "", text)
    text = re.sub(r"\n\s*```\s*$", "", text)
    return text.strip()


def _stream_code(prompt, brief):
    """Stream source out of the model, feeding the panel as it arrives."""
    from core.llm import generate_stream

    _emit("start", {"brief": brief})
    collected = []
    try:
        for fragment in generate_stream(prompt, role="code"):
            collected.append(fragment)
            _emit("chunk", fragment)
    except Exception as e:
        _emit("error", str(e))
        return "", False

    code = _clean_code("".join(collected))
    if len(code) < 20:
        _emit("error", "model did not return usable source")
        return code, False
    _emit("done", code)
    return code, True


def _to_workshop(kind, title, payload):
    """Put a result on the workshop surface if one is up (or waiting)."""
    try:
        from tools.system import workshop_tool
        workshop_tool.request("place", kind=kind, title=title, payload=payload)
    except Exception:
        pass


@tool(
    name="forge_create_code",
    description=(
        "Write a working program or script — a scraper, a converter, a bot, a "
        "utility — save it, and show it on the workshop. "
        "Use for 'write me a python script that...', 'build me a program to...', "
        "'make me a tool that...'. "
        "'brief' is what it should do; 'language' defaults to python."
    ),
    parameters={"brief": "string", "language": "string", "name": "string"},
    required=["brief"],
)
def handle_forge_create_code(action_data):
    brief = str(action_data.get("brief", "") or "").strip()
    if not brief:
        return "What should the program do?"

    language = str(action_data.get("language", "") or "python").strip().lower()
    suffix = _LANG_SUFFIX.get(language, ".txt")
    name = str(action_data.get("name", "") or "").strip() or _title_from_brief(brief)

    code, ok = _stream_code(
        _CODE_BRIEF.format(brief=brief, language=language or "python"), brief)
    if not ok:
        return (f"I couldn't write that one — the model didn't return usable "
                "source. Ask again and I'll retry.")

    CODE_DIR.mkdir(parents=True, exist_ok=True)
    path = CODE_DIR / f"{_slugify(name)}_{int(time.time())}{suffix}"
    path.write_text(code, encoding="utf-8")

    artifact = {
        "id": f"code_{int(time.time())}",
        "title": name,
        "type": "code",
        "brief": brief,
        "language": language,
        "file_path": str(path),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code,
    }
    _set_active_artifact(artifact)
    _to_workshop("code", name, str(path))

    lines = code.count("\n") + 1
    return (f"Wrote \"{name}\" — {lines} lines of {language}, saved as "
            f"{path.name}. It's on the bench; open it or tell me what to change.")


@tool(
    name="forge_edit_file",
    description=(
        "Change an existing file on this machine — a source file in the user's "
        "own project, a config, a page they already have. Keeps a backup and "
        "shows what changed. "
        "Use for 'change X in my file at ...', 'edit this file to ...', "
        "'fix the bug in ...', 'add a function to ...'. "
        "'path' is the file; 'change' is what to do to it."
    ),
    parameters={"path": "string", "change": "string"},
    required=["path", "change"],
)
def handle_forge_edit_file(action_data):
    raw_path = str(action_data.get("path", "") or "").strip().strip('"').strip("'")
    change = str(action_data.get("change", "") or "").strip()

    if not raw_path:
        return "Which file should I change?"
    if not change:
        return "What should I change about it?"

    path = Path(raw_path).expanduser()
    if not path.exists() or not path.is_file():
        return f"I can't find a file at '{raw_path}'."

    try:
        original = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"I can't read {path.name} as text — {e}"

    # Pictures Helio inlined are megabytes of base64 the model never needs.
    # Work on the readable version and put them back afterwards.
    lean = _dehydrate(original)

    if len(lean) > 120_000:
        return (f"{path.name} is {len(lean):,} characters — too big for me "
                "to rewrite whole safely. Point me at a smaller file, or tell "
                "me the specific function to change.")

    updated, ok = _stream_code(
        _EDIT_BRIEF.format(change=change, path=path.name, current=lean),
        f"{path.name}: {change}")

    if not ok:
        return (f"I couldn't make that change to {path.name} — the model didn't "
                "return a usable file. Nothing was touched.")

    if updated.strip() == lean.strip():
        return (f"I read {path.name} but didn't change anything — either it "
                "already does that, or the change wasn't safe to make.")

    # Always a backup. This is the user's real project, and a rewrite the model
    # got wrong with no way back is not a trade worth making.
    backup = path.with_suffix(path.suffix + ".helio-bak")
    try:
        backup.write_text(original, encoding="utf-8")
    except Exception as e:
        return f"I stopped before writing — couldn't save a backup ({e})."

    try:
        # Pictures go back in on the way to disk, so the saved file still
        # carries them even though the model only ever saw filenames.
        path.write_text(_embed_local_images(updated)[0], encoding="utf-8")
    except Exception as e:
        return f"Couldn't write {path.name} — {e}. The original is untouched."

    # Diff the readable versions — a diff of two base64 blobs helps nobody.
    diff = "\n".join(difflib.unified_diff(
        lean.splitlines(), updated.splitlines(),
        fromfile=f"{path.name}  (before)", tofile=f"{path.name}  (after)",
        lineterm="", n=3))

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    diff_path = DOCS_DIR / f"{_slugify(path.stem)}_change_{int(time.time())}.diff"
    diff_path.write_text(diff or "(no textual difference)", encoding="utf-8")

    artifact = {
        "id": f"edit_{int(time.time())}",
        # Just the filename. Pasting the whole instruction in here produced
        # titles hundreds of characters long, and a label that wide pushed
        # the Forge panel out past its frame.
        "title": path.name,
        "type": "document",
        "brief": change,
        "file_path": str(path),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": updated,
    }
    _set_active_artifact(artifact)
    _to_workshop("diff", f"{path.name} · what changed", str(diff_path))

    added = sum(1 for line in diff.splitlines()
                if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff.splitlines()
                  if line.startswith("-") and not line.startswith("---"))
    return (f"Changed {path.name} — {added} lines added, {removed} removed. "
            f"The original is saved as {backup.name}, and the diff is on the "
            "workshop so you can see exactly what moved.")


@tool(
    name="forge_scrap",
    description=(
        "Throw away things Helio has built — one of them, or all of them. "
        "Use for 'scrap all builds', 'delete all my builds', 'clear the forge', "
        "'scrap that build', 'delete the sourdough page'. "
        "'which' is 'all', or part of the title or filename of one build. "
        "Scrapped files move to data/forge/scrapped and can be recovered."
    ),
    parameters={"which": "string"},
    required=["which"],
)
def handle_forge_scrap(action_data):
    which = str(action_data.get("which", "") or "").strip()
    history = list_forge_history()

    if not history:
        return "There's nothing on the rack to scrap."

    if which.lower() in ("all", "everything", "the lot", "all builds",
                         "all of them", "all my builds"):
        scrapped = 0
        for record in list(history):
            if delete_artifact(record.get("file_path", "")):
                scrapped += 1
        return ("Scrapped all {} build{} — the rack is empty. The files moved "
                "to data/forge/scrapped if you want any of them back."
                .format(scrapped, "" if scrapped == 1 else "s"))

    if not which:
        return "Which build should I scrap? Name one, or say 'all'."

    # A number from the rack listing.
    target = None
    if which.isdigit():
        index = int(which) - 1
        if 0 <= index < len(history):
            target = history[index]

    if target is None:
        needle = which.lower()
        for record in history:
            title = str(record.get("title", "")).lower()
            name = Path(str(record.get("file_path", ""))).name.lower()
            if needle in title or needle in name:
                target = record
                break

    if target is None:
        return (f"I couldn't find a build matching '{which}'. "
                "Ask what I've built if you want the list.")

    delete_artifact(target.get("file_path", ""))
    return ("Scrapped \"{}\". It moved to data/forge/scrapped if you want it "
            "back.".format(target.get("title", "that build")))


def find_build(which):
    """One of Helio's own builds, by title, filename, or rack number."""
    history = list_forge_history()
    if not history:
        return None

    needle = str(which or "").strip().lower()
    if not needle:
        return history[0]

    if needle.isdigit():
        index = int(needle) - 1
        return history[index] if 0 <= index < len(history) else None

    for record in history:
        if needle == str(record.get("title", "")).lower():
            return record
    for record in history:
        title = str(record.get("title", "")).lower()
        name = Path(str(record.get("file_path", ""))).name.lower()
        if needle in title or needle in name:
            return record

    # Every significant word present, in any order — "ankit singh website"
    # against "Ankit Singh - Fullstack Developer Portfolio".
    words = [w for w in re.split(r"\W+", needle)
             if len(w) > 2 and w not in ("the", "you", "made", "built",
                                         "forge", "website", "site", "page")]
    if words:
        for record in history:
            haystack = (str(record.get("title", "")) + " "
                        + Path(str(record.get("file_path", ""))).name).lower()
            if all(w in haystack for w in words):
                return record
    return None


@tool(
    name="forge_open",
    description=(
        "Open something Helio built before — put it up on the workshop, or "
        "launch it in the browser. "
        "Use for 'open the portfolio you made', 'show me the bakery site you "
        "built', 'open that calculator from the forge'. "
        "'which' names the build. Set 'launch' to true only when the user "
        "wants it in their browser rather than on the workshop."
    ),
    parameters={"which": "string", "launch": "boolean"},
    required=["which"],
)
def handle_forge_open(action_data):
    which = str(action_data.get("which", "") or "").strip()
    launch = bool(action_data.get("launch"))

    record = find_build(which)
    if record is None:
        if not list_forge_history():
            return "I haven't built anything yet."
        return (f"I couldn't find a build matching '{which}'. "
                "Ask what I've built if you want the list.")

    path = record.get("file_path", "")
    title = record.get("title", "that build")
    if not path or not Path(path).exists():
        return f"\"{title}\" is on the rack but its file has gone."

    if launch:
        try:
            os.startfile(path)
            return f"Opened \"{title}\" in your browser."
        except Exception as e:
            return f"Couldn't open \"{title}\" — {e}"

    kind = {"chart": "chart", "image": "image",
            "code": "code", "document": "document"}.get(
        record.get("type"), "web")
    try:
        from tools.system import workshop_tool
        if not workshop_tool.is_open():
            workshop_tool.open_surface()
        workshop_tool.request("place", kind=kind, title=title, payload=path)
    except Exception as e:
        return f"Couldn't put \"{title}\" up — {e}"

    return f"Put \"{title}\" up on the workshop."
