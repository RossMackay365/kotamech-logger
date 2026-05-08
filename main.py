import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from utils import (
    cleanup_retention,
    get_conn,
    get_or_create_device,
    init_db,
    resolve_device_id,
    run_incremental_vacuum,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

CLEANUP_INTERVAL_SECONDS = 3600
VACUUM_EVERY_N_TICKS = 24 * 7  # weekly when interval is hourly


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


class RegisterRequest(BaseModel):
    client_name: str
    device_serial: str


class ErrorEntry(BaseModel):
    error_type: str
    message: str


class ConsumableEntry(BaseModel):
    name: str
    value: float


class UpdateRequest(BaseModel):
    client_name: str
    device_serial: str
    logs: list[str] = []
    errors: list[ErrorEntry] = []
    consumables: list[ConsumableEntry] = []


@app.post("/register")
def register(data: RegisterRequest):
    device_id = get_or_create_device(data.client_name, data.device_serial)
    return {"device_id": device_id}


@app.post("/update")
def update(data: UpdateRequest):
    device_id = resolve_device_id(data.client_name, data.device_serial)
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
        if data.consumables:
            conn.executemany(
                "INSERT INTO consumables_raw (device_id, timestamp, name, value) VALUES (?, ?, ?, ?)",
                [(device_id, ts, c.name, c.value) for c in data.consumables],
            )
    return {"status": "ok", "device_id": device_id}


@app.get("/view/{client_name}/{device_serial}", response_class=HTMLResponse)
def view_device(client_name: str, device_serial: str, request: Request):
    device_id = resolve_device_id(client_name, device_serial)
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

    with get_conn() as conn:
        # --- Logs / errors ---
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

        # --- Current state totals: today / past 30 days / this year ---
        # Today: raw SUM per consumable for today.
        today_rows = conn.execute(
            "SELECT name, SUM(value) AS s FROM consumables_raw "
            "WHERE device_id = ? AND substr(timestamp, 1, 10) = ? "
            "GROUP BY name",
            (device_id, today_str),
        ).fetchall()
        # Past 30 days, days [today-29 .. yesterday] from daily tier; today from raw.
        daily_30_total_rows = conn.execute(
            "SELECT name, SUM(avg_value * sample_count) AS s "
            "FROM consumables_daily "
            "WHERE device_id = ? AND day >= ? AND day <= ? "
            "GROUP BY name",
            (device_id, days30_window_start, yesterday_str),
        ).fetchall()
        # This year: monthly tier for past months in this year + daily for current
        # month (excluding today) + raw for today. Tiers don't overlap by design.
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

        # --- Yearly totals from yearly tier (one row per name per year) ---
        yearly_rows = conn.execute(
            "SELECT name, year, avg_value * sample_count AS total "
            "FROM consumables_yearly "
            "WHERE device_id = ? "
            "ORDER BY year DESC, name",
            (device_id,),
        ).fetchall()

        # --- Past 30 days breakdown: per-hour totals from raw ---
        hourly_30_rows = conn.execute(
            "SELECT substr(timestamp, 1, 10) AS day, "
            "       substr(timestamp, 12, 2) AS hour, "
            "       name, SUM(value) AS total "
            "FROM consumables_raw "
            "WHERE device_id = ? AND substr(timestamp, 1, 10) >= ? "
            "GROUP BY day, hour, name",
            (device_id, days30_cutoff_day),
        ).fetchall()

    logs = [{"timestamp": r["timestamp"], "message": r["message"]} for r in logs_rows]
    errors = [
        {"timestamp": r["timestamp"], "error_type": r["error_type"], "message": r["message"]}
        for r in errors_rows
    ]

    # Combine current-state totals per consumable name.
    totals: dict[str, dict[str, float]] = {}

    def _bucket(name: str) -> dict[str, float]:
        return totals.setdefault(name, {"today": 0.0, "past_30": 0.0, "this_year": 0.0})

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
        {"name": name, "today": v["today"], "past_30": v["past_30"], "this_year": v["this_year"]}
        for name, v in sorted(totals.items())
    ]

    # Yearly totals pivoted: one row per name, one column per year.
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

    # Past 30 days breakdown: per-day -> per-hour -> per-consumable total.
    days_map: dict[str, dict] = {}
    for r in hourly_30_rows:
        d = days_map.setdefault(r["day"], {"names": set(), "hours": {}})
        d["names"].add(r["name"])
        d["hours"].setdefault(r["hour"], {})[r["name"]] = r["total"]
    last_30_days = [
        {
            "day": day,
            "consumable_names": sorted(d["names"]),
            "hours": [
                {"hour": h, "by_name": d["hours"][h]}
                for h in sorted(d["hours"].keys(), reverse=True)
            ],
        }
        for day, d in sorted(days_map.items(), reverse=True)
    ]

    return templates.TemplateResponse(
        request,
        "device_view.html",
        {
            "client_name": client_name,
            "device_serial": device_serial,
            "logs": logs,
            "errors": errors,
            "current_state": current_state,
            "yearly_totals": yearly_totals,
            "last_30_days": last_30_days,
        },
    )
