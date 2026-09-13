"""
web_reader.py — using the internet instead of just opening it.

Until now "search the web" meant opening a browser tab and stopping. That is a
shortcut, not a capability: Helio never saw the result, so it could not answer
from it, quote it, or act on it.

These tools fetch and read. `search_and_read` runs a real DuckDuckGo query,
pulls the top pages, and answers from their text with the sources listed;
`read_webpage` does the same for one URL you already have.

Extraction uses lxml, which is already a dependency, rather than adding
readability or trafilatura. The approach is deliberately simple: strip the
furniture (script, style, nav, header, footer, aside), prefer <article> or
<main> when the page marks it, and fall back to the densest text block. That
handles articles and docs well and gives up gracefully on app shells, which is
the right trade for a tool whose failure mode should be "I couldn't read that"
rather than a page of navigation links.
"""
import re
import urllib.parse

from tools.tool_registry import tool

# Big enough for a long article, small enough not to blow the context budget
# when three pages are read at once.
MAX_PAGE_CHARS = 6000
MAX_PAGES = 3
TIMEOUT = 12

_HEADERS = {
    # Some sites serve a stub or a block page to an unknown agent. Identify as
    # a normal browser but stay honest about being Helio.
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36 Helio/1.0"),
    "Accept-Language": "en-GB,en;q=0.9",
}

_STRIP_TAGS = ("script", "style", "noscript", "nav", "header", "footer",
               "aside", "form", "svg", "iframe", "button")


def _clean(text):
    text = re.sub(r"[ \t\xa0]+", " ", text or "")
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_text(html, url=""):
    """Return (title, body_text). Never raises — returns ("", "") on failure."""
    try:
        from lxml import html as lhtml
    except ImportError:
        return "", ""

    try:
        tree = lhtml.fromstring(html)
    except Exception:
        return "", ""

    title = ""
    found = tree.xpath("//title/text()")
    if found:
        title = _clean(found[0])[:160]

    for tag in _STRIP_TAGS:
        for node in tree.xpath("//" + tag):
            node.getparent().remove(node) if node.getparent() is not None else None

    # Prefer what the page itself calls the article.
    body = None
    for path in ("//article", "//main", "//*[@role='main']",
                 "//div[contains(@class,'content')]"):
        candidates = tree.xpath(path)
        if candidates:
            body = max(candidates, key=lambda n: len(n.text_content() or ""))
            if len(body.text_content() or "") > 400:
                break
            body = None

    if body is None:
        body = tree

    # Keep block structure so paragraphs survive as paragraphs.
    parts = []
    for node in body.xpath(".//p | .//h1 | .//h2 | .//h3 | .//li | .//pre"):
        chunk = _clean(node.text_content())
        if len(chunk) > 2:
            parts.append(chunk)

    text = _clean("\n\n".join(parts)) or _clean(body.text_content())
    return title, text[:MAX_PAGE_CHARS]


def fetch(url):
    """Fetch one URL. Returns (title, text, error)."""
    import requests

    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    try:
        response = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
    except Exception as e:
        return "", "", f"{type(e).__name__}: {str(e)[:90]}"

    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type and "text" not in content_type:
        return "", "", f"that URL is {content_type or 'not a web page'}"

    title, text = extract_text(response.text, url)
    if not text:
        return title, "", "the page had no readable text (it may be a web app)"
    return title, text, ""


def normalise_query(query):
    """
    Repair the word order that stripping a command prefix leaves behind.

    "look up what RAG is" loses its prefix and becomes "what RAG is", which a
    search engine reads as a phrase about "what" and returns the wrong pages —
    that exact query came back with the Wikipedia article for "Retrieval".
    Putting the verb back where a question expects it fixes the results.
    """
    text = " ".join((query or "").split())
    if not text:
        return text

    trailing = re.match(
        r"^(what|who|where|when|why|how)\s+(.+?)\s+(is|are|was|were|does|did|means)$",
        text, re.I)
    if trailing:
        lead, subject, verb = trailing.groups()
        return f"{lead.lower()} {verb.lower()} {subject}"

    # A bare "what X" with no verb searches better as just X — but only for a
    # short noun phrase. "who won the world cup in 2022" has a verb of its own
    # and loses meaning if the question word is stripped.
    bare = re.match(r"^what\s+(\S+(?:\s+\S+){0,2})$", text, re.I)
    if bare and not re.search(r"\b(is|are|was|were|does|did|do)\b", text, re.I):
        return bare.group(1)

    return text


