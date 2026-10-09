"""Launch the current source with a new, isolated workspace and browser profile."""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent


def prepare(source, destination):
    """Copy executable source/assets only; never import project runtime data."""
    source, destination = Path(source), Path(destination)
    for package in ("app", "tenderops", "cli"):
        base = source / package
        paths = base.glob("*.py") if package == "app" else base.rglob("*.py")
        for path in paths:
            if any(part.startswith((".", "__pycache__")) for part in path.relative_to(base).parts):
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    web = source / "app" / "web"
    for path in web.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".js", ".css", ".svg", ".png", ".jpg", ".ico", ".woff", ".woff2"}:
            continue
        relative = path.relative_to(web)
        if path.name == "config.js" or any(part.startswith((".", "_")) for part in relative.parts):
            continue
        target = destination / "app" / "web" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    (destination / "data" / "demo").mkdir(parents=True)


def clean_environment(workspace, port):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("TENDEROPS_", "PYTHON"))}
    env.update(TENDEROPS_BASE=str(workspace), TENDEROPS_WRITEBASE=str(workspace),
               TENDEROPS_DB_PATH=str(workspace / "tenderops.sqlite3"),
               TENDEROPS_HOST="127.0.0.1", TENDEROPS_PORT=str(port),
               PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    return env


def browser_path():
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
                candidate = Path(base) / relative
                if candidate.is_file():
                    return candidate
    raise RuntimeError("Install Edge or Chrome to open an isolated browser profile.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true", help="Run without opening a browser (for verification).")
    args = parser.parse_args()
    browser = None if args.no_browser else browser_path()
    # New directory on every launch: a crash can never cause old state to be reused.
    workspace = Path(tempfile.mkdtemp(prefix="tenderops-clean-"))
    prepare(ROOT, workspace)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen([sys.executable, "-B", "-u", str(workspace / "app" / "server.py")],
                               cwd=workspace, env=clean_environment(workspace, port))
    try:
        deadline = time.monotonic() + 45
        while True:
            if process.poll() is not None:
                raise RuntimeError("The clean server stopped during startup.")
            try:
                with urllib.request.urlopen(url + "/api/tenders", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("The clean server did not become ready within 45 seconds.")
                time.sleep(0.2)
        print(f"\nClean TenderOps: {url}\nSession folder: {workspace}", flush=True)
        print("No previous projects, prices, AI notes, settings, or browser state loaded.", flush=True)
        print("Export anything you need before stopping. Every launch starts empty.", flush=True)
        print("Press Ctrl+C in this window to stop the server.", flush=True)
        if browser:
            subprocess.Popen([str(browser), f"--user-data-dir={workspace / 'browser-profile'}",
                              "--no-first-run", "--no-default-browser-check", "--disable-sync", url])
        process.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        # Keep session output available for recovery; it is never read by another launch.
        print(f"Stopped. This session's files remain at {workspace}; future launches ignore them.", flush=True)


if __name__ == "__main__":
    main()
