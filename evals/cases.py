"""
cases.py — what Helio is supposed to do when you say things to it.

Every case here came from a real bug or a real phrasing tried during
development. The negatives matter as much as the positives: most routing
regressions in this project were a pattern getting greedy and swallowing
something it shouldn't ("I have a headache" becoming a calendar entry).

Each case is (utterance, expected_class, expected_fields).
  expected_class  — the task class classify_task should return.
  expected_fields — a subset of the extracted data that must match exactly.
                    Leave empty to assert only the class.

Add a case whenever you fix a routing bug. That's how this file stays useful
instead of decorative.
"""

# ── deterministic fast paths ────────────────────────────────────────────────
# These must never need a model. Each one that falls through to the LLM costs
# a second or two and a chance of being answered from the model's imagination
# rather than the store.

TIMERS = [
    ("remind me in 20 minutes to check the oven", "TIMER_ACTION",
     {"when": "in 20 minutes", "message": "check the oven"}),
    ("remind me to call mom in 2 hours", "TIMER_ACTION",
     {"when": "in 2 hours", "message": "call mom"}),
    ("remind me at 6pm to take the bins out", "TIMER_ACTION",
     {"when": "at 6pm", "message": "take the bins out"}),
    ("set a timer for 5 minutes", "TIMER_ACTION", {"when": "5 minutes"}),
    ("start a timer for 10 mins", "TIMER_ACTION", {"when": "10 mins"}),
    ("wake me in 30 minutes", "TIMER_ACTION", {"when": "in 30 minutes"}),
    ("what reminders do i have", "TIMER_ACTION", {"action": "list"}),
    ("any timers running", "TIMER_ACTION", {"action": "list"}),
    ("list my reminders", "TIMER_ACTION", {"action": "list"}),
    ("cancel the oven reminder", "TIMER_ACTION", {"action": "cancel"}),
    ("cancel all reminders", "TIMER_ACTION", {"action": "cancel", "which": "all"}),
]

SCHEDULE = [
    ("what's on my schedule", "SCHEDULE_ACTION", {"action": "list", "range": "today"}),
    ("what's on today", "SCHEDULE_ACTION", {"action": "list", "range": "today"}),
    ("what do i have tomorrow", "SCHEDULE_ACTION", {"action": "list", "range": "tomorrow"}),
    ("show me my calendar for this week", "SCHEDULE_ACTION", {"action": "list", "range": "week"}),
    ("what does my day look like", "SCHEDULE_ACTION", {"action": "list"}),
    ("am i free", "SCHEDULE_ACTION", {"action": "list"}),
    ("any birthdays coming up", "SCHEDULE_ACTION", {"action": "list"}),
    ("schedule dentist for tomorrow at 3pm", "SCHEDULE_ACTION",
     {"action": "add", "what": "dentist"}),
    ("add gym to my schedule for tomorrow at 7am", "SCHEDULE_ACTION",
     {"action": "add", "what": "gym"}),
    ("i have a meeting at 3pm", "SCHEDULE_ACTION", {"action": "add", "what": "meeting"}),
    ("i have a standup tomorrow at 10", "SCHEDULE_ACTION",
     {"action": "add", "what": "standup"}),
    ("Arjun's birthday is on March 12", "SCHEDULE_ACTION",
     {"action": "add", "what": "Arjun's birthday"}),
    ("what should i do", "SCHEDULE_ACTION", {"action": "suggest"}),
    ("what can i do now", "SCHEDULE_ACTION", {"action": "suggest"}),
    ("i have some free time", "SCHEDULE_ACTION", {"action": "suggest"}),
    ("i'm free", "SCHEDULE_ACTION", {"action": "suggest"}),
    ("any ideas", "SCHEDULE_ACTION", {"action": "suggest"}),
    ("the second one", "SCHEDULE_ACTION", {"action": "pick", "choice": "second"}),
    ("2", "SCHEDULE_ACTION", {"action": "pick", "choice": "2"}),
]

DOCUMENTS = [
    ("what did the contract say about payment terms", "DOC_ACTION", {"action": "ask"}),
    ("what does the report say about revenue", "DOC_ACTION", {"action": "ask"}),
    ("what were the action items in my notes", "DOC_ACTION", {"action": "ask"}),
    ("search my documents for the deadline", "DOC_ACTION", {"action": "ask"}),
    ("what do my notes say about the migration", "DOC_ACTION", {"action": "ask"}),
    ("what documents have you studied", "DOC_ACTION", {"action": "list"}),
    ("study D:/notes/meeting.docx", "DOC_ACTION", {"action": "study"}),
]

