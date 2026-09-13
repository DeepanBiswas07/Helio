import os
import re
import time

SEARCH_DRIVES = ["C:\\", "D:\\"]

ALLOWED_EXTENSIONS = [
    ".pdf", ".doc", ".docx", ".txt",
    ".ppt", ".pptx", ".xls", ".xlsx"
]

EXCLUDE_DIRS = [
    ".bin",
    "windows\\temp",
    "system volume information",
    # Noise directories: these hold thousands of bundled docs/licences that are
    # never what a user means, and walking them dominates scan time.
    "\\windows\\",
    "\\program files",
    "\\programdata\\",
    "appdata\\local\\programs",
    "appdata\\local\\temp",
    "$recycle.bin",
    "node_modules",
    "\\.git\\",
    "\\.venv\\",
    "site-packages",
]

LOCATION_WORDS = {
    "in", "inside", "under", "from", "on", "the", "my",
    "drive", "folder", "directory", "path", "then"
}
FOLDER_END_WORDS = {"folder", "directory", "path"}
QUERY_START_WORDS = {"search", "find", "look", "lookup", "read", "open"}

TYPE_KEYWORDS = {
    "pdf": [".pdf"],
    "doc": [".doc", ".docx"],
    "word": [".doc", ".docx"],
    "ppt": [".ppt", ".pptx"],
    "presentation": [".ppt", ".pptx"],
    "excel": [".xls", ".xlsx"],
    "sheet": [".xls", ".xlsx"],
    "text": [".txt"]
}


def extract_query_features(query):
    q = normalize_text(query)

    features = {
        "query": q,
        "extensions": None,
        "sort": "relevance"
    }

    for key, exts in TYPE_KEYWORDS.items():
        if key in q:
            features["extensions"] = exts
            break

    if "latest" in q or "recent" in q or "new" in q:
        features["sort"] = "date_desc"

    if "oldest" in q:
        features["sort"] = "date_asc"

    if "last week" in q or "yesterday" in q:
        features["sort"] = "date_desc"

    return features


def normalize_text(text):
    text = text.lower()
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_excluded(path):
    path = path.lower()
    return any(ex in path for ex in EXCLUDE_DIRS)


def is_valid_file(file_name):
    file_name = file_name.lower()
    return any(file_name.endswith(ext) for ext in ALLOWED_EXTENSIONS)


def normalize_drive(drive):
    if not drive:
        return None

    drive = drive.strip().upper().replace("/", "\\")

    if re.fullmatch(r"[A-Z]", drive):
        return f"{drive}:\\"

    if re.fullmatch(r"[A-Z]:", drive):
        return f"{drive}\\"

    if re.fullmatch(r"[A-Z]:\\", drive):
        return drive

    return None


def clean_location_tail(text):
    words = re.findall(r"[a-zA-Z0-9_.-]+", text.lower())
    useful_words = []

    for word in words:
        if word in QUERY_START_WORDS and useful_words:
            break

        if word in FOLDER_END_WORDS and useful_words:
            break

        if word in LOCATION_WORDS or re.fullmatch(r"[a-z]", word):
            continue

        useful_words.append(word)

    return useful_words


def find_existing_child(root, folder_words):
    if not folder_words:
        return root

    candidates = [
        os.path.join(root, *folder_words),
        os.path.join(root, " ".join(folder_words)),
        os.path.join(root, "_".join(folder_words)),
        os.path.join(root, "-".join(folder_words)),
    ]

    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate

    current = root
    for word in folder_words:
        if not os.path.isdir(current):
            break

        try:
            matches = [
                entry for entry in os.listdir(current)
                if os.path.isdir(os.path.join(current, entry))
                and entry.lower() == word.lower()
            ]
        except OSError:
            return root

        if not matches:
            return root

        current = os.path.join(current, matches[0])

    return current if os.path.isdir(current) else root


def extract_search_scope(text):
    text = text.strip()

    explicit_path = re.search(r"([a-zA-Z]:\\[^:*?\"<>|\r\n]*)", text)
    if explicit_path:
        path = explicit_path.group(1).strip().rstrip("\\/")
        if os.path.isdir(path):
            return path

    drive_match = re.search(r"\b([a-zA-Z])\s*(?:drive|:)\b", text)
    if not drive_match:
        return None

    root = normalize_drive(drive_match.group(1))
    if not root or not os.path.isdir(root):
        return None

    folder_words = clean_location_tail(text[drive_match.end():])
    return find_existing_child(root, folder_words)


