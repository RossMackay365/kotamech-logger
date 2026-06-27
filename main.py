import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from utils import (
    cleanup_retention,
    chart_payload,
    get_conn,
    get_or_create_device,
    init_db,
    record_consumable_usage,
    resolve_device_id,
    run_incremental_vacuum,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Data Cleanup
CLEANUP_INTERVAL_SECONDS = 3600
# Database Freeing
VACUUM_EVERY_N_TICKS = 24 * 7

# Data Retention Function - Called Every Hour
async def _retention_loop():
    tick = 0
    while True:
        try:
            cleanup_retention()
            if tick > 0 and tick % VACUUM_EVERY_N_TICKS == 0:
                run_incremental_vacuum()
        except Exception as e:
            print(f"[retention] cleanup failed: {e}")
        tick += 1
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cleanup_retention()
    task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class RegisterRequest(BaseModel):
    client_name: str
    device_name: str
    device_serial: str


class ErrorEntry(BaseModel):
    error_type: str
    message: str


class ConsumableEntry(BaseModel):
    name: str
    value: float


class UpdateRequest(BaseModel):
    client_name: str
    device_name: str
    device_serial: str
    logs: list[str] = []
    errors: list[ErrorEntry] = []
    consumables: list[ConsumableEntry] = []

# Register New Device with Server
@app.post("/register")
def register(data: RegisterRequest):
    device_id = get_or_create_device(
        data.client_name, data.device_name, data.device_serial
    )
    return {"device_id": device_id}

# Update Server with Logs, Errors, and Consumable Data from Device
@app.post("/update")
def update(data: UpdateRequest):
    device_id = resolve_device_id(
        data.client_name, data.device_name, data.device_serial
    )
    if device_id is None:
        raise HTTPException(status_code=404, detail="Device not registered")

    ts = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        if data.logs:
            conn.executemany(
                "INSERT INTO logs (device_id, timestamp, message) VALUES (?, ?, ?)",
                [(device_id, ts, msg) for msg in data.logs],
            )
        if data.errors:
            conn.executemany(
                "INSERT INTO errors (device_id, timestamp, error_type, message) VALUES (?, ?, ?, ?)",
                [(device_id, ts, e.error_type, e.message) for e in data.errors],
            )
        # Consumable values are cumulative totals; store the per-hour delta.
        for c in data.consumables:
            record_consumable_usage(conn, device_id, ts, c.name, c.value)
    return {"status": "ok", "device_id": device_id}

# Home Page - List of Clients
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT client_name FROM devices ORDER BY client_name COLLATE NOCASE"
        ).fetchall()
    clients = [r["client_name"] for r in rows]
    return templates.TemplateResponse(
        request, "home.html", {"clients": clients}
    )


# Client Page - List of Device Names for a Client
@app.get("/{client_name}", response_class=HTMLResponse)
def view_client(client_name: str, request: Request):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, device_name FROM devices "
            "WHERE client_name = ? ORDER BY device_name COLLATE NOCASE",
            (client_name,),
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Client not found")

    # Group device ids by device_name, preserving alphabetical order.
    ids_by_name: dict[str, list[int]] = {}
    for r in rows:
        ids_by_name.setdefault(r["device_name"], []).append(r["id"])
    device_names = list(ids_by_name.keys())
    chart = chart_payload(list(ids_by_name.items()), split_by_consumable=False)

    return templates.TemplateResponse(
        request,
        "client_view.html",
        {"client_name": client_name, "device_names": device_names, "chart": chart},
    )


# Device Name Page - List of Serials for a Client + Device Name
@app.get("/{client_name}/{device_name}", response_class=HTMLResponse)
def view_device_name(client_name: str, device_name: str, request: Request):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, device_serial FROM devices "
            "WHERE client_name = ? AND device_name = ? "
            "ORDER BY device_serial COLLATE NOCASE",
            (client_name, device_name),
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Device name not found")
    device_serials = [r["device_serial"] for r in rows]
    device_ids = [r["id"] for r in rows]
    # Per-consumable tabs, usage summed across all serials of this device type.
    chart = chart_payload([(device_name, device_ids)], split_by_consumable=True)
    return templates.TemplateResponse(
        request,
        "device_name_view.html",
        {
            "client_name": client_name,
            "device_name": device_name,
            "device_serials": device_serials,
            "chart": chart,
        },
    )