SCREEN = [
    ("what's on my screen", "SCREEN_ACTION", {}),
    ("what is on my screen?", "SCREEN_ACTION", {}),
    ("what am i looking at", "SCREEN_ACTION", {}),
    ("look at my screen and tell me what's wrong", "SCREEN_ACTION", {}),
    ("read my screen", "SCREEN_ACTION", {}),
    ("can you see my screen", "SCREEN_ACTION", {}),
]

APPS_AND_FILES = [
    ("open chrome", "SIMPLE_ACTION", {"app": "chrome"}),
    ("start spotify", "SIMPLE_ACTION", {"app": "spotify"}),
    ("launch vscode", "SIMPLE_ACTION", {"app": "vscode"}),
    ("find my resume", "FILE_SEARCH_ACTION", {"query": "resume"}),
    ("search for my cv in D drive", "FILE_SEARCH_ACTION", {}),
    ("next", "FILE_ACTION", {"action": "next"}),
    ("open 2", "FILE_ACTION", {"action": "open", "index": 2}),
    ("read 3", "FILE_ACTION", {"action": "read", "index": 3}),
]

SEARCH = [
    ("youtube lofi beats", "SEARCH_ACTION", {"action": "youtube"}),
    ("google python decorators", "SEARCH_ACTION", {"action": "web"}),
]

# ── the forge ───────────────────────────────────────────────────────────────
# The whole sentence after the command scaffolding is the brief: cutting it at
# the first "and"/"with" was why "a bakery site with prices and opening hours"
# reached the generator as "a bakery site".
FORGE = [
    ("make me a website for my mum's bakery with prices and opening hours",
     "FORGE_WEBSITE",
     {"topic": "website for my mum's bakery with prices and opening hours"}),
    ("build a pomodoro timer with a start and reset button", "FORGE_WEBSITE",
     {"topic": "pomodoro timer with a start and reset button"}),
    # The noun can carry modifiers — it is not glued to the trigger.
    ("make me a tip calculator", "FORGE_WEBSITE", {"topic": "tip calculator"}),
    ("build me a simple todo app", "FORGE_WEBSITE", {"topic": "simple todo app"}),
    # The noun must survive into the brief, or there is nothing to build.
    ("design a dashboard showing my weekly hours", "FORGE_WEBSITE",
     {"topic": "dashboard showing my weekly hours"}),
    ("create a landing page for my portfolio", "FORGE_WEBSITE", {}),
    ("hey helio can you build a quiz about space", "FORGE_WEBSITE",
     {"topic": "quiz about space"}),
    # Revision keeps its verb and referent: cutting them left "make the
    # buttons blue" as the change "blue", and "restyle it" as nothing.
    # (These need something on the bench, so run.py seeds one.)
    ("make the buttons blue", "FORGE_REVISE", {"change": "make the buttons blue"}),
    ("change it to dark mode", "FORGE_REVISE", {"change": "change it to dark mode"}),
    ("add a contact form", "FORGE_REVISE", {"change": "add a contact form"}),
    ("restyle it", "FORGE_REVISE", {"change": "restyle it"}),
    # A build request is not a revision, and vice versa.
    ("make a note that i called mum about the website", "UNKNOWN", {}),
]


# the workshop, and pulling pictures off the internet
WORKSHOP = [
    ("open the workshop", "WORKSHOP_ACTION", {"action": "open_workshop"}),
    ("open my workbench", "WORKSHOP_ACTION", {"action": "open_workshop"}),
    ("go into build mode", "WORKSHOP_ACTION", {"action": "open_workshop"}),
    ("clear the workshop", "WORKSHOP_ACTION", {"action": "clear_workshop"}),
    # Both word orders people actually use. Matching only "picture OF a cat"
    # left the commoner phrasing falling through to the model.
    ("find me a cat photo", "IMAGE_ACTION", {"query": "cat"}),
    ("find me a ginger cat picture", "IMAGE_ACTION", {"query": "ginger cat"}),
    ("get me an image of a sunset", "IMAGE_ACTION", {"query": "a sunset"}),
    ("find a picture of the eiffel tower", "IMAGE_ACTION",
     {"query": "the eiffel tower"}),
    ("grab a logo for github", "IMAGE_ACTION", {"query": "github"}),
    ("download a wallpaper of mountains", "IMAGE_ACTION", {"query": "mountains"}),
]


