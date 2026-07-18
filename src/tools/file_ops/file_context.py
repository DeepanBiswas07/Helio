FILE_RESULTS = []
CURRENT_INDEX = 0
PAGE_SIZE = 5


def set_results(results):
    global FILE_RESULTS, CURRENT_INDEX
    FILE_RESULTS = results
    CURRENT_INDEX = 0


def get_next_page():
    global CURRENT_INDEX

    start = CURRENT_INDEX
    end = start + PAGE_SIZE

    page = FILE_RESULTS[start:end]
    CURRENT_INDEX = end

    return page, start


def get_by_index(index):
    try:
        return FILE_RESULTS[index]
    except (IndexError, TypeError):
        return None


def format_results(results, start_index=0):
    if not results:
        return "No files found."

    output = ""
    for i, path in enumerate(results, start=start_index + 1):
        output += f"{i}. {path}\n"

    return output
