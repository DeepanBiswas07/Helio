import re
import os
from tools.file_ops.file_search import extract_search_scope, strip_search_scope
from tools.time_ops.when_parser import parse_when, parse_datetime

# Task Classes
SIMPLE_ACTION = "SIMPLE_ACTION"
FILE_ACTION = "FILE_ACTION"
SEARCH_ACTION = "SEARCH_ACTION"
FILE_SEARCH_ACTION = "FILE_SEARCH_ACTION"
TIMER_ACTION = "TIMER_ACTION"
SCREEN_ACTION = "SCREEN_ACTION"
SCHEDULE_ACTION = "SCHEDULE_ACTION"
DOC_ACTION = "DOC_ACTION"
SYSINFO_ACTION = "SYSINFO_ACTION"
WEBREAD_ACTION = "WEBREAD_ACTION"
LEARNING_ACTION = "LEARNING_ACTION"
MEMEDIT_ACTION = "MEMEDIT_ACTION"
FORGE_WEBSITE = "FORGE_WEBSITE"
FORGE_REVISE = "FORGE_REVISE"
WORKSHOP_ACTION = "WORKSHOP_ACTION"
IMAGE_ACTION = "IMAGE_ACTION"
PLANET_ACTION = "PLANET_ACTION"
FORGE_CODE = "FORGE_CODE"
FORGE_SCRAP = "FORGE_SCRAP"
FORGE_OPEN = "FORGE_OPEN"
SURFACE_ACTION = "SURFACE_ACTION"
COMPLEX_TASK = "COMPLEX_TASK"
UNKNOWN = "UNKNOWN"

# Keywords that strongly indicate a LOCAL file search request
_FILE_SEARCH_TRIGGERS = [
    r"\bsearch\b.{0,40}\b(?:file|drive|pc|computer|disk|folder|document|my)\b",
    r"\bfind\b.{0,40}\b(?:file|drive|pc|computer|disk|folder|document|my)\b",
    r"\b[a-z]\s*drive\b",
    r"\bsearch\s+(?:for\s+)?(?:my\s+)?(?:resume|cv|document|file|pdf|report|notes|presentation)\b",
    r"\bfind\s+(?:my\s+)?(?:resume|cv|document|file|pdf|report|notes|presentation)\b",
]

# Reminder / timer phrasings, in the two orders people actually say them.
# Group order differs between them, so each carries its own field mapping.
_TIMER_TRIGGERS = [
    # "remind me in 20 minutes to check the oven"
    (r"^(?:hey\s+)?(?:can\s+you\s+)?(?:please\s+)?remind\s+me\s+"
     r"((?:in|at|after|tomorrow)\b.+?)\s+(?:to|that|about)\s+(.+?)[.!?]*$",
     ("when", "message")),
    # "remind me to check the oven in 20 minutes"
    (r"^(?:hey\s+)?(?:can\s+you\s+)?(?:please\s+)?remind\s+me\s+(?:to|that|about)\s+"
     r"(.+?)\s+((?:in|at|after|tomorrow)\b.+?)[.!?]*$",
     ("message", "when")),
    # "set a timer for 5 minutes"
    (r"^(?:set|start|create|put)\s+(?:a|an)?\s*(?:timer|alarm|countdown)\s*"
     r"(?:for|of)?\s*(.+?)[.!?]*$",
     ("when",)),
    # "wake me in 30 minutes"
    (r"^(?:wake|ping|nudge)\s+me\s+((?:in|at)\b.+?)[.!?]*$", ("when",)),
]

# Asking about or cancelling reminders. Worth catching deterministically: these
# are fixed phrasings, and the LLM route answers them from conversation memory
# instead of the store — which means inventing reminders that don't exist.
_REMINDER_LIST_TRIGGERS = [
    r"^(?:what|which)\s+(?:reminders?|timers?|alarms?)\b",
    r"\b(?:list|show|tell\s+me)\s+(?:my\s+|all\s+(?:my\s+)?|the\s+)?(?:reminders?|timers?|alarms?)\b",
    r"\b(?:do\s+i\s+have|are\s+there|have\s+i\s+got)\s+(?:any\s+)?(?:reminders?|timers?|alarms?)\b",
    r"\bany\s+(?:reminders?|timers?|alarms?)\s+(?:set|running|pending|left|going)\b",
    r"\b(?:reminders?|timers?|alarms?)\s+do\s+i\s+have\b",
]

_REMINDER_CANCEL_TRIGGER = (
    r"^(?:cancel|delete|remove|clear|stop|kill|forget)\b"
    r"(?=.*\b(?:reminders?|timers?|alarms?)\b)\s*(.*)$"
)

# "What's on my screen?" and its neighbours. These must be caught before the
# ' and ' complexity heuristic, since "look at my screen and tell me what's
# wrong" is one action, not two.
_SCREEN_TRIGGERS = [
    r"\b(?:what(?:'|’)?s|what\s+is)\s+(?:on|showing\s+on|open\s+on)\s+my\s+screen\b",
    r"\bwhat\s+am\s+i\s+(?:looking\s+at|seeing)\b",
    r"\b(?:look\s+at|check|read|describe)\s+(?:my|the)\s+screen\b",
    r"\bcan\s+you\s+see\s+(?:my\s+screen|this|what.{0,12}on\s+my\s+screen)\b",
    r"\btake\s+a\s+(?:screenshot|look)\s+(?:of\s+)?(?:my\s+screen)\b",
]