# driving the ring, and writing programs
PLANETS_AND_CODE = [
    # "open my memory" used to launch the Windows memory diagnostic.
    ("open my memory", "PLANET_ACTION", {"planet": "memory"}),
    ("show me the schedule", "PLANET_ACTION", {"planet": "schedule"}),
    ("go to files", "PLANET_ACTION", {"planet": "files"}),
    ("open the forge", "PLANET_ACTION", {"planet": "forge"}),
    ("bring up system", "PLANET_ACTION", {"planet": "system"}),
    # ...and the app launcher keeps everything that is genuinely an app.
    ("open memory diagnostic", "SIMPLE_ACTION", {}),
    # "script"/"program" writes source; "tool"/"app" stays a built page,
    # because that is what the user means when they say it.
    ("write me a python script that renames every file to lowercase",
     "FORGE_CODE", {"language": "python"}),
    ("write a program to parse my csv exports", "FORGE_CODE",
     {"brief": "parse my csv exports"}),
    ("make me a bash script that backs up my documents", "FORGE_CODE",
     {"language": "bash"}),
    ("make me a tip calculator", "FORGE_WEBSITE", {}),
]


# throwing builds away
SCRAP = [
    # "scrap all builds" reached the model, which had no scrapping tool and
    # built a website called "Scrap All Builds".
    ("scrap all builds", "FORGE_SCRAP", {"which": "all"}),
    ("delete all my builds", "FORGE_SCRAP", {"which": "all"}),
    ("clear the forge", "FORGE_SCRAP", {"which": "all"}),
    ("wipe the rack", "FORGE_SCRAP", {"which": "all"}),
    ("throw away all my builds", "FORGE_SCRAP", {"which": "all"}),
    # ...and one by name. This used to match the memory-forget trigger and
    # would have deleted a memory instead of a build.
    ("delete the sourdough build", "FORGE_SCRAP", {"which": "sourdough"}),
    ("scrap the bakery build", "FORGE_SCRAP", {"which": "bakery"}),
]


# using what is open on the workshop
SURFACE = [
    # Helio could place things on the surface but never read it back, so
    # "the picture on screen" meant nothing to it.
    ("what's on the workshop", "SURFACE_ACTION", {}),
    ("what picture is on screen", "SURFACE_ACTION", {}),
    ("what have you got open on the screen", "SURFACE_ACTION", {}),
    # The picture is named only by where it is, and the target only by being
    # the most recent build — "" means newest.
    ("use the picture on screen in the last website you made",
     "FORGE_REVISE", {"which": ""}),
    ("use the picture on screen in the portfolio",
     "FORGE_REVISE", {"which": "portfolio"}),
    ("put the image on screen into the bakery site",
     "FORGE_REVISE", {"which": "bakery site"}),
    ("use that one up there in the coffee shop site",
     "FORGE_REVISE", {"which": "coffee shop site"}),
    ("use the photo you opened in the website", "FORGE_REVISE", {"which": ""}),
]


# ── negatives ───────────────────────────────────────────────────────────────
# Things that LOOK like a command to one of the patterns above but aren't.
# Every one of these is a bug that was actually possible at some point.

