"""
image_finder.py — fetch a picture off the internet and put it on the bench.

"find me a cat photo" should end with a cat on the workshop surface that can be
dragged somewhere and used, not with a link to read out. So this searches,
downloads the first result that is genuinely an image, saves it next to
everything else Helio has made, and asks the workshop to put it up.

Auto-discovered by tool_registry.auto_discover().
"""
import re
import time
from pathlib import Path

import requests

from tools.tool_registry import tool

IMAGES_DIR = Path(__file__).resolve().parents[3] / "data" / "forge" / "images"

# Enough candidates that a few dead hosts do not sink the request.
CANDIDATES = 8
MAX_BYTES = 12 * 1024 * 1024
TIMEOUT = 12

# What the first bytes of a real image look like. A host answering an image URL
# with an HTML error page is common enough to be worth checking properly.
MAGIC = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"BM": ".bmp",
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


def _slug(text):
    slug = re.sub(r"[^\w\s-]", "", (text or "image").lower())
    return re.sub(r"[-\s]+", "_", slug).strip("_")[:40] or "image"


def _extension(blob, url):
    for magic, suffix in MAGIC.items():
        if blob.startswith(magic):
            return suffix
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    suffix = Path(url.split("?")[0]).suffix.lower()
    return suffix if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp") else ""


# Stock libraries serve watermarked previews. A hero image with "alamy"
# tiled across it is worse than no photo at all, and image search returns
# these first for most queries.
WATERMARKED = (
    "alamy.com", "shutterstock.com", "dreamstime.com", "gettyimages",
    "istockphoto.com", "123rf.com", "depositphotos.com", "freepik.com",
    "stock.adobe.com", "agefotostock.com", "canstockphoto", "vectorstock.com",
    "bigstockphoto.com", "shutterstock", "photostock", "stockphoto",
    # Adobe Stock's CDN — same watermarked previews under a different name.
    "ftcdn.net", "adobestock", "stockcake", "vecteezy.com", "pond5.com",
)

# Free-licence hosts, which serve the real image with nothing written on it.
PREFERRED = (
    "pxhere.com", "staticflickr.com", "unsplash.com", "images.pexels.com",
    "pixabay.com", "wikimedia.org", "wikipedia.org", "burst.shopifycdn.com",
    "openverse", "publicdomainpictures.net", "stocksnap.io", "pexels.com",
)


def _rank(results):
    """Drop watermarked stock previews, put known-clean hosts first."""
    from urllib.parse import urlparse

    clean, rest = [], []
    for item in results:
        host = urlparse(str(item.get("image", ""))).netloc.lower()
        if any(bad in host for bad in WATERMARKED):
            continue
        (clean if any(good in host for good in PREFERRED) else rest).append(item)
    return clean + rest


def _search_images(query, limit=CANDIDATES):
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return []
    try:
        with DDGS() as engine:
            # Ask for more than needed: most of what comes back for a typical
            # query is watermarked stock and gets thrown away.
            raw = engine.images(query, max_results=max(limit * 3, 18)) or []
    except Exception as e:
        print(f"[Images] search failed: {e}")
        return []

    ranked = _rank(raw)
    if ranked:
        return ranked[:limit]

    # Everything came back watermarked. A long descriptive query tends to hit
    # only stock libraries; the plain subject usually has free photos of it.
    words = [w for w in re.split(r"\W+", query) if len(w) > 2][:2]
    simple = " ".join(words)
    if simple and simple.lower() != query.strip().lower():
        print(f"[Images] only stock for '{query[:36]}' — retrying as '{simple}'")
        try:
            with DDGS() as engine:
                retry = engine.images(simple, max_results=max(limit * 3, 18)) or []
            ranked = _rank(retry)
            if ranked:
                return ranked[:limit]
        except Exception:
            pass

    # Still nothing clean. A hero with "alamy" tiled across it looks broken,
    # so give up here and let the caller fall back to a plain photo.
    print(f"[Images] no unwatermarked photo for '{query[:40]}'")
    return []


# A 4MB hero photo cannot be inlined (it blows the embed cap and falls back to
# a file:/// path that only works on this machine), and nothing on a web page
# needs more than about 1600px across.
MAX_WIDTH = 1600
DOWNSCALE_OVER = 900_000


def _shrink(blob, suffix):
    """Re-encode an oversized photo so a page can carry it."""
    if len(blob) <= DOWNSCALE_OVER:
        return blob, suffix
    try:
        import io
        from PIL import Image
    except Exception:
        return blob, suffix
    try:
        image = Image.open(io.BytesIO(blob))
        image.load()
        if image.width > MAX_WIDTH:
            height = round(image.height * MAX_WIDTH / image.width)
            image = image.resize((MAX_WIDTH, height), Image.LANCZOS)
        if image.mode in ("RGBA", "P", "LA"):
            image = image.convert("RGB")
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=82, optimize=True)
        smaller = out.getvalue()
        if smaller and len(smaller) < len(blob):
            return smaller, ".jpg"
    except Exception as e:
        print(f"[Images] could not downscale: {e}")
    return blob, suffix


def _download(url):
    """Return (bytes, extension) for a real image, or (None, reason)."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        response.raise_for_status()

        declared = int(response.headers.get("content-length") or 0)
        if declared and declared > MAX_BYTES:
            return None, "too large"

        blob = b""
        for chunk in response.iter_content(65536):
            blob += chunk
            if len(blob) > MAX_BYTES:
                return None, "too large"
        if len(blob) < 512:
            return None, "empty"

        suffix = _extension(blob, url)
        if not suffix:
            return None, "not an image"
        return blob, suffix
    except Exception as e:
        return None, str(e)[:60]


def fetch_image(query):
    """
    Search, download, save. Returns (path, title, source_url) or (None, reason, "").
    """
    results = _search_images(query)
    if not results:
        return None, "nothing came back from the image search", ""

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    reasons = []
    for result in results:
        url = result.get("image") or result.get("thumbnail") or ""
        if not url:
            continue
        blob, outcome = _download(url)
        if blob is None:
            reasons.append(outcome)
            continue

        blob, outcome = _shrink(blob, outcome)
        path = IMAGES_DIR / f"{_slug(query)}_{int(time.time())}{outcome}"
        try:
            path.write_bytes(blob)
        except Exception as e:
            reasons.append(str(e)[:50])
            continue
        return str(path), result.get("title", query), result.get("url", "")

    detail = f" ({', '.join(reasons[:3])})" if reasons else ""
    return None, f"none of the results would download{detail}", ""


@tool(
    name="find_image",
    description=(
        "Search the internet for a picture, download it, and put it on the "
        "workshop surface so it can be moved around and used. "
        "Use for 'find me a cat photo', 'get me an image of a sunset', "
        "'find a picture of the Eiffel tower', 'grab a logo for X'. "
        "'query' is what the picture should be of."
    ),
    parameters={"query": "string"},
    required=["query"],
)
def handle_find_image(action_data):
    query = str(action_data.get("query", "") or "").strip()
    if not query:
        return "A picture of what?"

    path, title, source = fetch_image(query)
    if path is None:
        return f"I couldn't get a picture of {query} — {title}."

    try:
        from tools.system import workshop_tool
        if not workshop_tool.is_open():
            workshop_tool.open_surface()
        workshop_tool.request("place", kind="image", title=title[:60] or query,
                              payload=path)
        placed = " It's on the workshop surface."
    except Exception:
        placed = ""

    where = Path(path).name
    return f"Found a picture of {query} and saved it as {where}.{placed}"