# Reading the schedule back. Deterministic because the LLM route answers these
# from conversation memory when it stumbles — which means inventing events.
_SCHEDULE_READ_TRIGGERS = [
    r"\bwhat(?:'|\u2019)?s?\s+(?:is\s+)?(?:on\s+)?(?:my\s+)?(?:schedule|calendar|agenda)\b",
    r"\b(?:show|read|check)\s+(?:me\s+)?(?:my\s+)?(?:schedule|calendar|agenda)\b",
    r"\bwhat\s+(?:do\s+)?i\s+have\s+(?:on\s+)?(?:today|tomorrow|this week|this month|next week)\b",
    r"\bwhat(?:'|\u2019)?s\s+(?:on\s+)?(?:today|tomorrow|this week|this month)\b",
    r"\bwhat\s+does\s+my\s+(?:day|week|month)\s+look\s+like\b",
    r"\bam\s+i\s+free\b",
    r"\bany\s+birthdays\b",
]

# Asking for something to do with the time in front of them.
_SCHEDULE_SUGGEST_TRIGGERS = [
    r"\bwhat\s+should\s+i\s+do\b",
    r"\bwhat\s+can\s+i\s+do\b",
    r"\bi\s+have\s+(?:some\s+)?(?:free\s+)?time\b",
    r"\bi(?:'|\u2019)?m\s+free\b",
    r"\bany\s+(?:ideas|suggestions)\b",
    r"\bsuggest\s+(?:me\s+)?something\b",
    r"\bgive\s+me\s+(?:some\s+)?(?:ideas|suggestions)\b",
    r"\bwhat\s+now\b",
]

# Putting something on the schedule. Each needs a parseable date, checked below,
# so "I have a problem" never becomes a calendar entry.
_SCHEDULE_ADD_TRIGGERS = [
    (r"^(?:add|put)\s+(.+?)\s+(?:to|on|in)\s+my\s+(?:schedule|calendar)"
     r"(?:\s+(?:for|at|on)\s+(.+))?$", ("what", "when")),
    (r"^schedule\s+(.+?)\s+(?:for|at|on)\s+(.+)$", ("what", "when")),
    (r"^(?:i\s+have|i(?:'|\u2019)?ve\s+got|i\s+got)\s+(?:a|an|my)?\s*(.+?)\s+"
     r"((?:at|on|tomorrow|next|this|today|tonight)\b.+)$", ("what", "when")),
    (r"^(.+?)(?:'|\u2019)?s?\s+birthday\s+is\s+(?:on\s+)?(.+)$", ("what", "when")),
]

# Picking one of the options Helio just offered.
_SCHEDULE_PICK_TRIGGER = (
    r"^(?:do\s+)?(?:the\s+)?(?:number\s+)?"
    r"(first|second|third|1|2|3)(?:\s+one)?$"
)

# Questions aimed at the contents of the user's own documents. Deterministic
# because the alternative is chat answering from the model's weights, which is
# how you get a confident invented answer about your own contract.
_DOC_ASK_TRIGGERS = [
    r"\bwhat\s+(?:did|does|do)\s+(?:the\s+|my\s+|that\s+)?\S+\s+say\s+about\b",
    r"\baccording\s+to\s+(?:the\s+|my\s+)?\S+\s*,?\s*(?:what|how|when|who)\b",
    r"\b(?:in|from)\s+(?:the\s+|my\s+)?(?:document|doc|file|notes|contract|paper|report|pdf)s?\b.{0,40}\b(?:what|how|when|who|where|why)\b",
    r"\bwhat\s+(?:were|are)\s+the\s+(?:action\s+items|key\s+points|main\s+points|takeaways)\b",
    r"\bsearch\s+(?:my|the)\s+(?:documents|docs|notes|files)\s+for\b",
    r"\bwhat\s+do\s+(?:my|the)\s+(?:notes|documents|docs)\s+say\b",
]

# "study this file" — read it into searchable memory.
_DOC_STUDY_TRIGGER = (
    r"^(?:study|learn|read\s+and\s+remember|memorise|memorize|index)\s+"
    r"(?:this\s+|the\s+|my\s+)?(?:file\s+|document\s+|doc\s+)?(.+?)[.!?]*$"
)

_DOC_LIST_TRIGGERS = [
    r"\bwhat\s+(?:documents|docs|files)\s+have\s+you\s+(?:studied|read|learned)\b",
    r"\bwhat\s+(?:documents|docs)\s+do\s+you\s+know\b",
    r"\blist\s+(?:your\s+)?studied\s+(?:documents|docs|files)\b",
]

# Machine readouts. The SYSTEM planet has always shown these on screen; asking
# for them out loud used to reach chat, which answered from imagination.
_SYSINFO_TRIGGERS = [
    (r"\bcpu\b|\bprocessor\b", "cpu"),
    # "your memory" means what Helio remembers, not this machine's RAM.
    (r"\bram\b|(?<!your\s)\bmemory\b", "memory"),
    (r"\b(?:disk|storage|drive)\s+space\b|\brunning\s+out\s+of\s+space\b|\bfree\s+space\b", "disk"),
    (r"\bbattery\b|\bcharge\b(?!\s+of)", "battery"),
    (r"\buptime\b|\bhow\s+long\s+has\s+(?:it|this|my\s+\w+)\s+been\s+(?:on|running|up)\b", "uptime"),
]