NEGATIVES = [
    # "on screen" belongs to the workshop, not the screen-reading tool.
    ("what's on my screen", "SCREEN_ACTION", {}),
    ("find me a cat photo", "IMAGE_ACTION", {}),
    # The scrap trigger needs a build as its object, so these stay put.
    ("clear the workshop", "WORKSHOP_ACTION", {}),
    ("forget that i work at acme", "LEARNING_ACTION", {}),
    ("delete what you know about my old address", "MEMEDIT_ACTION", {}),
    # The planet trigger is anchored at both ends so these stay put.
    ("how much memory do i have", "SYSINFO_ACTION", {}),
    ("what's my memory usage", "SYSINFO_ACTION", {}),
    ("what's on my schedule today", "SCHEDULE_ACTION", {}),
    ("check your memory", "MEMEDIT_ACTION", {}),
    # "find me a ..." belongs to the file search unless a picture is named.
    ("find my resume", "FILE_SEARCH_ACTION", {}),
    ("find me a file called notes", "FILE_SEARCH_ACTION", {}),
    ("open chrome", "SIMPLE_ACTION", {}),
    # The Forge's triggers are the greediest in the file — these are the
    # phrasings that looked like builds or revisions and are not.
    ("add gym at 7 every weekday", "UNKNOWN", {}),  # the model books this
    ("add milk to my shopping list", "UNKNOWN", {}),
    ("change my meeting to 6pm", "UNKNOWN", {}),

    # No parseable date, so not a calendar entry.
    ("i have a headache", "UNKNOWN", {}),
    ("i have a problem with the code", "UNKNOWN", {}),
    ("i have an idea", "UNKNOWN", {}),
    # No duration, so not a timer.
    ("set a timer", "UNKNOWN", {}),
    ("remind me later", "UNKNOWN", {}),
    # "study" without a path is not an indexing request.
    ("study for my exam", "UNKNOWN", {}),
    ("i need to study more", "UNKNOWN", {}),
    # Plain conversation.
    ("hello", "UNKNOWN", {}),
    ("what time is it", "UNKNOWN", {}),
    ("thanks", "UNKNOWN", {}),
    ("who are you", "UNKNOWN", {}),
]

SYSTEM_INFO = [
    ("what's my cpu at", "SYSINFO_ACTION", {"what": "cpu"}),
    ("how much ram am i using", "SYSINFO_ACTION", {"what": "memory"}),
    ("am i running out of space", "SYSINFO_ACTION", {"what": "disk"}),
    ("what's my battery", "SYSINFO_ACTION", {"what": "battery"}),
    ("how long has this been on", "SYSINFO_ACTION", {"what": "uptime"}),
]

WEB_READING = [
    ("look up what the model context protocol is", "WEBREAD_ACTION",
     {"action": "search"}),
    ("search the web for python dataclasses", "WEBREAD_ACTION",
     {"action": "search"}),
    ("what's the latest on the mars mission", "WEBREAD_ACTION",
     {"action": "search"}),
    ("read https://example.com/article", "WEBREAD_ACTION",
     {"action": "read", "url": "https://example.com/article"}),
    ("summarise this page https://example.com/x", "WEBREAD_ACTION",
     {"action": "read"}),
]

# ── compound requests ───────────────────────────────────────────────────────
# Two commands in one sentence must reach the planner, which fans them out into
# parallel branches. Before this, the first matching fast path answered clause
# one and dropped clause two without saying so.

COMPOUND = [
    ("check my cpu usage and look up what the model context protocol is",
     "COMPLEX_TASK", {}),
    ("what is on my schedule and what is my cpu at", "COMPLEX_TASK", {}),
    ("open chrome and open vscode", "COMPLEX_TASK", {}),
    # Making something is a family too. Without that, "look up X and build me
    # a page about it" read as one lookup and the page was never built.
    ("look up what the model context protocol is and build me a page explaining it",
     "COMPLEX_TASK", {}),
    ("find me a cat photo and build me a page about cats", "COMPLEX_TASK", {}),
    ("check the weather in coimbatore and put it on the workshop",
     "COMPLEX_TASK", {}),
    # ...but these are single actions that merely contain "and".
    ("make me a website for a bakery with prices and opening hours",
     "FORGE_WEBSITE", {}),
    ("build a pomodoro timer with a start and reset button", "FORGE_WEBSITE", {}),
    ("look at my screen and tell me what is wrong", "SCREEN_ACTION", {}),
    ("remind me in 20 minutes to check the oven and the stove", "TIMER_ACTION", {}),
    ("find my resume and read it", "FILE_SEARCH_ACTION", {}),
]

# ── what Helio inferred, and correcting it ──────────────────────────────────
# These are the controls on an inference system. They have to work every time:
# if "that's not true" doesn't reliably land, a wrong belief about the user
# becomes permanent.

