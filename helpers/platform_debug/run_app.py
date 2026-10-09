# -*- coding: utf-8 -*-
"""TenderOps — single-file executable launcher.
Starts the HTTP server on 127.0.0.1:9000 (daemon thread) and opens the browser.
Close the browser tab / press Ctrl+C in console to stop.
"""
import threading, time, webbrowser, sys, os

def main():
    # frozen-aware: when packaged by PyInstaller, data lives in sys._MEIPASS
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    os.environ.setdefault("TENDEROPS_BASE", base)
    os.environ.setdefault("TENDEROPS_PORT", "9000")
    from app.server import run
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.2)
    port = os.environ.get("TENDEROPS_PORT", "9000")
    url = f"http://127.0.0.1:{port}"
    webbrowser.open(url)
    print(f"TenderOps running -> {url}")
    print("Close this console window to stop.")
    try:
        while t.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