# Only treat those as a system question when the sentence is actually asking.
_SYSINFO_QUESTION = (
    r"\b(?:what|what's|whats|how|hows|how's|check|show|tell\s+me|is|am\s+i|"
    r"give\s+me)\b"
)

# Reading the web rather than opening it. The existing SEARCH_ACTION opens a
# browser tab; these fetch the pages and answer from them, so they take the
# phrasings where the user clearly wants an answer, not a window.
_WEBREAD_URL = re.compile(
    r"(?:read|open|summari[sz]e|check|what(?:'|\u2019)?s\s+(?:on|at))\s+"
    r"(?:this\s+|that\s+|the\s+)?(?:page\s+|link\s+|article\s+|url\s+)?"
    r"(https?://\S+|www\.\S+)", re.I)

_WEBREAD_TRIGGERS = [
    r"^(?:look\s+up|search\s+(?:the\s+)?(?:web|internet|online)\s+for)\s+\S+",
    r"\b(?:find\s+out|look\s+up)\s+(?:what|who|when|where|why|how)\b",
    r"\bsearch\s+online\s+for\b",
    r"\bwhat\s+(?:is|are)\s+the\s+latest\b",
    r"\bwhat(?:'|\u2019)?s\s+the\s+latest\s+on\b",
]

# Joiners that can separate two independent requests. Deliberately narrow:
# a comma is far too common inside a single instruction to count.
_COMPOUND_SPLIT = re.compile(r"\s+(?:and\s+then|and\s+also|and|then|also)\s+", re.I)

# Families whose members are separate enough that seeing two of them means two
# requests. Two file lookups in one sentence are usually still one job.
_COMPOUND_FAMILIES = {
    SIMPLE_ACTION: "launch", FILE_SEARCH_ACTION: "files", FILE_ACTION: "files",
    SEARCH_ACTION: "web", WEBREAD_ACTION: "web", SCREEN_ACTION: "screen",
    SYSINFO_ACTION: "system", SCHEDULE_ACTION: "schedule",
    TIMER_ACTION: "timer", DOC_ACTION: "docs",
    # Making things counts as a family too, or "look up X and build me a page
    # about it" reads as a single lookup and the page is silently never made.
    FORGE_WEBSITE: "build", FORGE_CODE: "build", FORGE_REVISE: "build",
    IMAGE_ACTION: "image",
    WORKSHOP_ACTION: "surface", PLANET_ACTION: "surface",
}


def _is_compound(query_lower, query):
    """
    True when the request is two commands wearing one sentence.

    Each half is classified on its own with the compound check disabled, so
    this recurses exactly one level.
    """
    halves = [h.strip() for h in _COMPOUND_SPLIT.split(query.strip()) if h.strip()]
    if len(halves) < 2:
        return False

    families = []
    for half in halves[:3]:
        # Too short to be a command on its own — "milk and eggs" is one thing.
        if len(half.split()) < 2:
            continue
        task_class, _data = classify_task(half, _check_compound=False)
        family = _COMPOUND_FAMILIES.get(task_class)
        if family:
            families.append(family)

    return len(set(families)) >= 2

# Asking what Helio has worked out on its own, or telling it it's wrong.
# Deterministic because these are the controls on an inference system — they
# have to work every time, not most of the time.
_LEARNING_READ_TRIGGERS = [
    r"\bwhat\s+(?:have|did)\s+you\s+(?:learn|learnt|learned|notice[d]?|observe[d]?|figure[d]?\s+out)\b",
    r"\bwhat\s+do\s+you\s+know\s+about\s+(?:me|my\s+habits|my\s+routine)\b",
    r"\bwhat\s+patterns?\s+(?:have\s+you\s+)?(?:seen|noticed|found)\b",
    r"\bwhat\s+have\s+you\s+picked\s+up\b",
]

_LEARNING_FORGET_TRIGGER = (
    r"^(?:that(?:'|\u2019)?s\s+(?:not\s+true|wrong)|forget\s+that|"
    r"stop\s+(?:thinking|assuming)|i\s+don(?:'|\u2019)?t\s+do\s+that)\b\s*(.*)$"
)

_LEARNING_WIPE_TRIGGERS = [
    r"\b(?:forget|clear|delete|erase)\s+(?:my\s+)?(?:activity|history|behaviou?r|tracking)\b",
    r"\bstop\s+tracking\s+me\b",
]

# Editing what Helio was told. Deterministic because a wrong belief the user
# is actively trying to delete has to actually go — "mostly works" is not good
# enough for a correction.
# ── what is on the workshop, and using it ────────────────────────────────────
_SURFACE_ASK = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+)?"
    r"(?:what(?:'s|\s+is|\s+are)?|which|tell\s+me\s+what)\s+"
    r"(?:.{0,24}?\s+)?"
    r"(?:on|open\s+(?:on|in)|up\s+on|showing\s+(?:on|in))\s+"
    r"(?:the\s+|my\s+)?(?:screen|workshop|surface|workbench)\b",
    re.IGNORECASE)

