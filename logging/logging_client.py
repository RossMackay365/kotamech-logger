"""Kotamech Logger - per-Pi client.

Invoked by kotamech-logger.service (hourly via kotamech-logger.timer).

Reads the JSON log file produced by Logger.py (path taken from the
KOTAMECH_LOG_FILE environment variable), sends its full contents to the
backend, then wipes the logs and errors from the file. Consumables are kept:
their `value` is the running cumulative total used, and the backend derives
per-hour usage from successive readings.

For Each Pi:
- Set BACKEND_URL below
- Point KOTAMECH_LOG_FILE at the JSON file Logger.py writes (see setup.sh)
- Disable Key Expiry in Tailscale
"""
import json
import os
import sys

import requests

# --- Configuration Values ---------------------------------------------------------
BACKEND_URL  = "http://100.86.104.44:8000"   # Tailscale Log Server IP:Port
LOG_FILE_ENV = "KOTAMECH_LOG_FILE"           # env var holding the path to the JSON log file
# -------------------------------------------------------------------------

REQUEST_TIMEOUT = 30

# Identity placeholder; mirrors Logger.py's default_payload. A file still
# carrying this value hasn't been configured yet, so we refuse to register it.
PLACEHOLDER = "TO-DO"

DEFAULT_PAYLOAD = {
    "client_name": PLACEHOLDER,
    "device_serial": PLACEHOLDER,
    "logs": [],
    "errors": [],
    "consumables": [],
}


def log_file_path() -> str:
    path = os.environ.get(LOG_FILE_ENV)
    if not path:
        raise RuntimeError(f"{LOG_FILE_ENV} environment variable is not set")
    return path


def ensure_file(path: str) -> bool:
    """Create the log file with a default skeleton if it doesn't exist.

    Returns True if the file was just created (nothing to send yet).
    """
    if os.path.exists(path):
        return False
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        json.dump(DEFAULT_PAYLOAD, f, indent=4)
    return True


def load_payload(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def require_identity(payload: dict) -> tuple[str, str]:
    """Return (client_name, device_serial), refusing unconfigured placeholders."""
    client_name = payload.get("client_name", "")
    device_serial = payload.get("device_serial", "")
    if client_name in ("", PLACEHOLDER) or device_serial in ("", PLACEHOLDER):
        raise RuntimeError(
            f"client_name/device_serial not configured in the log file "
            f"(got {client_name!r}/{device_serial!r}); refusing to register"
        )
    return client_name, device_serial


def register(client_name: str, device_serial: str) -> int:
    payload = {"client_name": client_name, "device_serial": device_serial}
    r = requests.post(f"{BACKEND_URL}/register", json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()["device_id"]


def update(payload: dict) -> None:
    r = requests.post(f"{BACKEND_URL}/update", json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()


def clear_logs_and_errors(path: str) -> None:
    data = load_payload(path)
    data["logs"] = []
    data["errors"] = []
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


# Main Function -> Read File, Register Device, Send Update, Wipe Logs/Errors
def main() -> None:
    try:
        path = log_file_path()
        payload = load_payload(path)
        device_id = register(payload["client_name"], payload["device_serial"])
        update(payload)
        clear_logs_and_errors(path)
        print(
            f"ok device_id={device_id} "
            f"logs={len(payload.get('logs', []))} "
            f"errors={len(payload.get('errors', []))} "
            f"consumables={len(payload.get('consumables', []))}"
        )
    except Exception as e:
        print(f"kotamech-logger client failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
