"""Kotamech Logger - per-Pi client.

Invoked by kotamech-logger.service (hourly via kotamech-logger.timer).

For Each Pi:
- Update Configuration Values
- Update Collect Logs/Errors/Consumables Functions
- Disable Key Expiry in Tailscale
"""
import sys
import requests

# --- Configuration Values ---------------------------------------------------------
BACKEND_URL   = "http://100.86.104.44:8000"   # Tailscale Log Server IP:Port
CLIENT_NAME   = "FILL-ME-IN"
DEVICE_SERIAL = "FILL-ME-IN"
# -------------------------------------------------------------------------

REQUEST_TIMEOUT = 30


def register() -> int:
    """POST /register. Idempotent on the backend. Returns the device_id."""
    payload = {
        "client_name": CLIENT_NAME,
        "device_serial": DEVICE_SERIAL,
    }
    r = requests.post(f"{BACKEND_URL}/register", json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()["device_id"]


def update(logs: list[str], errors: list[dict], consumables: list[dict]) -> None:
    """POST /update.

    logs:        list[str]
    errors:      list[{"error_type": str, "message": str}]
    consumables: list[{"name": str, "value": float}]
    """
    payload = {
        "client_name": CLIENT_NAME,
        "device_serial": DEVICE_SERIAL,
        "logs": logs,
        "errors": errors,
        "consumables": consumables,
    }
    r = requests.post(f"{BACKEND_URL}/update", json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()


# --- Temporary Values - MUST BE UPDATED -----------------------
def collect_logs() -> list[str]:
    return ["pi heartbeat ok"]


def collect_errors() -> list[dict]:
    return [{"error_type": "example", "message": "replace me"}]

# The backend assumes the value sent is the consumables used in the past hour,
#  not the total consumables left in the machine. If that changes, the backend needs to be updated
def collect_consumables() -> list[dict]:
    return [{"name": "example_consumable", "value": 1.0}]
# -------------------------------------------------------------------------


# Main Function -> Register Device, Collect Data, Send Update
def main() -> None:
    try:
        device_id = register()
        logs = collect_logs()
        errors = collect_errors()
        consumables = collect_consumables()
        update(logs, errors, consumables)
        print(
            f"ok device_id={device_id} "
            f"logs={len(logs)} errors={len(errors)} consumables={len(consumables)}"
        )
    except Exception as e:
        print(f"kotamech-logger client failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