# "use the one on screen in the portfolio" — the picture is named only by
# where it is, and the target build only by being the last one.
_USE_ON_SCREEN = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:use|put|add|insert|drop|stick|place)\s+"
    r"(?:the\s+|that\s+|this\s+)?"
    r"(?:picture|image|photo|pic|photograph|one)\s+"
    r"(?:that(?:'s|\s+is)?\s+)?"
    r"(?:(?:is\s+)?(?:on|up\s+on|open\s+(?:on|in))\s+(?:the\s+|my\s+)?"
    r"(?:screen|workshop|surface)|up\s+there|"
    r"(?:you|i)\s+(?:opened|put\s+up|found))"
    r"(?:\s+(?:in|into|on|onto|to)\s+(?:the\s+)?(?P<target>.+?))?\s*$",
    re.IGNORECASE)

# Words that only say "the recent one", not which one.
_GENERIC_TARGET = re.compile(
    r"^(?:last|latest|most\s+recent|newest|previous|that|it|one|thing|"
    r"web\s*site|website|site|page|build|project|app|"
    r"you\s+(?:made|built|created)|i\s+(?:made|built)|we\s+(?:made|built)|"
    r"the|a|an|\s)+$",
    re.IGNORECASE)


def _revise_target(match):
    """Which build to change: a name, or "" meaning the most recent."""
    target = (match.group("target") or "").strip(" .!?,")
    if not target or _GENERIC_TARGET.match(target):
        return ""
    return target


# ── opening something Helio built ────────────────────────────────────────────
# An explicit reference to the forge is required — "you made", "in the forge",
# "I built" — so "open chrome" and "find my resume" are left where they are.
_FORGE_OPEN = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:open|show(?:\s+me)?|bring\s+up|pull\s+up|load|display)\s+"
    r"(?:me\s+)?(?:the\s+|that\s+|my\s+|a\s+)?"
    r"(?P<what>.+?)\s*"
    r"(?:that\s+|which\s+)?"
    r"\b(?:(?:you|we|i)\s+(?:made|built|created|forged)|"
    r"(?:in|from|on)\s+(?:the\s+)?(?:forge|rack|bench|workshop)|"
    r"from\s+my\s+builds)\b"
    r"(?:\s+(?:here|earlier|before|already))?"
    r"(?:\s+(?:in|on)\s+(?:the\s+)?(?:forge|rack|bench|workshop))?\s*$",
    re.IGNORECASE)

_FORGE_OPEN_NOISE = re.compile(
    r"\b(website|web\s*site|webpage|web\s*page|site|page|build|one)\b\s*$",
    re.IGNORECASE)


def _forge_open_target(match):
    """What to look for on the rack, with the filler words trimmed."""
    what = (match.group("what") or "").strip(" .!?,")
    trimmed = _FORGE_OPEN_NOISE.sub("", what).strip()
    return trimmed or what


# ── scrapping what was built ─────────────────────────────────────────────────
# The object has to be a build, so "forget that I work at Acme" and "clear the
# workshop" (which only takes things off the surface) are left alone.
_FORGE_SCRAP = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:scrap|bin|trash|throw\s+(?:away|out)|get\s+rid\s+of|delete|remove|clear|"
    r"wipe|empty)\s+"
    r"(?P<scope>all\s+(?:of\s+)?|every\s+|everything\s+(?:in|from|on)\s+)?"
    r"(?:my\s+|the\s+|that\s+|this\s+)?"
    r"(?P<what>[\w'\- ]{0,40}?)\s*"
    r"\b(?:builds?|forge|rack|artifacts?|creations?)\b\s*$",
    re.IGNORECASE)


def _scrap_target(match):
    """'all', or the name of the one build to throw away."""
    scope = (match.group("scope") or "").strip().lower()
    what = (match.group("what") or "").strip()
    if scope or not what or what.lower() in ("of", "my", "the"):
        return "all"
    return what


# ── the planets ──────────────────────────────────────────────────────────────
# Anchored at both ends: "open my memory" is the MEMORY planet, but "open
# memory diagnostic" is a Windows tool and must stay with the app launcher.
_PLANET_NAMES = (r"setup|assemblies|routines|memory|chat|files|file|system|"
                 r"schedule|calendar|agenda|forge|bench")
_PLANET_OPEN = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:open|show|bring\s+up|go\s+to|switch\s+to|take\s+me\s+to|jump\s+to)\s+"
    r"(?:me\s+)?(?:my\s+|the\s+)?"
    r"(?P<planet>" + _PLANET_NAMES + r")"
    r"(?:\s+(?:planet|panel|page))?\s*$",
    re.IGNORECASE)

# ── writing programs ─────────────────────────────────────────────────────────
# "script"/"program" means source on disk. "tool"/"app" stays with the Forge,
# which builds those as working pages — that is what the user sees them as.
_CODE_BUILD = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:write|code|make|build|create|generate)\s+"
    r"(?:me\s+)?(?:a|an|some)?\s*"
    r"(?P<lang>python|py|javascript|js|typescript|ts|bash|shell|powershell|"
    r"ruby|go|rust|java|c\+\+|cpp|c#|csharp|sql|batch)?\s*"
    r"(?:script|program|module|cli|command\s+line\s+tool|function|class)\b"
    r"\s*(?:that|which|to|for|:)?\s*",
    re.IGNORECASE)


