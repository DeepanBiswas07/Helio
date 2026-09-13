"""
system_info.py — machine readouts, by voice.

The SYSTEM planet has shown CPU, memory, disk and uptime on screen since the
beginning, but none of it was reachable by asking. The eval harness caught it:
"what's my cpu at" routed to chat, which has no idea, so the answer came from
the model's imagination. Same live figures, now available as a tool.
"""
import time

from tools.tool_registry import tool


def _gb(value):
    return value / (1024 ** 3)


def _uptime():
    import psutil
    seconds = max(0, time.time() - psutil.boot_time())
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@tool(
    name="get_system_info",
    description=(
        "Report how the computer itself is doing: CPU load, memory use, disk "
        "space, battery and uptime. "
        "Use for 'what's my CPU at', 'how much RAM am I using', 'am I running "
        "out of space', 'how long has this been on', 'is my machine struggling'. "
        "'what' optionally narrows it to cpu / memory / disk / battery / uptime."
    ),
    parameters={"what": "string"},
    required=[],
)
def handle_get_system_info(action_data):
    try:
        import psutil
    except ImportError:
        return "I can't read the system stats — psutil isn't installed."

    wanted = str(action_data.get("what", "") or "").strip().lower()

    def cpu():
        # interval=0.3 gives a real reading; interval=None returns 0.0 on the
        # first call because there is no previous sample to diff against.
        load = psutil.cpu_percent(interval=0.3)
        cores = psutil.cpu_count(logical=False)
        threads = psutil.cpu_count(logical=True)
        return f"CPU is at {load:.0f}% across {cores} cores ({threads} threads)."

    def memory():
        mem = psutil.virtual_memory()
        return ("Memory is at {:.0f}% — {:.1f} GB of {:.1f} GB in use, "
                "{:.1f} GB free.").format(
            mem.percent, _gb(mem.used), _gb(mem.total), _gb(mem.available))

    def disk():
        try:
            usage = psutil.disk_usage("/")
        except Exception:
            return "I couldn't read the disk."
        return ("The system drive is {:.0f}% full — {:.0f} GB free of "
                "{:.0f} GB.").format(usage.percent, _gb(usage.free), _gb(usage.total))

    def battery():
        try:
            power = psutil.sensors_battery()
        except Exception:
            power = None
        if power is None:
            return "No battery detected — this machine is on mains power."
        state = "charging" if power.power_plugged else "on battery"
        if power.secsleft and power.secsleft > 0 and not power.power_plugged:
            hours, minutes = divmod(int(power.secsleft) // 60, 60)
            return f"Battery is at {power.percent:.0f}%, {state}, about {hours}h {minutes}m left."
        return f"Battery is at {power.percent:.0f}%, {state}."

    def uptime():
        return f"This machine has been up for {_uptime()}."

    parts = {"cpu": cpu, "memory": memory, "ram": memory, "disk": disk,
             "storage": disk, "space": disk, "battery": battery,
             "power": battery, "uptime": uptime}

    for key, fn in parts.items():
        if key in wanted:
            return fn()

    # No narrowing asked for: the whole readout, in the order people care.
    return "\n".join([cpu(), memory(), disk(), battery(), uptime()])
