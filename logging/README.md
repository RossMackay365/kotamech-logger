# Pi Setup

How to set up a new Raspberry Pi.

## 1. Copy The /loggin Folder to The Raspberry Pi

## 2. Edit Placeholders

Open `setup.sh` and complete:

- `TS_AUTHKEY` — the Tailscale auth key
- `TS_HOSTNAME` — what this Pi should be called on the tailnet

Open `client.py` and complete:

- `BACKEND_URL` — The backend's tailnet address (e.g. `http://log-server:8000`)
- `CLIENT_NAME` — The client for the device
- `DEVICE_SERIAL` — The device's serial number

## 3. Replace Example Data

Open `client.py` and replace the `collect_logs`, `collect_errors`, and `collect_consumables` functions with whatever you actually want to send.

Do this **before** running the setup script. Once setup runs, the timer starts firing within a couple of minutes and any leftover example data will be sent to the backend.

## 4. Run Setup Shell Script

```
cd pi
sudo ./setup.sh
```

This installs Python, installs and connects Tailscale, and sets up the hourly update job.

## Useful Commands

- See logs: `journalctl -u kotamech-logger.service -f`
- Trigger an update now: `sudo systemctl start kotamech-logger.service`
- Check the timer: `systemctl status kotamech-logger.timer`