# ── the workshop ─────────────────────────────────────────────────────────────
_WORKSHOP_OPEN = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:open|show|bring\s+up|launch|start|go\s+(?:to|into)|enter)\s+"
    r"(?:the\s+|my\s+|up\s+)?"
    r"(?:workshop|work\s*bench|workbench|build\s+mode|making\s+surface)\b",
    re.IGNORECASE)

_WORKSHOP_CLEAR = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:clear|clean|empty|reset|wipe)\s+"
    r"(?:the\s+|my\s+)?(?:workshop|work\s*bench|workbench|surface)\b",
    re.IGNORECASE)

# Both orders people actually say it in: "a picture OF a cat" puts the subject
# after the noun, "a CAT picture" puts it before. Matching only the first left
# the commonest phrasing falling through to the model.
_IMAGE_FIND = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:find|get|grab|fetch|search\s+for|look\s+up|download|show)\s+"
    r"(?:me\s+)?(?:a|an|some)?\s*"
    r"(?P<before>[\w\s'-]{0,40}?)\s*"
    r"(?:image|images|picture|pictures|pic|pics|photo|photos|photograph|"
    r"wallpaper|logo|icon)s?\s*"
    r"(?:of|for|showing|with)?\s*"
    r"(?P<after>.*)$",
    re.IGNORECASE)


def _image_subject(match):
    """What the picture should be of, whichever side of the noun it sat on."""
    after = (match.group("after") or "").strip(" .!?")
    if after:
        return after
    return (match.group("before") or "").strip(" .!?")


# ── The Forge ────────────────────────────────────────────────────────────────
# Everything after the trigger is the brief, verbatim. "for/about/of" is
# optional so both "build a site for my bakery" and "build me a bakery site"
# land the same way.
# The trigger strips the command scaffolding only — "make me a" — and the
# whole rest of the sentence becomes the brief. Consuming the noun as well
# turned "design a dashboard showing my weekly hours" into the brief
# "showing my weekly hours", and the generator then had no idea what to build.
_FORGE_BUILD_TRIGGER = (
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:make|create|build|generate|design|forge|whip\s+up|put\s+together)\s+"
    # "be" because speech-to-text hears "build me a" as "build be a" often
    # enough that it reached the rack as a build titled "Be a website for...".
    r"(?:me\s+|be\s+|us\s+)?(?:a|an|the)?\s*"
)

# What counts as a thing the Forge can make. Checked against the head of the
# brief rather than glued to the trigger, so any number of modifiers can sit
# in front of it — "pomodoro timer", "tip calculator", "one-page resume".
_FORGE_NOUN = re.compile(
    r"\b(?:web\s*site|website|web\s*page|webpage|web\s*app|site|page|"
    r"dashboard|landing\s+page|portfolio|app|tool|game|timer|calculator|"
    r"tracker|form|quiz|chart|graph|report|resume|cv|menu|gallery|blog|"
    r"shop|store|planner|widget|clock|converter|generator)\b",
    re.IGNORECASE,
)

_FORGE_CLAUSE = re.compile(
    r"\b(?:that|about|for|with|showing|which|where|when|to|of|listing|"
    r"containing|using)\b", re.IGNORECASE)


def _forge_head(brief):
    """The noun phrase at the front of a brief, before any detail clause."""
    split = _FORGE_CLAUSE.search(brief)
    return brief[:split.start()] if split else brief[:44]


# Editing whatever is on the bench. Gated on there actually being one, so
# "make it bigger" said to any other planet is not stolen by the Forge.
_FORGE_POLITE = re.compile(
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?",
    re.IGNORECASE)

_FORGE_REVISE_TRIGGER = (
    r"^(?:hey\s+)?(?:helio[,\s]+)?(?:can\s+you\s+|could\s+you\s+|please\s+)?"
    r"(?:change|edit|update|revise|restyle|tweak|adjust|fix|make|add|remove|"
    r"delete|swap|replace|move|resize|rename)\s+"
    # A referent is required. Without one, "add milk to my list" is a
    # revision of the page, which it plainly is not.
    r"(?:it\b|that\b|this\b|them\b|"
    r"(?:a\s+|an\s+|the\s+|some\s+)?(?:page|site|build|"
    r"button|buttons|colour|color|colours|colors|background|text|font|fonts|"
    r"title|header|heading|headings|footer|layout|image|images|link|links|"
    r"menu|nav|navbar|style|theme|border|spacing|padding|margin|logo|banner|"
    r"contact\s+form|form|table|chart|list|card|cards|section|sections|"
    r"dark\s+mode|light\s+mode)\b)"
)


def _forge_has_active_build():
    """True when the Forge has something on the bench to revise."""
    try:
        from tools.system.forge_tool import get_active_artifact
        return bool(get_active_artifact().get("file_path"))
    except Exception:
        return False


_MEMEDIT_FORGET_TRIGGER = (
    r"^(?:forget|delete|remove)\s+(?:that\s+|what\s+you\s+know\s+about\s+|"
    r"the\s+fact\s+that\s+)?(.+?)[.!?]*$"
)

_MEMEDIT_HISTORY_TRIGGERS = [
    r"\bwhat\s+did\s+(?:i|you)\s+(?:say|think)\s+before\b",
    r"\bwhat\s+was\s+it\s+before\b",
    r"\bshow\s+me\s+the\s+old\s+(?:version|value)\b",
    r"\bhas\s+that\s+changed\b",
    r"\bwhat\s+did\s+you\s+used?\s+to\s+think\b",
]

