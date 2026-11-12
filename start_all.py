"""Starts all four apps with ONE command:   python start_all.py

    360 Degree View ............ port 8501      Store Replenishment App ... port 8502
    Warehouse Packaging App .... port 8503      Store Stock Request App ... port 8504

Every line they print is labelled, e.g. [packing]. Press Ctrl + C once: all four are stopped properly.
If one app crashes, the other three keep running and you are told which one stopped.

Options
    --demo-if-empty   if there is no database yet, invent demo data and build it first (used on a new server / in Docker)
    --host 0.0.0.0    listen on every network address (a server needs this; the default 127.0.0.1 is your own computer only)

Settings (environment variables or the .env file; all optional)
    PUBLIC_HOST       the address people use to reach the server, e.g. 203.0.113.7. The links between the apps are built from it.
    PORT_APP, PORT_STORE, PORT_PACKING, PORT_REQUEST   change a port (defaults 8501 to 8504)
    TZ                time zone, e.g. Asia/Kolkata (the apps print local times)
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DB_FILE = ROOT / "data" / "retail.db"

APPS = [("app", "app.py", "PORT_APP", 8501), ("store", "store_app.py", "PORT_STORE", 8502),
        ("packing", "packing_app.py", "PORT_PACKING", 8503), ("request", "stock_request_app.py", "PORT_REQUEST", 8504)]


def run_step(*args: str) -> None:
    if subprocess.run([sys.executable, *args], cwd=ROOT).returncode != 0:
        raise SystemExit(f"Stopped: {' '.join(args)} failed. Fix the error above and start again.")


def prepare_data() -> None:
    """Only when there is no database: make the demo data (never overwrites real files) and build the database."""
    if DB_FILE.exists():
        return
    print("No database found: making the demo data and building the database (about a minute) ...", flush=True)
    if not any((ROOT / "data" / "raw").glob("*.csv")):
        run_step("make_demo_data.py")
    run_step("build_database.py", "--yes")


def ports() -> dict:
    return {name: int(os.getenv(var, default)) for name, _file, var, default in APPS}


def environment(host_name: str, port: dict) -> dict:
    """The links between the apps. On a server they must use the server's address, not 'localhost'."""
    env = dict(os.environ)
    base = f"http://{os.getenv('PUBLIC_HOST') or 'localhost'}"
    env.setdefault("APP_URL", f"{base}:{port['app']}")
    env.setdefault("STORE_APP_URL", f"{base}:{port['store']}")
    env.setdefault("PACKING_APP_URL", f"{base}:{port['packing']}")
    env.setdefault("STOCK_REQUEST_APP_URL", f"{base}:{port['request']}")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def pump(name: str, process: subprocess.Popen) -> None:
    for line in process.stdout:
        text = line.rstrip()
        if text:
            print(f"[{name}] {text}", flush=True)


def stop_all(processes: dict) -> None:
    for process in processes.values():
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)   # the app and everything it started
            else:
                process.terminate()
    deadline = time.time() + 8
    for process in processes.values():
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        if process.poll() is None:
            process.kill()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo-if-empty", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    if args.demo_if_empty:
        prepare_data()
    if not DB_FILE.exists():
        raise SystemExit("data/retail.db not found. Run:  python build_database.py   (or start with --demo-if-empty)")

    port = ports()
    env = environment(args.host, port)
    processes = {}
    for name, file, _var, _default in APPS:
        cmd = [sys.executable, "-m", "streamlit", "run", file, "--server.port", str(port[name]), "--server.address", args.host,
               "--server.headless", "true", "--browser.gatherUsageStats", "false"]
        processes[name] = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=pump, args=(name, processes[name]), daemon=True).start()

    shown = os.getenv("PUBLIC_HOST") or ("localhost" if args.host == "127.0.0.1" else "this-computer")
    print("\nStarting four apps. Open:", flush=True)
    for name, file, _var, _default in APPS:
        print(f"  [{name}] http://{shown}:{port[name]}", flush=True)
    print("Press Ctrl + C once to stop all of them.\n", flush=True)

    reported = set()
    try:
        while True:
            time.sleep(1)
            for name, process in processes.items():
                if process.poll() is not None and name not in reported:
                    reported.add(name)
                    print(f"!! [{name}] stopped (code {process.returncode}). The other apps keep running.", flush=True)
            if len(reported) == len(processes):
                raise SystemExit("All four apps have stopped.")
    except KeyboardInterrupt:
        print("\nStopping all four apps ...", flush=True)
    finally:
        stop_all(processes)
    print("All stopped.")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    main()
