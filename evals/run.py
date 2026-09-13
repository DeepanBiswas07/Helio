"""
run.py — does Helio still route things correctly?

    python evals/run.py            the deterministic paths, ~1 second
    python evals/run.py --llm      also the model-routed ones, ~1 minute
    python evals/run.py --verbose  show every case, not just failures

Why this exists: the router prompt has been changed a dozen times in this
project, each time validated on a single example that happened to be at hand.
That is how a fix for one phrasing silently breaks three others. This turns
"I think that helped" into a number you can compare against the last number.

Exit code is non-zero when anything fails, so it works as a pre-commit gate.
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _HERE)

import cases as suite  # noqa: E402


class Result:
    def __init__(self):
        self.passed = 0
        self.failed = []
        self.times = []

    @property
    def total(self):
        return self.passed + len(self.failed)

    @property
    def rate(self):
        return (100.0 * self.passed / self.total) if self.total else 100.0


def _matches(data, expected):
    data = data or {}
    return all(data.get(key) == value for key, value in expected.items())


def run_fast(verbose=False):
    """The regex layer. No model, no network — this should be instant."""
    from routing import task_classifier
    from routing.task_classifier import classify_task

    # The Forge's revision triggers only fire when something is on the bench,
    # so this suite would otherwise pass or fail depending on whether the last
    # real build happened to still be sitting there. Stand a decoy up instead.
    bench_check = task_classifier._forge_has_active_build
    task_classifier._forge_has_active_build = lambda: True

    print("\033[1mDETERMINISTIC ROUTING\033[0m")
    overall = Result()

    for group_name, group in suite.GROUPS:
        result = Result()
        for utterance, expected_class, expected_fields in group:
            start = time.perf_counter()
            task_class, data = classify_task(utterance)
            elapsed = (time.perf_counter() - start) * 1000
            result.times.append(elapsed)

            ok = task_class == expected_class and _matches(data, expected_fields)
            if ok:
                result.passed += 1
                if verbose:
                    print("  ok    {:<48} {}".format(utterance[:48], task_class))
            else:
                shown = {k: v for k, v in (data or {}).items()
                         if k in ("action", "what", "when", "message", "range",
                                  "choice", "which", "app", "query", "index")}
                result.failed.append((utterance, expected_class, task_class, shown))

        overall.passed += result.passed
        overall.failed.extend(result.failed)
        overall.times.extend(result.times)

        mark = "\033[32m✓\033[0m" if not result.failed else "\033[31m✗\033[0m"
        print("  {} {:<20} {:>3}/{:<3}  {:>5.1f}ms avg".format(
            mark, group_name, result.passed, result.total,
            sum(result.times) / max(1, len(result.times))))

        for utterance, want, got, shown in result.failed:
            print("      \033[31mFAIL\033[0m {!r}".format(utterance))
            print("           want {}  got {} {}".format(want, got, shown or ""))

    task_classifier._forge_has_active_build = bench_check
    return overall


def run_prompt_templates():
    """
    Every prompt template must format with exactly what its call site passes.

    Adding a field to a template and missing one call site raised KeyError
    forty seconds into a build, after the model had already been paid for.
    This costs nothing and catches it here instead.
    """
    from tools.system import forge_tool as forge

    print("[1mPROMPT TEMPLATES[0m")
    shelf = forge._picture_block()
    checks = [
        ("page", forge._PAGE_BRIEF, {"brief": "x", "pictures": shelf}),
        ("revise", forge._REVISE_BRIEF,
         {"change": "x", "current": "y", "pictures": shelf}),
        ("code", forge._CODE_BRIEF, {"brief": "x", "language": "python"}),
        ("edit", forge._EDIT_BRIEF,
         {"change": "x", "path": "y", "current": "z"}),
    ]
    result = Result()
    for name, template, arguments in checks:
        try:
            template.format(**arguments)
            result.passed += 1
        except Exception as e:
            result.failed.append((name, "formats", type(e).__name__ + str(e), {}))

    mark = "[32m✓[0m" if not result.failed else "[31m✗[0m"
    print("  {} {:<20} {:>3}/{:<3}".format(
        mark, "forge prompts", result.passed, len(checks)))
    for name, _, detail, _ in result.failed:
        print("      [31mFAIL[0m {} -> {}".format(name, detail))
    return result


def run_llm(verbose=False):
    """The model layer. Slower, non-deterministic, run when it matters."""
    from routing.intent_detector import detect_intents
    from routing.task_classifier import classify_task

    print("\n\033[1mMODEL ROUTING\033[0m")
    result = Result()

    for utterance, expected in suite.LLM_CASES:
        # Skip anything the regex layer already handles — that's the fast
        # suite's job, and a double-cover here would hide a fast-path loss.
        task_class, _data = classify_task(utterance)
        if task_class not in ("UNKNOWN", "COMPLEX_TASK"):
            if verbose:
                print("  skip  {:<48} handled deterministically".format(utterance[:48]))
            continue

        start = time.perf_counter()
        try:
            intents = detect_intents(utterance, {})
        except Exception as e:
            intents = []
            print("      \033[31mERROR\033[0m {!r}: {}".format(utterance, e))
        elapsed = time.perf_counter() - start
        result.times.append(elapsed)

        got = "none"
        if intents:
            first = intents[0]
            got = (first.get("parameters", {}).get("action")
                   or first.get("intent") or "none")

        ok = got == expected
        if ok:
            result.passed += 1
            if verbose:
                print("  ok    {:<48} {:<18} {:.1f}s".format(
                    utterance[:48], got, elapsed))
        else:
            result.failed.append((utterance, expected, got, elapsed))
            print("      \033[31mFAIL\033[0m {!r}".format(utterance))
            print("           want {}  got {}  ({:.1f}s)".format(expected, got, elapsed))

    if result.total:
        print("  {} model routing        {:>3}/{:<3}  {:>5.1f}s avg".format(
            "\033[32m✓\033[0m" if not result.failed else "\033[31m✗\033[0m",
            result.passed, result.total,
            sum(result.times) / max(1, len(result.times))))
    return result


def run_retrieval(verbose=False):
    """Retrieval is a routing question too: does the right passage come back?"""
    print("\n\033[1mDOCUMENT RETRIEVAL\033[0m")
    result = Result()

    try:
        from memory import vector_store as store
    except Exception as e:
        print("  skipped — vector store unavailable ({})".format(e))
        return result

    if store.chunk_count() == 0:
        print("  skipped — no documents studied yet "
              "(study one, then this suite checks it stays findable)")
        return result

    docs = store.indexed_documents()
    print("  {} documents, {} chunks indexed".format(len(docs), store.chunk_count()))

    # Without a fixed corpus we can't assert specific passages, so assert the
    # properties that must hold for any corpus: a query drawn from a chunk
    # retrieves that chunk, and nonsense retrieves nothing confidently.
    import random
    random.seed(7)
    _vectors, records = store._load()
    sample = random.sample(records, min(8, len(records)))

    for record in sample:
        words = record["text"].split()
        probe = " ".join(words[3:16]) if len(words) > 16 else record["text"][:90]
        start = time.perf_counter()
        hits = store.search(probe, top_k=3)
        result.times.append((time.perf_counter() - start) * 1000)

        found = any(h["path"] == record["path"] and h["chunk"] == record["chunk"]
                    for h in hits)
        if found:
            result.passed += 1
            if verbose:
                print("  ok    {} p{} round-trips".format(
                    record["name"], record["chunk"] + 1))
        else:
            result.failed.append((probe[:60], record["name"], "", 0))
            print("      \033[31mFAIL\033[0m passage from {} p{} not retrievable "
                  "by its own text".format(record["name"], record["chunk"] + 1))

    print("  {} passage round-trip   {:>3}/{:<3}  {:>5.1f}ms avg".format(
        "\033[32m✓\033[0m" if not result.failed else "\033[31m✗\033[0m",
        result.passed, result.total,
        sum(result.times) / max(1, len(result.times))))
    return result


def main():
    parser = argparse.ArgumentParser(description="Helio routing evals")
    parser.add_argument("--llm", action="store_true",
                        help="also exercise model-routed cases (slow)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print passing cases too")
    args = parser.parse_args()

    if os.name == "nt":
        os.system("")   # enable ANSI colours in the Windows console

    started = time.time()
    results = [run_fast(args.verbose), run_prompt_templates()]
    results.append(run_retrieval(args.verbose))
    if args.llm:
        results.append(run_llm(args.verbose))

    passed = sum(r.passed for r in results)
    failed = sum(len(r.failed) for r in results)
    total = passed + failed

    print("\n" + "─" * 62)
    verdict = "\033[32mPASS\033[0m" if not failed else "\033[31m{} FAILING\033[0m".format(failed)
    print("{}  {}/{} cases  ({:.0f}%)  in {:.1f}s".format(
        verdict, passed, total,
        (100.0 * passed / total) if total else 100.0,
        time.time() - started))
    if not args.llm:
        print("Model-routed cases not run. Add --llm before shipping prompt changes.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
