import webbrowser
import urllib.parse


def open_google_search(query):
    if not query.strip():
        return "Empty search query."

    encoded = urllib.parse.quote(query)
    url = f"https://www.google.com/search?q={encoded}"

    webbrowser.open(url)
    return f"Searching for '{query}'"


def open_youtube_search(query):
    if not query.strip():
        return "Empty search query."

    encoded = urllib.parse.quote(query)
    url = f"https://www.youtube.com/results?search_query={encoded}"

    webbrowser.open(url)
    return f"Opening YouTube for '{query}'"
