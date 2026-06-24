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
- `LOG_FILE` — absolute path to the JSON log file that `Logger.py` writes (this is passed to the service as the `KOTAMECH_LOG_FILE` environment variable)

Open 'Logger.py' and complete:
- `filename` — absolute path to the JSON log file.
- `client_name` — client_name
- `device_serial` — device_serial


## 3. Produce the Log File

`logging_client.py` no longer collects data itself. Each tick it reads the whole JSON file at `KOTAMECH_LOG_FILE`, sends it to the backend, then wipes the `logs` and `errors` arrays (consumables are kept). Your machine code is responsible for filling that file in.

Use `Logger.py` to maintain the file:

```json
{
  "client_name": "string",
  "device_serial": "string",
  "logs": ["string"],
  "errors": [{ "error_type": "string", "message": "string" }],
  "consumables": [{ "name": "string", "value": 1.23 }]
}
```

### `Logger.py` helpers

`Logger.py` is a small helper module **your machine code imports and calls** — it is not run by this service or the timer. Keeping the file populated is entirely the machine's responsibility; the client only reads, sends, and clears it. The methods on offer are:

| Method | What it does |
| --- | --- |
| `setup()` | Loads the existing file, or creates it from `default_payload` if absent. Call once at startup. |
| `add_log(message)` | Appends a log string. |
| `add_error(error_type, message)` | Appends an error `{error_type, message}`. |
| `update_consumable(name, value)` | Sets the running cumulative total for a consumable (adds it if new). |
| `get_consumable_value(name)` | Returns the current stored value for a consumable, or `None`. |
| `save_payload()` | Writes the in-memory payload to disk. |
| `clear_logs_and_errors()` | Empties logs and errors, then saves. |

Important: `add_log`, `add_error`, and `update_consumable` only mutate the in-memory payload. **Call `save_payload()` to flush to disk** — the client reads the file, so anything not saved won't be sent.

Consumable `value` is the **running cumulative total used** for that consumable, not the amount used in the last hour. `logging_client.py` leaves consumables in place between ticks; the backend turns successive cumulative readings into per-hour usage.

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
- Run client manually inside the venv: `KOTAMECH_LOG_FILE=/path/to/fileName.json ./venv/bin/python logging_client.py`
- Re-install Python deps: `./venv/bin/pip install -r requirements.txt`

## Backend JSON Reference
### POST `/register`

Registers the device. Idempotent, safe to call every tick. `logging_client.py` calls this before every update.

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
- `consumables`: List of objects. `value` is a `float` representing the **running cumulative total used** for that consumable. The backend stores the last cumulative value per device+consumable and records the difference between successive readings as the usage for that hour. The first reading for a new device+consumable sets the baseline and records `0` usage. Values are assumed to only increase; a counter that resets or is replaced will under-report until it passes its previous high.

Response:

```json
{ "status": "ok", "device_id": 1 }
```

Errors:

- `404 Device not registered` — call `/register` first (or use `logging_client.py`, which does it for you every tick).