def _search(query, limit=MAX_PAGES):
    """DuckDuckGo results as [(title, url, snippet)]. ddgs is already a dep."""
    query = normalise_query(query)
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return []
    try:
        with DDGS() as engine:
            return [
                (r.get("title", ""), r.get("href") or r.get("url", ""),
                 r.get("body", ""))
                for r in engine.text(query, max_results=limit)
            ]
    except Exception:
        return []


def _answer_from(question, pages):
    """Ask the model, grounded strictly in what was fetched."""
    passages = "\n\n".join(
        f"[{i + 1}] {p['title'] or p['url']}\n{p['text'][:2600]}"
        for i, p in enumerate(pages))

    prompt = (
        "Answer the question using ONLY the web pages below.\n\n"
        "Rules:\n"
        "- If the pages don't contain the answer, say so. Don't fill gaps from "
        "your own knowledge.\n"
        "- Be direct: two to four sentences unless more is genuinely needed.\n"
        "- Cite sources as [1], [2] inline where a claim comes from one.\n"
        "- Plain text only.\n\n"
        f"Pages:\n{passages}\n\n"
        f"Question: {question}\nAnswer:"
    )
    try:
        from core.llm import generate
        return generate(prompt, role="chat") or ""
    except Exception as e:
        return f"LLM Error: {e}"



def _pin_to_surface(title, body):
    """
    Put an answer on the workshop when the workshop is open.

    "check the weather and show me here" was answering out loud and leaving
    the surface empty. If there is a surface up, that is where the user is
    looking, so the answer belongs on it. Silent no-op when there is not.
    """
    try:
        from tools.system import workshop_tool
        if not workshop_tool.is_open():
            return False
        workshop_tool.request("place", kind="text", title=title[:60],
                              payload=body)
        return True
    except Exception:
        return False


@tool(
    name="search_and_read",
    description=(
        "Search the web and actually read the results, then answer from them. "
        "Use for any question needing current or external information — news, "
        "documentation, prices, 'what is X', 'how do I Y', 'look up Z'. "
        "This is different from web_search, which only opens a browser tab "
        "without reading anything. Prefer this one when the user wants an "
        "answer rather than a browser window. 'query' is what to look up."
    ),
    parameters={"query": "string"},
    required=["query"],
)
def handle_search_and_read(action_data):
    query = str(action_data.get("query", "") or "").strip()
    if not query:
        return "What should I look up?"

    results = _search(query, MAX_PAGES)
    if not results:
        return (f"I couldn't get search results for '{query}'. "
                "The search service may be unreachable.")

    pages, failures = [], []
    for title, url, snippet in results:
        if not url:
            continue
        page_title, text, error = fetch(url)
        if error or not text:
            failures.append(f"{urllib.parse.urlparse(url).netloc}: {error}")
            continue
        pages.append({"title": page_title or title, "url": url, "text": text})

    if not pages:
        detail = "; ".join(failures[:2]) if failures else "no readable pages"
        return f"I found results for '{query}' but couldn't read any of them ({detail})."

    answer = _answer_from(query, pages)
    if answer.startswith("LLM Error:"):
        top = pages[0]
        return (f"I read {top['title'] or top['url']} but couldn't summarise it "
                f"(the model is unreachable). Opening paragraph:\n\n{top['text'][:600]}")

    sources = "\n".join(
        f"  [{i + 1}] {p['title'][:70] or urllib.parse.urlparse(p['url']).netloc}\n"
        f"      {p['url']}"
        for i, p in enumerate(pages))
    full = f"{answer}\n\nSources:\n{sources}"
    # "show me here" — if the workshop is up, that is where they are looking.
    _pin_to_surface(query, full)
    return full


@tool(
    name="read_webpage",
    description=(
        "Fetch one web page and read it. Use when the user gives a URL and wants "
        "to know what's on it, or asks about a link — 'what does this page say', "
        "'summarise this article', 'read <url>'. "
        "'url' is the address. 'question' is optionally what they want to know "
        "from it; leave empty for a summary."
    ),
    parameters={"url": "string", "question": "string"},
    required=["url"],
)
def handle_read_webpage(action_data):
    url = str(action_data.get("url", "") or "").strip().strip("<>\"'")
    question = str(action_data.get("question", "") or "").strip()

    if not url:
        return "Which page should I read?"

    title, text, error = fetch(url)
    if error:
        return f"I couldn't read that page — {error}."

    ask = question or "Summarise what this page says."
    answer = _answer_from(ask, [{"title": title, "url": url, "text": text}])

    if answer.startswith("LLM Error:"):
        return (f"I read {title or url} but couldn't summarise it (the model is "
                f"unreachable). Opening paragraph:\n\n{text[:600]}")

    return f"{answer}\n\nSource: {title or url}\n{url}"