# View Device Data and Logs (HTML Page)
@app.get("/{client_name}/{device_name}/{device_serial}", response_class=HTMLResponse)
def view_device(client_name: str, device_name: str, device_serial: str, request: Request):
    device_id = resolve_device_id(client_name, device_name, device_serial)
    if device_id is None:
        raise HTTPException(status_code=404, detail="Device not found")

    now = datetime.now()
    logs_cutoff = (now - timedelta(days=30)).isoformat(timespec="seconds")
    errors_cutoff = (now - timedelta(days=365 * 2)).isoformat(timespec="seconds")

    today_str = now.date().isoformat()
    days30_cutoff_day = (now.date() - timedelta(days=30)).isoformat()
    yesterday_str = (now.date() - timedelta(days=1)).isoformat()
    days30_window_start = (now.date() - timedelta(days=29)).isoformat()
    current_month_str = now.strftime("%Y-%m")
    current_year_str = now.strftime("%Y")

    # Retrieve Data from Database
    with get_conn() as conn:
        # Logs & Errors
        logs_rows = conn.execute(
            "SELECT timestamp, message FROM logs "
            "WHERE device_id = ? AND timestamp >= ? "
            "ORDER BY timestamp DESC",
            (device_id, logs_cutoff),
        ).fetchall()
        errors_rows = conn.execute(
            "SELECT timestamp, error_type, message FROM errors "
            "WHERE device_id = ? AND timestamp >= ? "
            "ORDER BY timestamp DESC",
            (device_id, errors_cutoff),
        ).fetchall()

        # Latest cumulative total reported per consumable (survives raw retention)
        cumulative_rows = conn.execute(
            "SELECT name, value FROM consumables_cumulative WHERE device_id = ?",
            (device_id,),
        ).fetchall()

        # Current Consumable Totals from Today
        today_rows = conn.execute(
            "SELECT name, SUM(value) AS s FROM consumables_raw "
            "WHERE device_id = ? AND substr(timestamp, 1, 10) = ? "
            "GROUP BY name",
            (device_id, today_str),
        ).fetchall()

        # Past 30 Days
        daily_30_total_rows = conn.execute(
            "SELECT name, SUM(avg_value * sample_count) AS s "
            "FROM consumables_daily "
            "WHERE device_id = ? AND day >= ? AND day <= ? "
            "GROUP BY name",
            (device_id, days30_window_start, yesterday_str),
        ).fetchall()

        # Monthly Totals
        monthly_year_rows = conn.execute(
            "SELECT name, SUM(avg_value * sample_count) AS s "
            "FROM consumables_monthly "
            "WHERE device_id = ? AND substr(month, 1, 4) = ? AND month < ? "
            "GROUP BY name",
            (device_id, current_year_str, current_month_str),
        ).fetchall()
        daily_curr_month_rows = conn.execute(
            "SELECT name, SUM(avg_value * sample_count) AS s "
            "FROM consumables_daily "
            "WHERE device_id = ? AND substr(day, 1, 7) = ? AND day < ? "
            "GROUP BY name",
            (device_id, current_month_str, today_str),
        ).fetchall()

        # Yearly Totals
        yearly_rows = conn.execute(
            "SELECT name, year, avg_value * sample_count AS total "
            "FROM consumables_yearly "
            "WHERE device_id = ? "
            "ORDER BY year DESC, name",
            (device_id,),
        ).fetchall()

        # Per-Reading Totals from Past 30 Days (actual receive time, not rounded)
        hourly_30_rows = conn.execute(
            "SELECT substr(timestamp, 1, 10) AS day, "
            "       substr(timestamp, 12, 8) AS time, "
            "       name, SUM(value) AS total "
            "FROM consumables_raw "
            "WHERE device_id = ? AND substr(timestamp, 1, 10) >= ? "
            "GROUP BY day, time, name",
            (device_id, days30_cutoff_day),
        ).fetchall()

    logs = [{"timestamp": r["timestamp"], "message": r["message"]} for r in logs_rows]
    errors = [
        {"timestamp": r["timestamp"], "error_type": r["error_type"], "message": r["message"]}
        for r in errors_rows
    ]

    # Combine Totals into Current State
    totals: dict[str, dict[str, float]] = {}

    def _bucket(name: str) -> dict[str, float]:
        return totals.setdefault(
            name, {"today": 0.0, "past_30": 0.0, "this_year": 0.0, "total": 0.0}
        )

    # Cumulative totals also seed the bucket, so a consumable shows up even when
    # its usage in every window is zero (e.g. a freshly registered device).
    for r in cumulative_rows:
        _bucket(r["name"])["total"] = r["value"] or 0.0
    for r in today_rows:
        s = r["s"] or 0.0
        b = _bucket(r["name"])
        b["today"] += s
        b["past_30"] += s
        b["this_year"] += s
    for r in daily_30_total_rows:
        _bucket(r["name"])["past_30"] += r["s"] or 0.0
    for r in daily_curr_month_rows:
        _bucket(r["name"])["this_year"] += r["s"] or 0.0
    for r in monthly_year_rows:
        _bucket(r["name"])["this_year"] += r["s"] or 0.0

    current_state = [
        {
            "name": name,
            "today": v["today"],
            "past_30": v["past_30"],
            "this_year": v["this_year"],
            "total": v["total"],
        }
        for name, v in sorted(totals.items())
    ]

    # Organise Totals for Template
    yearly_by_name: dict[str, dict[str, float]] = {}
    for r in yearly_rows:
        yearly_by_name.setdefault(r["name"], {})[r["year"]] = r["total"]
    yearly_years = sorted({r["year"] for r in yearly_rows}, reverse=True)
    yearly_totals = {
        "years": yearly_years,
        "rows": [
            {"name": name, "cells": [yearly_by_name[name].get(y) for y in yearly_years]}
            for name in sorted(yearly_by_name.keys())
        ],
    }

    days_map: dict[str, dict] = {}
    for r in hourly_30_rows:
        d = days_map.setdefault(r["day"], {"names": set(), "times": {}})
        d["names"].add(r["name"])
        d["times"].setdefault(r["time"], {})[r["name"]] = r["total"]
    last_30_days = [
        {
            "day": day,
            "consumable_names": sorted(d["names"]),
            "readings": [
                {"time": t, "by_name": d["times"][t]}
                for t in sorted(d["times"].keys(), reverse=True)
            ],
        }
        for day, d in sorted(days_map.items(), reverse=True)
    ]

    # Per-consumable usage tabs for this single device.
    chart = chart_payload([(device_serial, [device_id])], split_by_consumable=True)

    # Return Template
    return templates.TemplateResponse(
        request,
        "device_view.html",
        {
            "client_name": client_name,
            "device_name": device_name,
            "device_serial": device_serial,
            "logs": logs,
            "errors": errors,
            "current_state": current_state,
            "yearly_totals": yearly_totals,
            "last_30_days": last_30_days,
            "chart": chart,
        },
    )