LEARNING = [
    ("what have you learned about me", "LEARNING_ACTION", {"action": "list"}),
    ("what have you noticed", "LEARNING_ACTION", {"action": "list"}),
    ("what patterns have you seen", "LEARNING_ACTION", {"action": "list"}),
    ("what do you know about my habits", "LEARNING_ACTION", {"action": "list"}),
    ("that's not true", "LEARNING_ACTION", {"action": "forget"}),
    ("forget that i open chrome in the morning", "LEARNING_ACTION",
     {"action": "forget"}),
    ("stop assuming i go to the gym", "LEARNING_ACTION", {"action": "forget"}),
    ("forget my activity", "LEARNING_ACTION", {"action": "wipe"}),
    ("stop tracking me", "LEARNING_ACTION", {"action": "wipe"}),
    ("clear my history", "LEARNING_ACTION", {"action": "wipe"}),
]

# ── editing what Helio was told ─────────────────────────────────────────────
# Distinct from LEARNING above: that corrects what Helio *inferred*, this
# corrects what the user *told* it. The two must not steal each other's
# phrasings — "forget my activity" is the log, "forget that I work at Acme" is
# a fact.

MEMORY_EDIT = [
    # "forget that X" is genuinely ambiguous — X may be a fact you stated or a
    # pattern Helio inferred. One door, and the tool looks in both stores.
    ("forget that i work at acme", "LEARNING_ACTION", {"action": "forget"}),
    ("delete what you know about my old address", "MEMEDIT_ACTION",
     {"action": "forget"}),
    ("what did i say before", "MEMEDIT_ACTION", {"action": "history"}),
    ("show me the old version", "MEMEDIT_ACTION", {"action": "history"}),
    ("is anything you know out of date", "MEMEDIT_ACTION", {"action": "expire"}),
    ("check your memory", "MEMEDIT_ACTION", {"action": "expire"}),
    # ...and these still belong to the other controls.
    ("forget my activity", "LEARNING_ACTION", {"action": "wipe"}),
    ("stop tracking me", "LEARNING_ACTION", {"action": "wipe"}),
    ("cancel all reminders", "TIMER_ACTION", {"action": "cancel"}),
]

FAST_CASES = (TIMERS + SCHEDULE + DOCUMENTS + SCREEN + SYSTEM_INFO
              + WEB_READING + APPS_AND_FILES + SEARCH + COMPOUND
              + LEARNING + MEMORY_EDIT + NEGATIVES)

GROUPS = [
    ("timers & reminders", TIMERS),
    ("schedule", SCHEDULE),
    ("documents", DOCUMENTS),
    ("screen", SCREEN),
    ("system readouts", SYSTEM_INFO),
    ("web reading", WEB_READING),
    ("apps & files", APPS_AND_FILES),
    ("web search", SEARCH),
    ("compound requests", COMPOUND),
    ("learning controls", LEARNING),
    ("memory editing", MEMORY_EDIT),
    ("the forge", FORGE),
    ("workshop & images", WORKSHOP),
    ("planets & code", PLANETS_AND_CODE),
    ("scrapping builds", SCRAP),
    ("the surface", SURFACE),
    ("negatives", NEGATIVES),
]


# ── LLM routing ─────────────────────────────────────────────────────────────
# Phrasings the regexes deliberately don't cover, where the model has to pick
# the tool. Slower and non-deterministic, so these run only with --llm.
# Each is (utterance, expected tool action or intent).

LLM_CASES = [
    ("what have you built", "forge_list_artifacts"),
    ("show me my forge", "forge_list_artifacts"),
    ("i've got a dentist appointment on friday at 3", "add_event"),
    ("my sister's birthday is the 12th of march", "add_event"),
    ("i go to the gym at 7 every weekday", "add_event"),
    ("put a note in my calendar for the team offsite next thursday", "add_event"),
    ("what does my week look like", "list_schedule"),
    ("i skipped the gym today, what could i do instead", "suggest_activity"),
    ("i went to the gym", "complete_event"),
    ("remember that i prefer dark mode", "remember_fact"),
    ("save this as my morning routine: open chrome, open vscode", "remember_workflow"),
    ("run my work setup", "run_workflow"),
    ("is my machine struggling", "get_system_info"),
    ("what did the lease say about the deposit", "ask_documents"),
    ("find out who won the world cup in 2022", "search_and_read"),
    # Grounded chat: a yes/no lookup, not a listing.
    ("do i have a dentist appointment booked", "chat"),
    ("what did i just say", "chat"),
    ("introduce yourself", "chat"),
]
