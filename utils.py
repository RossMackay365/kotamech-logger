import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "database.db"

LOGS_RETENTION_DAYS = 30
ERRORS_RETENTION_DAYS = 365 * 2
RAW_RETENTION_DAYS = 30
DAILY_RETENTION_DAYS = 30
MONTHLY_RETENTION_MONTHS = 24


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                device_name TEXT NOT NULL,
                device_serial TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                UNIQUE(client_name, device_name, device_serial)
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                timestamp DATETIME NOT NULL,
                message TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                timestamp DATETIME NOT NULL,
                error_type TEXT NOT NULL,
                message TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            CREATE TABLE IF NOT EXISTS consumables_raw (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                timestamp DATETIME NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            CREATE TABLE IF NOT EXISTS consumables_cumulative (
                device_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (device_id, name),
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            CREATE TABLE IF NOT EXISTS consumables_daily (
                device_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                name TEXT NOT NULL,
                avg_value REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                PRIMARY KEY (device_id, day, name),
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            CREATE TABLE IF NOT EXISTS consumables_monthly (
                device_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                name TEXT NOT NULL,
                avg_value REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                PRIMARY KEY (device_id, month, name),
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            CREATE TABLE IF NOT EXISTS consumables_yearly (
                device_id INTEGER NOT NULL,
                year TEXT NOT NULL,
                name TEXT NOT NULL,
                avg_value REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                PRIMARY KEY (device_id, year, name),
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            CREATE INDEX IF NOT EXISTS idx_logs_device_time ON logs(device_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_errors_device_time ON errors(device_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_consumables_device_time ON consumables_raw(device_id, timestamp);
            """
        )

# Initialise Device (or Get Existing) and Return ID
def get_or_create_device(client_name: str, device_name: str, device_serial: str) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM devices "
            "WHERE client_name = ? AND device_name = ? AND device_serial = ?",
            (client_name, device_name, device_serial),
        ).fetchone()
        # If Device Exists -> Return Device ID
        if row:
            return row["id"]

        # Else -> Create Device and Return New ID
        cur = conn.execute(
            "INSERT INTO devices (client_name, device_name, device_serial, created_at) "
            "VALUES (?, ?, ?, ?)",
            (client_name, device_name, device_serial, now),
        )
        return cur.lastrowid

# Convert a Cumulative Consumable Total into Per-Hour Usage and Store It
def record_consumable_usage(
    conn: sqlite3.Connection,
    device_id: int,
    timestamp: str,
    name: str,
    cumulative_value: float,
) -> None:
    """Store per-hour usage derived from a running cumulative total.

    Clients report the cumulative total used for each consumable. The first
    reading for a (device, consumable) sets the baseline and records 0 usage;
    every later reading records (current - previous) as the usage for the hour.
    The latest cumulative value is persisted so it survives raw-data retention.
    """
    row = conn.execute(
        "SELECT value FROM consumables_cumulative WHERE device_id = ? AND name = ?",
        (device_id, name),
    ).fetchone()
    usage = 0.0 if row is None else cumulative_value - row["value"]

    conn.execute(
        "INSERT INTO consumables_raw (device_id, timestamp, name, value) VALUES (?, ?, ?, ?)",
        (device_id, timestamp, name, usage),
    )
    conn.execute(
        "INSERT INTO consumables_cumulative (device_id, name, value) VALUES (?, ?, ?) "
        "ON CONFLICT(device_id, name) DO UPDATE SET value = excluded.value",
        (device_id, name, cumulative_value),
    )


# Get Device ID (None if Not Found)
def resolve_device_id(client_name: str, device_name: str, device_serial: str) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM devices "
            "WHERE client_name = ? AND device_name = ? AND device_serial = ?",
            (client_name, device_name, device_serial),
        ).fetchone()
        return row["id"] if row else None

# Cleanup Helper Functions
def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def _rollup_daily(conn: sqlite3.Connection, today_iso: str) -> None:
    # Aggregate Hourly Data into Daily Summary
    conn.execute(
        """
        INSERT OR IGNORE INTO consumables_daily (device_id, day, name, avg_value, sample_count)
        SELECT device_id, substr(timestamp, 1, 10) AS day, name,
               AVG(value), COUNT(*)
        FROM consumables_raw
        WHERE substr(timestamp, 1, 10) < ?
        GROUP BY device_id, day, name
        """,
        (today_iso,),
    )


def _rollup_monthly(conn: sqlite3.Connection, current_month: str) -> None:
    # Aggregate Daily Data into Monthly Summary
    conn.execute(
        """
        INSERT OR IGNORE INTO consumables_monthly (device_id, month, name, avg_value, sample_count)
        SELECT device_id, substr(day, 1, 7) AS month, name,
               SUM(avg_value * sample_count) / SUM(sample_count),
               SUM(sample_count)
        FROM consumables_daily
        WHERE substr(day, 1, 7) < ?
        GROUP BY device_id, month, name
        """,
        (current_month,),
    )

def _rollup_yearly(conn: sqlite3.Connection, current_year: str) -> None:
    # Aggregate Monthly Data into Yearly Summary
    conn.execute(
        """
        INSERT OR IGNORE INTO consumables_yearly (device_id, year, name, avg_value, sample_count)
        SELECT device_id, substr(month, 1, 4) AS year, name,
               SUM(avg_value * sample_count) / SUM(sample_count),
               SUM(sample_count)
        FROM consumables_monthly
        WHERE substr(month, 1, 4) < ?
        GROUP BY device_id, year, name
        """,
        (current_year,),
    )


# Cleanup Function -> Combines Data, Deletes Expired Data
def cleanup_retention() -> None:
    now = datetime.now()
    today_iso = now.date().isoformat()
    current_month = now.strftime("%Y-%m")
    current_year = now.strftime("%Y")

    monthly_cutoff_y, monthly_cutoff_m = _shift_month(
        now.year, now.month, -MONTHLY_RETENTION_MONTHS
    )
    monthly_cutoff = f"{monthly_cutoff_y:04d}-{monthly_cutoff_m:02d}"
    daily_cutoff = (now.date() - timedelta(days=DAILY_RETENTION_DAYS)).isoformat()
    raw_cutoff = (now - timedelta(days=RAW_RETENTION_DAYS)).isoformat(timespec="seconds")
    logs_cutoff = (now - timedelta(days=LOGS_RETENTION_DAYS)).isoformat(timespec="seconds")
    errors_cutoff = (now - timedelta(days=ERRORS_RETENTION_DAYS)).isoformat(timespec="seconds")

    with get_conn() as conn:
        _rollup_daily(conn, today_iso)
        _rollup_monthly(conn, current_month)
        _rollup_yearly(conn, current_year)

        conn.execute("DELETE FROM consumables_raw WHERE timestamp < ?", (raw_cutoff,))
        conn.execute("DELETE FROM consumables_daily WHERE day < ?", (daily_cutoff,))
        conn.execute(
            "DELETE FROM consumables_monthly WHERE month < ?", (monthly_cutoff,)
        )
        conn.execute("DELETE FROM logs WHERE timestamp < ?", (logs_cutoff,))
        conn.execute("DELETE FROM errors WHERE timestamp < ?", (errors_cutoff,))


def run_incremental_vacuum() -> None:
    # Reclaim Free Data Pages After Cleanup
    with get_conn() as conn:
        conn.execute("PRAGMA incremental_vacuum")


# ----- Chart data: consumable usage-per-period time series -----------------------

RANGE_KEYS = ["24h", "30d", "12mo", "5y"]
RANGE_LABELS = {
    "24h": "Last 24 hours",
    "30d": "Last 30 days",
    "12mo": "Last 12 months",
    "5y": "Last 5 years",
}
DEFAULT_RANGE = "30d"


def _placeholders(n: int) -> str:
    return ",".join(["?"] * n)


def _range_buckets(range_key: str, now: datetime) -> tuple[list[str], list[str]]:
    """(bucket_keys, display_labels) ordered oldest -> newest for a range."""
    if range_key == "24h":
        hours = [now - timedelta(hours=i) for i in range(23, -1, -1)]
        return [h.strftime("%Y-%m-%dT%H") for h in hours], [h.strftime("%H:00") for h in hours]
    if range_key == "30d":
        days = [now.date() - timedelta(days=i) for i in range(29, -1, -1)]
        return [d.isoformat() for d in days], [d.strftime("%m-%d") for d in days]
    if range_key == "12mo":
        keys = []
        for i in range(11, -1, -1):
            y, m = _shift_month(now.year, now.month, -i)
            keys.append(f"{y:04d}-{m:02d}")
        return keys, list(keys)
    if range_key == "5y":
        keys = [f"{now.year - i:04d}" for i in range(4, -1, -1)]
        return keys, list(keys)
    raise ValueError(f"unknown range: {range_key}")


def _current_month_totals(conn, ids: list[int], ph: str, now: datetime) -> dict[str, float]:
    """Usage in the current (partial) month: completed days + today's raw."""
    today = now.date().isoformat()
    current_month = now.strftime("%Y-%m")
    totals: dict[str, float] = {}
    for r in conn.execute(
        f"SELECT name, SUM(avg_value * sample_count) s FROM consumables_daily "
        f"WHERE device_id IN ({ph}) AND substr(day, 1, 7) = ? AND day < ? GROUP BY name",
        (*ids, current_month, today),
    ).fetchall():
        totals[r["name"]] = totals.get(r["name"], 0.0) + (r["s"] or 0.0)
    for r in conn.execute(
        f"SELECT name, SUM(value) s FROM consumables_raw "
        f"WHERE device_id IN ({ph}) AND substr(timestamp, 1, 10) = ? GROUP BY name",
        (*ids, today),
    ).fetchall():
        totals[r["name"]] = totals.get(r["name"], 0.0) + (r["s"] or 0.0)
    return totals


def _current_year_totals(conn, ids: list[int], ph: str, now: datetime) -> dict[str, float]:
    """Usage in the current (partial) year: completed months + current month."""
    current_year = now.strftime("%Y")
    current_month = now.strftime("%Y-%m")
    totals: dict[str, float] = {}
    for r in conn.execute(
        f"SELECT name, SUM(avg_value * sample_count) s FROM consumables_monthly "
        f"WHERE device_id IN ({ph}) AND substr(month, 1, 4) = ? AND month < ? GROUP BY name",
        (*ids, current_year, current_month),
    ).fetchall():
        totals[r["name"]] = totals.get(r["name"], 0.0) + (r["s"] or 0.0)
    for name, val in _current_month_totals(conn, ids, ph, now).items():
        totals[name] = totals.get(name, 0.0) + val
    return totals


def usage_series(
    conn, device_ids: list[int], range_key: str, now: datetime
) -> dict[str, list[float]]:
    """Per-period usage per consumable for a set of devices, aligned to the
    range's buckets (missing buckets = 0). Returns {consumable: [values]}."""
    keys, _labels = _range_buckets(range_key, now)
    n = len(keys)
    idx_of = {k: i for i, k in enumerate(keys)}
    series: dict[str, list[float]] = {}
    if not device_ids:
        return series
    ids = list(device_ids)
    ph = _placeholders(len(ids))

    def add(name: str, key: str, value) -> None:
        if value is None or key not in idx_of:
            return
        series.setdefault(name, [0.0] * n)[idx_of[key]] += value

    if range_key in ("24h", "30d"):
        span = 13 if range_key == "24h" else 10
        rows = conn.execute(
            f"SELECT substr(timestamp, 1, {span}) b, name, SUM(value) s "
            f"FROM consumables_raw WHERE device_id IN ({ph}) "
            f"AND substr(timestamp, 1, {span}) >= ? GROUP BY b, name",
            (*ids, keys[0]),
        ).fetchall()
        for r in rows:
            add(r["name"], r["b"], r["s"])
    elif range_key == "12mo":
        current_month = now.strftime("%Y-%m")
        for r in conn.execute(
            f"SELECT month, name, SUM(avg_value * sample_count) s "
            f"FROM consumables_monthly WHERE device_id IN ({ph}) "
            f"AND month >= ? AND month < ? GROUP BY month, name",
            (*ids, keys[0], current_month),
        ).fetchall():
            add(r["name"], r["month"], r["s"])
        for name, val in _current_month_totals(conn, ids, ph, now).items():
            add(name, current_month, val)
    elif range_key == "5y":
        current_year = now.strftime("%Y")
        for r in conn.execute(
            f"SELECT year, name, SUM(avg_value * sample_count) s "
            f"FROM consumables_yearly WHERE device_id IN ({ph}) "
            f"AND year >= ? AND year < ? GROUP BY year, name",
            (*ids, keys[0], current_year),
        ).fetchall():
            add(r["name"], r["year"], r["s"])
        for name, val in _current_year_totals(conn, ids, ph, now).items():
            add(name, current_year, val)

    return series


def _chart_envelope(now: datetime) -> dict:
    return {
        "ranges": RANGE_KEYS,
        "rangeLabels": RANGE_LABELS,
        "defaultRange": DEFAULT_RANGE,
        "labels": {rk: _range_buckets(rk, now)[1] for rk in RANGE_KEYS},
        "tabs": [],
        "data": {},
    }


def chart_payload(groups: list[tuple[str, list[int]]], split_by_consumable: bool) -> dict:
    """Build the uniform chart payload consumed by the frontend.

    `groups` is an ordered list of (label, device_ids).
    - split_by_consumable=True (device pages): a single group whose consumables
      each become their own tab (one line per chart).
    - split_by_consumable=False (client page): each group is a tab whose chart
      carries one line per consumable.

    Shape: data[tab][range] = {consumable: [values]}, lines = that dict's keys.
    """
    now = datetime.now()
    payload = _chart_envelope(now)
    with get_conn() as conn:
        if split_by_consumable:
            _label, device_ids = groups[0]
            by_range = {rk: usage_series(conn, device_ids, rk, now) for rk in RANGE_KEYS}
            consumables = sorted({c for s in by_range.values() for c in s})
            payload["tabs"] = consumables
            for c in consumables:
                payload["data"][c] = {}
                for rk in RANGE_KEYS:
                    n = len(payload["labels"][rk])
                    payload["data"][c][rk] = {c: by_range[rk].get(c, [0.0] * n)}
        else:
            payload["tabs"] = [label for label, _ in groups]
            for label, device_ids in groups:
                payload["data"][label] = {
                    rk: usage_series(conn, device_ids, rk, now) for rk in RANGE_KEYS
                }
    return payload
