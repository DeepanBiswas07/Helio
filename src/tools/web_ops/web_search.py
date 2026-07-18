from ddgs import DDGS


def search_web(query, max_results=5):
    results = []

    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "body": r.get("body", ""),
                    "link": r.get("href", "")
                })
    except Exception:
        return []

    return results
