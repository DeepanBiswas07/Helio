"""
autostart.py — have Helio come up with Windows, and go away with it.

    python scripts/autostart.py install     turn it on
    python scripts/autostart.py uninstall   turn it off
    python scripts/autostart.py status      check

Uses the per-user Startup folder rather than a registry Run key or a scheduled
task, for one reason: you can see it. It's a shortcut in a folder you can open,
delete, or disable from Task Manager's Startup tab like anything else. A
registry entry does the same job while being invisible, which is the wrong
trade for something that turns on your microphone and camera at login.

The shortcut runs pythonw.exe, not python.exe, so no console window flashes up
at login — Helio's own terminal console is still available when you launch it
by hand.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHORTCUT_NAME = "Helio.lnk"


def startup_dir():
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def shortcut_path():
    return os.path.join(startup_dir(), SHORTCUT_NAME)


def _interpreter():
    """
    The windowed interpreter from the project's venv, falling back to whatever
    is running this script. pythonw keeps the console from flashing at login.
    """
    venv = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "pythonw.exe")
    if os.path.exists(venv):
        return venv
    windowed = sys.executable.replace("python.exe", "pythonw.exe")
    return windowed if os.path.exists(windowed) else sys.executable


def install():
    target = _interpreter()
    script = os.path.join(PROJECT_ROOT, "ui", "app.py")
    if not os.path.exists(script):
        return False, f"Can't find {script}"

    folder = startup_dir()
    if not os.path.isdir(folder):
        return False, f"Startup folder not found: {folder}"

    link = shortcut_path()

    # A .lnk needs the Windows shell. pywin32 is the clean way; PowerShell's
    # WScript.Shell is the fallback so this works without extra packages.
    try:
        from win32com.client import Dispatch
        shell = Dispatch("WScript.Shell")
        cut = shell.CreateShortCut(link)
        cut.Targetpath = target
        cut.Arguments = f'"{script}"'
        cut.WorkingDirectory = PROJECT_ROOT
        cut.Description = "Helio — offline desktop AI assistant"
        cut.save()
        return True, link
    except ImportError:
        pass

    import subprocess
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        "$s.TargetPath = '{target}';"
        "$s.Arguments = '\"{script}\"';"
        "$s.WorkingDirectory = '{root}';"
        "$s.Description = 'Helio - offline desktop AI assistant';"
        "$s.Save()"
    ).format(link=link, target=target, script=script, root=PROJECT_ROOT)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True)
    if result.returncode != 0:
        return False, (result.stderr or "PowerShell could not create the shortcut").strip()
    return os.path.exists(link), link


def uninstall():
    link = shortcut_path()
    if not os.path.exists(link):
        return False, "Helio was not set to start with Windows."
    try:
        os.remove(link)
    except OSError as e:
        return False, str(e)
    return True, link


def status():
    link = shortcut_path()
    return os.path.exists(link), link


def main():
    command = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()

    if command == "install":
        ok, detail = install()
        print("Helio will now start with Windows.\n  {}".format(detail) if ok
              else "Could not set up autostart.\n  {}".format(detail))
        if ok:
            print("\nTo undo: python scripts/autostart.py uninstall")
            print("Or delete that shortcut, or switch it off in Task Manager "
                  "> Startup apps.")
        return 0 if ok else 1

    if command == "uninstall":
        ok, detail = uninstall()
        print("Helio will no longer start with Windows." if ok else detail)
        return 0

    on, link = status()
    print("Starts with Windows: {}".format("yes" if on else "no"))
    print("  shortcut: {}".format(link))
    print("  interpreter: {}".format(_interpreter()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
