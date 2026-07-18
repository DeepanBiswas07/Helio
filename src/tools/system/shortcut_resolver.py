from pylnk3 import parse


def resolve_lnk(path):
    try:
        lnk = parse(path)
        return lnk.path
    except Exception:
        return None
