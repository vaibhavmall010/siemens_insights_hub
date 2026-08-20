"""
webhook/alert_handler.py

Grafana alert webhook receiver.

Mirrors Siemens Insights Hub alert management and notification routing.
Receives POST callbacks from Grafana when alert rules fire or resolve.
Persists alerts to SQLite and exposes an alert history API.

Grafana → (POST /alert) → this service → SQLite → /alerts API → Grafana stat panel
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WEBHOOK] %(message)s",
)
log = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "/data/alerts.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = FastAPI(title="Siemens Insights Hub — Alert Webhook")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Database ──────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS alerts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id    TEXT,
                title       TEXT,
                state       TEXT,
                severity    TEXT DEFAULT 'warning',
                machine_id  TEXT,
                message     TEXT,
                value       TEXT,
                dashboard   TEXT,
                panel       TEXT,
                url         TEXT,
                received_at TEXT DEFAULT (datetime('now')),
                raw_payload TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_alerts_machine
                ON alerts(machine_id, received_at);
            CREATE INDEX IF NOT EXISTS idx_alerts_state
                ON alerts(state, received_at);
        """)


# ── Schemas ───────────────────────────────────────────────────────────────────

class GrafanaAlert(BaseModel):
    title:      Optional[str] = "Unknown alert"
    state:      Optional[str] = "alerting"
    message:    Optional[str] = ""
    ruleId:     Optional[int] = None
    ruleName:   Optional[str] = ""
    ruleUrl:    Optional[str] = ""
    evalMatches: Optional[list] = []


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    log.info(f"Alert webhook ready — DB: {DB_PATH}")


@app.get("/health")
def health():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/alert")
async def receive_alert(request: Request):
    """
    Receive Grafana alert callback.
    Grafana sends both legacy (v8) and unified alerting (v9+) formats.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    raw = json.dumps(body)
    log.info(f"Alert received: {raw[:200]}")

    # Parse Grafana unified alerting format (v9+)
    alerts = body.get("alerts", [body])
    inserted = 0

    for alert in alerts:
        labels    = alert.get("labels", {})
        ann       = alert.get("annotations", {})
        title     = alert.get("title", body.get("title", "Unknown alert"))
        state     = alert.get("status", body.get("state", "alerting"))
        machine   = labels.get("machine_id", labels.get("instance", "unknown"))
        severity  = labels.get("severity", "warning")
        message   = ann.get("summary", ann.get("message", body.get("message", "")))
        value     = str(alert.get("values", {}) or body.get("evalMatches", ""))
        url       = alert.get("generatorURL", body.get("ruleUrl", ""))
        dashboard = ann.get("dashboard", labels.get("dashboard", ""))
        panel     = ann.get("panel", labels.get("panel", ""))

        with get_conn() as conn:
            conn.execute("""
                INSERT INTO alerts
                (alert_id, title, state, severity, machine_id, message,
                 value, dashboard, panel, url, raw_payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                alert.get("fingerprint", ""),
                title, state, severity, machine, message,
                value, dashboard, panel, url, raw,
            ))
        inserted += 1

    log.info(f"Persisted {inserted} alert(s) — state={state} machine={machine}")
    return {"status": "received", "inserted": inserted}


@app.get("/alerts")
def list_alerts(
    limit: int = 50,
    machine_id: Optional[str] = None,
    state: Optional[str] = None,
):
    with get_conn() as conn:
        where_parts = []
        params      = []
        if machine_id:
            where_parts.append("machine_id = ?")
            params.append(machine_id)
        if state:
            where_parts.append("state = ?")
            params.append(state)

        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        rows  = conn.execute(
            f"SELECT * FROM alerts {where} ORDER BY received_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()

    return {"alerts": [dict(r) for r in rows], "count": len(rows)}


@app.get("/alerts/summary")
def alert_summary():
    with get_conn() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        firing   = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE state = 'alerting'"
        ).fetchone()[0]
        resolved = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE state = 'ok'"
        ).fetchone()[0]
        by_machine = conn.execute("""
            SELECT machine_id, COUNT(*) as count
            FROM alerts GROUP BY machine_id ORDER BY count DESC
        """).fetchall()

    return {
        "total":      total,
        "firing":     firing,
        "resolved":   resolved,
        "by_machine": [dict(r) for r in by_machine],
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    uvicorn.run("webhook.alert_handler:app", host="0.0.0.0", port=8002, reload=False)
