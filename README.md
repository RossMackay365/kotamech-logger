# Kotamech Logger

A simple logging system for collecting data from remote devices (Raspberry Pis) and viewing it in a web browser.

## What It Does

- Each device sends **logs**, **errors**, and **consumable usage** to a central server once an hour.
- The server stores the data, automatically rolls older numbers up into daily/monthly/yearly summaries, and serves a web page per device.
- Everything talks over [Tailscale](https://tailscale.com), so devices and the server are on a private network, no public ports, no exposed IPs.

## Accessing the Server via Tailscale

1. Install Tailscale on your computer: <https://tailscale.com/download>
2. Log in to the same tailnet as the server and Pis.
3. Open the server in your browser:

   ```
   http://log-server:8000/view/<client_name>/<device_serial>
   ```

   Replace `log-server` with whatever the server's tailnet hostname is (check the Tailscale admin console if unsure). `<client_name>` and `<device_serial>` are whatever was configured on the Pi.

4. Log in with the admin account: `admin@log-server`.

## System Design

```
   ┌──────────────┐         Tailscale          ┌──────────────┐
   │ Raspberry Pi │ ─── POST /update (hourly) ─▶│   Server     │
   │  (client.py) │ ◀──────── responses ────── │  (main.py)   │
   └──────────────┘                             │   SQLite     │
   ┌──────────────┐                             │              │
   │ Raspberry Pi │ ────────────────────────────▶              │
   └──────────────┘                             └──────┬───────┘
                                                       │
                                                  Web browser
                                              http://<server>:8000
                                              /view/<client>/<serial>
```

- **Server** ([main.py](main.py)) — FastAPI app. Receives data, stores it in a local SQLite file ([database.db](database.db)), serves the view page.
- **Client** ([logging/client.py](logging/client.py)) — runs on each Pi, fires every hour via a systemd timer, sends a batch of data to the server.
- **Database** — SQLite. Logs kept 30 days, errors kept 2 years, consumables rolled up automatically and kept for years.

## Adding a New Device

See [logging/README.md](logging/README.md) for the full Pi setup walkthrough. Short version:

1. Copy the `logging/` folder onto the Pi.
2. Edit `setup.sh` (Tailscale key + hostname) and `client.py` (server URL, client name, device serial).
3. Run `sudo ./setup.sh`.

The Pi will join the tailnet, install itself as a systemd timer, and start sending data within a couple of minutes.

## Redeploying the Server

The server runs on the Pi as a systemd service (`log-server.service`) that simply
runs uvicorn:

```
ExecStart=/home/admin/log-server/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
```

`Restart=always` only restarts the *process* if it crashes — it does **not** pull
new code. There is no auto-deploy, so redeploying means copying the new files up
and restarting the service.

From this project directory on your machine, push the server files (not `venv`,
`__pycache__`, or `database.db`):

```bash
scp main.py utils.py requirements.txt admin@log-server:~/log-server/
scp -r templates admin@log-server:~/log-server/
```

Then on the Pi:

```bash
ssh admin@log-server

sudo systemctl stop log-server          # release the DB file lock

# Optional: start from a clean database. init_db() rebuilds an empty schema
# (CREATE TABLE IF NOT EXISTS) on startup, so just delete the file + sidecars.
rm -f ~/log-server/database.db ~/log-server/database.db-wal ~/log-server/database.db-shm

# Only needed if requirements.txt changed:
~/log-server/venv/bin/pip install -r ~/log-server/requirements.txt

sudo systemctl start log-server
systemctl status log-server --no-pager
journalctl -u log-server -f             # watch it boot
```

## Project Layout

```
kotamech_logger/
├── main.py              # FastAPI server
├── utils.py             # Database setup, rollups, retention
├── seed_db.py           # Test data seeder (optional)
├── database.db          # SQLite database (auto-created)
├── templates/           # HTML view page
├── requirements.txt     # Server Python dependencies
└── logging/             # Everything that goes on the Pi
    ├── client.py
    ├── setup.sh
    ├── kotamech-logger.service
    ├── kotamech-logger.timer
    └── README.md        # Pi setup instructions
```