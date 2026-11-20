"""Desktop-shortcut launcher: starts the app with no console window and opens the browser.
If the server is already running, it only opens the browser tab."""
import os
import socket
import sys
import traceback
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", 8765))
URL = f"http://127.0.0.1:{PORT}"


def already_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            return True
    except OSError:
        return False


def message(text: str):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, text, "Vid Downloader", 0x10)


if already_running():
    webbrowser.open(URL)
    sys.exit(0)

# pythonw has no console: send all output to a log file instead
log = open(ROOT / "app.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

try:
    import runpy
    runpy.run_path(str(ROOT / "app.py"), run_name="__main__")  # app.py opens the browser itself
except SystemExit as e:
    if e.code not in (0, None):
        message(f"Vid Downloader could not start:\n\n{e.code}\n\nRun setup.bat, then try again.")
except Exception:
    traceback.print_exc()
    message("Vid Downloader crashed while starting. See app.log in the app folder for details.")