_MEMEDIT_EXPIRY_TRIGGERS = [
    r"\bis\s+anything\s+(?:you\s+know\s+)?out\s+of\s+date\b",
    r"\banything\s+expired\b",
    r"\bcheck\s+your\s+memory\b",
]

# Tier 2 Heuristics: Keywords that indicate complex intent
COMPLEX_KEYWORDS = [
    "summarize", "compare", "analyze", "explain", "extract",
    "and then", "after that", "read and", "open and", "search and"
]


def classify_task(query, _check_compound=True):
    query_lower = query.lower().strip()

    # Two commands in one sentence go to the planner, which can run them as
    # parallel branches. Without this the first matching fast path answers the
    # first clause and silently drops the rest.
    if _check_compound and _is_compound(query_lower, query):
        return COMPLEX_TASK, None

    # ---------------------------------------------------------
    # Tier 0-: Searching *inside* documents beats searching *for* files.
    # "search my documents for the deadline" is a content question; the file
    # search pattern below matches the word "documents" and would turn it into
    # a filename lookup for "documents the deadline".
    # ---------------------------------------------------------
    for pattern in _DOC_ASK_TRIGGERS:
        if re.search(pattern, query_lower):
            return DOC_ACTION, {"action": "ask", "question": query.strip()}

    # ---------------------------------------------------------
    # Tier 0: Deterministic local file search detection
    # Must happen BEFORE the COMPLEX_TASK check to avoid being
    # swallowed by the ' and ' heuristic.
    # ---------------------------------------------------------
    for pattern in _FILE_SEARCH_TRIGGERS:
        if re.search(pattern, query_lower):
            root = extract_search_scope(query_lower)
            clean = strip_search_scope(query_lower)
            # Strip common conversational filler from the search query
            clean = re.sub(
                r"\b(?:search|find|look\s+for|look|for|on\s+pc|on\s+my\s+pc|on\s+computer|on\s+the\s+pc|my|then|tell\s+me|from\s+it|from\s+the\s+file|and|it|now|college\s+name|college|name|from\s+it|get|give\s+me|show|please|can\s+you|could\s+you|in\s+the|in)\b",
                " ", clean, flags=re.IGNORECASE
            )
            clean = re.sub(r"\s+", " ", clean).strip()
            # Remove leftover single-char tokens
            clean = " ".join(w for w in clean.split() if len(w) > 1)
            if clean:
                return FILE_SEARCH_ACTION, {"query": clean, "root": root}

    # ---------------------------------------------------------
    # Tier 0.5: Reminders, timers and screen questions
    # Ahead of the COMPLEX_TASK check for the same reason as file search —
    # "remind me to X and Y" and "look at my screen and tell me" are each one
    # action, and both are common enough that an LLM round trip is wasted here.
    # ---------------------------------------------------------
    for pattern in _SCREEN_TRIGGERS:
        if re.search(pattern, query_lower):
            return SCREEN_ACTION, {"question": query.strip()}

    for pattern, fields in _TIMER_TRIGGERS:
        match = re.match(pattern, query_lower)
        if match:
            extracted = dict(zip(fields, (g.strip() for g in match.groups())))
            # Only take the fast path when the time phrase is one we can
            # actually parse — otherwise let the LLM have a go at it.
            if parse_when(extracted.get("when", "")):
                return TIMER_ACTION, extracted

    for pattern in _REMINDER_LIST_TRIGGERS:
        if re.search(pattern, query_lower):
            return TIMER_ACTION, {"action": "list"}

    match = re.match(_REMINDER_CANCEL_TRIGGER, query_lower)
    if match:
        which = re.sub(
            r"\b(?:the|my|that|a|an|pending|reminders?|timers?|alarms?)\b",
            " ", match.group(1)
        )
        # Deliberately not defaulting a bare "cancel my reminder" to "all" —
        # the tool decides, and only clears everything if the user said so.
        return TIMER_ACTION, {"action": "cancel", "which": " ".join(which.split())}

    # ---------------------------------------------------------
    # Tier 0.6: Schedule
    # Ahead of the COMPLEX_TASK check for the same reason as the others —
    # "what's on today and what should I do" is one question, not two.
    # ---------------------------------------------------------
    for pattern in _SCHEDULE_SUGGEST_TRIGGERS:
        if re.search(pattern, query_lower):
            return SCHEDULE_ACTION, {"action": "suggest"}

    for pattern in _SCHEDULE_READ_TRIGGERS:
        if re.search(pattern, query_lower):
            span = "today"
            for word in ("tomorrow", "this week", "next week", "week", "month"):
                if word in query_lower:
                    span = "tomorrow" if word == "tomorrow" else (
                        "month" if word == "month" else "week")
                    break
            return SCHEDULE_ACTION, {"action": "list", "range": span}

    for pattern, fields in _SCHEDULE_ADD_TRIGGERS:
        match = re.match(pattern, query_lower)
        if match:
            # Match on the lowercased text but slice the ORIGINAL, so a title
            # keeps its capitals — "Arjun's birthday", not "arjun's birthday".
            original = query.strip()
            extracted = {}
            for field, index in zip(fields, range(1, len(fields) + 1)):
                span = match.span(index)
                extracted[field] = (
                    original[span[0]:span[1]].strip() if span[0] >= 0 else "")
            # Only take the fast path when there is a real date in it. Without
            # this guard, "I have a headache today" becomes a calendar entry.
            if parse_datetime(extracted.get("when") or extracted.get("what", "")):
                # The birthday pattern captures only the name, so put the word
                # back — "arjun" is not a useful thing to see on a calendar.
                what = extracted.get("what", "")
                if "birthday" in query_lower and "birthday" not in what:
                    extracted["what"] = f"{what.strip().rstrip(chr(39)+chr(8217))}'s birthday"
                extracted["action"] = "add"
                return SCHEDULE_ACTION, extracted

    match = re.match(_SCHEDULE_PICK_TRIGGER, query_lower)
    if match:
        return SCHEDULE_ACTION, {"action": "pick", "choice": match.group(1)}

    # ---------------------------------------------------------
    # Tier 0.7: Questions about the user's own documents
    # ---------------------------------------------------------
    for pattern in _DOC_LIST_TRIGGERS:
        if re.search(pattern, query_lower):
            return DOC_ACTION, {"action": "list"}

    match = re.match(_DOC_STUDY_TRIGGER, query_lower)
    if match and ("." in match.group(1) or "\\" in match.group(1) or "/" in match.group(1)):
        # Only when it looks like a path or a filename — "study for my exam"
        # is not a request to index anything.
        span = match.span(1)
        return DOC_ACTION, {"action": "study", "path": query.strip()[span[0]:span[1]]}

    # ---------------------------------------------------------
    # Tier 0.8: Machine readouts
    # ---------------------------------------------------------
    if re.search(_SYSINFO_QUESTION, query_lower):
        for pattern, part in _SYSINFO_TRIGGERS:
            if re.search(pattern, query_lower):
                return SYSINFO_ACTION, {"what": part}

    # ---------------------------------------------------------
    # Tier 0.9: Reading the web
    # ---------------------------------------------------------
    match = _WEBREAD_URL.search(query.strip())
    if match:
        return WEBREAD_ACTION, {"action": "read", "url": match.group(1),
                                "question": query.strip()}

    for pattern in _WEBREAD_TRIGGERS:
        if re.search(pattern, query_lower):
            cleaned = re.sub(
                r"^(?:look\s+up|search\s+(?:the\s+)?(?:web|internet|online)\s+for|"
                r"search\s+online\s+for|find\s+out)\s+", "", query.strip(),
                flags=re.I).strip()
            return WEBREAD_ACTION, {"action": "search", "query": cleaned or query.strip()}

    # ---------------------------------------------------------
    # Tier 0.4: What Helio has inferred, and correcting it
    # ---------------------------------------------------------
    for pattern in _LEARNING_WIPE_TRIGGERS:
        if re.search(pattern, query_lower):
            return LEARNING_ACTION, {"action": "wipe"}

    for pattern in _LEARNING_READ_TRIGGERS:
        if re.search(pattern, query_lower):
            return LEARNING_ACTION, {"action": "list"}

    if _SURFACE_ASK.match(query.strip()):
        return SURFACE_ACTION, {"action": "whats_on_the_workshop"}

    match = _USE_ON_SCREEN.match(query.strip())
    if match:
        return FORGE_REVISE, {"action": "forge_revise",
                              "change": query.strip(),
                              "which": _revise_target(match)}

    match = _FORGE_OPEN.match(query.strip())
    if match:
        target = _forge_open_target(match)
        if target:
            return FORGE_OPEN, {"action": "forge_open", "which": target}

    match = _FORGE_SCRAP.match(query.strip())
    if match:
        return FORGE_SCRAP, {"action": "forge_scrap",
                             "which": _scrap_target(match)}

    match = re.match(_LEARNING_FORGET_TRIGGER, query_lower)
    if match:
        return LEARNING_ACTION, {"action": "forget", "which": match.group(1).strip()}

    # ---------------------------------------------------------
    # Tier 0.45: Editing remembered facts
    # Runs after the LEARNING checks so "forget my activity" and "forget that
    # you noticed X" keep going to the inference controls rather than here.
    # ---------------------------------------------------------
    for pattern in _MEMEDIT_EXPIRY_TRIGGERS:
        if re.search(pattern, query_lower):
            return MEMEDIT_ACTION, {"action": "expire"}

    for pattern in _MEMEDIT_HISTORY_TRIGGERS:
        if re.search(pattern, query_lower):
            return MEMEDIT_ACTION, {"action": "history"}

    match = re.match(_MEMEDIT_FORGET_TRIGGER, query_lower)
    if match:
        subject = match.group(1).strip()
        # Words that mean a different kind of forgetting, already handled above.
        if subject and not re.search(
                r"^(?:my\s+)?(?:activity|history|behaviou?r|tracking|"
                r"reminders?|timers?|alarms?|everything|all)\b", subject):
            span = match.span(1)
            return MEMEDIT_ACTION, {"action": "forget",
                                    "what": query.strip()[span[0]:span[1]]}

    # ── The Forge: build me a thing I can look at ─────────────────────────
    #
    # The whole remaining sentence becomes the brief. An earlier version cut
    # the topic at the first "and"/"with", so "a bakery site with prices and
    # opening hours" reached the generator as "a bakery site" and the page
    # came back missing exactly what was asked for.
    match = _PLANET_OPEN.match(query.strip())
    if match:
        return PLANET_ACTION, {"action": "open_planet",
                               "planet": match.group("planet").lower()}

    match = _CODE_BUILD.match(query.strip())
    if match:
        brief = query.strip()[match.end():].strip(" .!?")
        if len(brief) >= 5:
            return FORGE_CODE, {"action": "forge_create_code", "brief": brief,
                                "language": (match.group("lang") or "python").lower()}

    if _WORKSHOP_OPEN.match(query.strip()):
        return WORKSHOP_ACTION, {"action": "open_workshop"}
    if _WORKSHOP_CLEAR.match(query.strip()):
        return WORKSHOP_ACTION, {"action": "clear_workshop"}

    match = _IMAGE_FIND.match(query.strip())
    if match:
        subject = _image_subject(match)
        if len(subject) >= 2:
            return IMAGE_ACTION, {"action": "find_image", "query": subject}

    match = re.match(_FORGE_BUILD_TRIGGER, query.strip(), re.IGNORECASE)
    if match:
        brief = query.strip()[match.end():].strip(" .!?")
        # The noun has to be in the head, or "make a note that I called mum
        # about the website" would be read as a build request.
        if len(brief) >= 3 and _FORGE_NOUN.search(_forge_head(brief)):
            return FORGE_WEBSITE, {"action": "forge_create_website",
                                   "topic": brief, "title": ""}

    # ---------------------------------------------------------
    # Tier 2: Heuristic Analysis (Check for complex workflows)
    # ---------------------------------------------------------
    if " and " in query_lower or any(kw in query_lower for kw in COMPLEX_KEYWORDS):
        return COMPLEX_TASK, None

    # ---------------------------------------------------------
    # Tier 1: Deterministic Regex Matching (Fast Path)
    # ---------------------------------------------------------
    
    # A. Specific web searches with 'in/on' keywords (must be checked before simple open)
    # 1. YouTube search (e.g. "open AI videos in YouTube" or "open AI videos on YouTube")
    match = re.match(r"^(?:open|search|search for|find)\s+(.+?)\s+(?:in|on)\s+youtube\.?$", query_lower)
    if match:
        return SEARCH_ACTION, {"action": "youtube", "query": match.group(1).strip()}

    # 2. Google / Web search (e.g. "open AI videos in Google" or "open AI videos in browser")
    match = re.match(r"^(?:open|search|search for|find)\s+(.+?)\s+(?:in|on)\s+(?:google|browser|chrome|edge|web)\.?$", query_lower)
    if match:
        return SEARCH_ACTION, {"action": "web", "query": match.group(1).strip()}

    # 0. FILE_ACTION: "open 2" / "read 3" — a numbered result, not an app.
    # This has to beat the app rule below, which otherwise tries to launch a
    # program called "2" and only recovers via the fast path's failure branch.
    match = re.match(r"^(open|read)\s+(\d+)$", query_lower)
    if match:
        return FILE_ACTION, {"action": match.group(1), "index": int(match.group(2))}

    # 1. SIMPLE_ACTION: open/launch [app name]
    # Negative lookahead ensures we don't match file requests like "open the file"
    match = re.match(r"^(?:open|launch|start)\s+(?!the file\b|my file\b|file\b)(.+)$", query_lower)
    if match:
        return SIMPLE_ACTION, {"app": match.group(1).strip()}
        
    # 2. FILE_ACTION: next / more
    if query_lower in ("next", "more"):
        return FILE_ACTION, {"action": "next"}
        
    # 3. FILE_ACTION: open / read [index]
    match = re.match(r"^(open|read)\s+(\d+)$", query_lower)
    if match:
        return FILE_ACTION, {"action": match.group(1), "index": int(match.group(2))}

    # 4. SEARCH_ACTION: youtube search
    match = re.match(r"^(?:youtube|search youtube for|search on youtube|search youtube)\s+(.+)$", query_lower)
    if match:
        return SEARCH_ACTION, {"action": "youtube", "query": match.group(1).strip()}
        
    # 5. SEARCH_ACTION: web search
    match = re.match(r"^(?:google|search web for|search the web for|search on google|search google for|search on web|search web)\s+(.+)$", query_lower)
    if match:
        return SEARCH_ACTION, {"action": "web", "query": match.group(1).strip()}

    # ---------------------------------------------------------
    # Editing what is on the Forge bench.
    #
    # Deliberately last. "add gym at 7 every weekday" and "change my meeting
    # to 6" are the same shape as "add a contact form", so every schedule,
    # reminder and memory detector above gets first refusal; only what none
    # of them wanted can be a revision. Gated on there actually being
    # something on the bench, too.
    # ---------------------------------------------------------
    if re.match(_FORGE_REVISE_TRIGGER, query.strip(), re.IGNORECASE)             and _forge_has_active_build():
        # Only the politeness comes off. The verb and its referent ARE the
        # instruction — cutting them left "make the buttons blue" as the
        # change "blue", and "restyle it" as nothing at all.
        change = _FORGE_POLITE.sub("", query.strip(), count=1).strip(" .!?")
        if change:
            return FORGE_REVISE, {"action": "forge_revise", "change": change}

    # ---------------------------------------------------------
    # Tier 3: Unknown -> Fallback to Slow Path LLM
    # ---------------------------------------------------------
    return UNKNOWN, None