def strip_search_scope(text):
    text = re.sub(r"([a-zA-Z]:\\[^:*?\"<>|\r\n]*)", " ", text)
    text = re.sub(r"\b(?:in|inside|under|from|on)\s+[a-zA-Z]\s*(?:drive|:)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[a-zA-Z]\s*(?:drive|:)\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def match_score(file_name, query):
    name = normalize_text(file_name)
    query = normalize_text(query)

    name_words = name.split()
    query_words = query.split()

    score = 0

    if query in name:
        score += 50

    matches = 0
    for word in query_words:
        if word in name_words:
            matches += 1
            score += 15

    if matches >= max(1, len(query_words) - 1):
        score += 30

    if not matches:
        if query.replace(" ", "") in name.replace(" ", ""):
            score += 25

    return score


def get_search_roots(root=None):
    if root:
        root = os.path.abspath(root)
        if os.path.isdir(root):
            return [root]
        return []

    return [drive for drive in SEARCH_DRIVES if os.path.exists(drive)]


def priority_search_roots(root=None):
    """
    Ordered roots for a streaming search: the places real documents actually live
    come first, so hits appear in the first second instead of after os.walk has
    ground through system directories at the top of C:\\.
    """
    if root:
        root = os.path.abspath(root)
        return [root] if os.path.isdir(root) else []

    roots = []
    home = os.path.expanduser("~")

    for sub in ("Desktop", "Documents", "Downloads", "OneDrive"):
        candidate = os.path.join(home, sub)
        if os.path.isdir(candidate):
            roots.append(candidate)

    if os.path.isdir(home):
        roots.append(home)

    roots.extend(drive for drive in SEARCH_DRIVES if os.path.exists(drive))
    return roots


def iter_search_files(query, root=None, max_results=200, progress_cb=None):
    """
    Streaming variant of live_search_files: yields (score, mtime, path) for each
    match *as the walk finds it*, instead of collecting everything and sorting at
    the end. A full C:\\ + D:\\ walk takes ~30-90s, so anything that waits for the
    complete result set is unusable interactively — this lets a caller show hits
    within the first second and refine ordering as more arrive.

    progress_cb, if given, is called with the directory currently being scanned.
    The caller cancels simply by not requesting the next item (closing the
    generator), so no shared cancel flag is needed.
    """
    features = extract_query_features(query)
    query = features["query"]

    if not query:
        return

    found = 0
    seen = set()
    done_roots = []

    for search_root in priority_search_roots(root):
        for current_root, dirs, files in os.walk(search_root):

            if is_excluded(current_root):
                dirs[:] = []
                continue

            # Skip anything a higher-priority root already covered.
            lowered = current_root.lower()
            if any(lowered.startswith(prefix) for prefix in done_roots):
                dirs[:] = []
                continue

            dirs[:] = [
                directory for directory in dirs
                if not is_excluded(os.path.join(current_root, directory))
            ]

            if progress_cb:
                progress_cb(current_root)

            for file in files:
                if not is_valid_file(file):
                    continue

                if features["extensions"]:
                    if not any(file.lower().endswith(ext) for ext in features["extensions"]):
                        continue

                score = match_score(file, query)

                if score > 0:
                    full_path = os.path.join(current_root, file)
                    if full_path.lower() in seen:
                        continue
                    seen.add(full_path.lower())

                    try:
                        mtime = os.path.getmtime(full_path)
                    except OSError:
                        mtime = 0

                    yield (score, mtime, full_path)

                    found += 1
                    if found >= max_results:
                        return

        done_roots.append(search_root.lower().rstrip("\\/") + os.sep)


def live_search_files(query, max_results=100, root=None):
    results = []

    features = extract_query_features(query)
    query = features["query"]

    if not query:
        return []

    for search_root in get_search_roots(root):
        for current_root, dirs, files in os.walk(search_root):

            if is_excluded(current_root):
                dirs[:] = []
                continue

            dirs[:] = [
                directory for directory in dirs
                if not is_excluded(os.path.join(current_root, directory))
            ]

            for file in files:
                if not is_valid_file(file):
                    continue

                if features["extensions"]:
                    if not any(file.lower().endswith(ext) for ext in features["extensions"]):
                        continue

                score = match_score(file, query)

                if score > 0:
                    full_path = os.path.join(current_root, file)

                    try:
                        mtime = os.path.getmtime(full_path)
                    except OSError:
                        mtime = 0

                    results.append((score, mtime, full_path))

    if features["sort"] == "date_desc":
        results.sort(key=lambda x: (x[1], x[0]), reverse=True)
    elif features["sort"] == "date_asc":
        results.sort(key=lambda x: (x[1], x[0]))
    else:
        results.sort(key=lambda x: x[0], reverse=True)

    return [r[2] for r in results[:max_results]]


class FileSearchBackend:
    def search(self, query, max_results=100, root=None):
        raise NotImplementedError


class LiveFileSearchBackend(FileSearchBackend):
    def search(self, query, max_results=100, root=None):
        return live_search_files(query, max_results=max_results, root=root)


ACTIVE_BACKEND = LiveFileSearchBackend()


def set_search_backend(backend):
    global ACTIVE_BACKEND

    if not isinstance(backend, FileSearchBackend):
        raise TypeError("backend must implement FileSearchBackend")

    ACTIVE_BACKEND = backend


def search_files(query, max_results=100, root=None):
    return ACTIVE_BACKEND.search(query, max_results=max_results, root=root)
