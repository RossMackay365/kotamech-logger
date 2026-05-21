# Pi Setup

How to set up a new Raspberry Pi.

## 1. Copy The /logging Folder to The Raspberry Pi

Put it anywhere stable (e.g. `/home/admin/logging`). Don't move it after running setup, as the systemd service uses an absolute path from the installation. To copy the folder across, use the following command (run from the ./kotamech_logger directory):

```
scp -r ./logging admin@IP_ADDRESS:/home/admin/
```

This only works if you have SSH access to the Pi you want to copy across to.

## 2. Edit Placeholders

Open `setup.sh` and complete:

- `TS_AUTHKEY` — the Tailscale auth key
- `TS_HOSTNAME` — what this Pi should be called on the tailnet

Open `client.py` and complete:

- `BACKEND_URL` — The backend's tailnet address (e.g. `http://log-server:8000`)
- `CLIENT_NAME` — The client for the device
- `DEVICE_SERIAL` — The device's serial number

## 3. Replace Example Data

Open `client.py` and replace the `collect_logs`, `collect_errors`, and `collect_consumables` functions with whatever you actually want to send. See below for the exact shapes the backend expects.

Do this **before** running the setup script. Once setup runs, the timer starts firing within a couple of minutes and any leftover example data will be sent to the backend.

## 4. Run Setup Shell Script

```
cd logging
chmod +x setup.sh
sudo ./setup.sh
```

This installs Python + `python3-venv`, installs and connects Tailscale, creates `./venv` and `pip install`s `requirements.txt`, then installs and enables the hourly systemd timer.

## Useful Commands

- See logs: `journalctl -u kotamech-logger.service -f`
- Trigger an update now: `sudo systemctl start kotamech-logger.service`
- Check the timer: `systemctl status kotamech-logger.timer`
- Run client manually inside the venv: `./venv/bin/python client.py`
- Re-install Python deps: `./venv/bin/pip install -r requirements.txt`

## Backend JSON Reference
### POST `/register`

Registers the device. Idempotent, safe to call every tick. `client.py` calls this before every update.

Request body:

```json
{
  "client_name": "string",
  "device_serial": "string"
}
```

Response:

```json
{ "device_id": 1 }
```

### POST `/update`

Sends a batch of logs, errors, and/or consumables for this device. All three lists are optional and default to `[]` — send only what you have.

Request body:

```json
{
  "client_name": "string",
  "device_serial": "string",
  "logs": [
    "string",
    "string"
  ],
  "errors": [
    { "error_type": "string", "message": "string" }
  ],
  "consumables": [
    { "name": "string", "value": 1.23 }
  ]
}
```

Field details:

- `logs`: List of plain strings. Each string is stored as one log row with a server-side timestamp.
- `errors`: List of objects. `error_type` is a short category label (e.g. `"network"`, `"hardware"`); `message` is the human-readable detail.
- `consumables`: List of objects. `value` is a `float` representing **usage in the past hour**, not the running total left in the machine. The backend sums these over time. If you ever switch to sending totals, the backend will need to change too.

Response:

```json
{ "status": "ok", "device_id": 1 }
```

Errors:

- `404 Device not registered` — call `/register` first (or use `client.py`, which does it for you every tick).
