"""
anomaly/detector.py

Siemens Insights Hub — ML anomaly detection service.

Mirrors the Siemens Senseye predictive analytics engine.
Polls InfluxDB for recent sensor batches every 30 seconds,
runs Isolation Forest inference, and writes anomaly scores
back to InfluxDB for Grafana to visualise.

Also exposes a /health and /status endpoint so Grafana
can check service liveness via a stat panel.
"""

import os
import sys
import time
import json
import logging
import threading
import numpy as np
import requests
from datetime import datetime, timezone
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from fastapi import FastAPI
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ANOMALY] %(message)s",
)
log = logging.getLogger(__name__)

INFLUXDB_URL    = os.getenv("INFLUXDB_URL",    "http://influxdb:8086")
INFLUXDB_TOKEN  = os.getenv("INFLUXDB_TOKEN",  "siemens-insights-token")
INFLUXDB_ORG    = os.getenv("INFLUXDB_ORG",    "siemens")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "factory")
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "30"))
CONTAMINATION   = float(os.getenv("CONTAMINATION", "0.04"))

SENSOR_COLS = [
    "vibration_mm_s", "temperature_c", "spindle_rpm",
    "current_a", "pressure_bar", "power_kw",
]

MACHINES = ["SM-01", "SM-02", "SM-03", "SM-04", "SM-05"]

app     = FastAPI(title="Siemens Insights Hub — Anomaly Detector")
_status = {"cycles": 0, "last_run": None, "anomalies_detected": 0, "model_trained": False}


# ── InfluxDB helpers ──────────────────────────────────────────────────────────

def query_influxdb(flux_query: str) -> list:
    """Execute a Flux query and return parsed CSV rows."""
    try:
        resp = requests.post(
            f"{INFLUXDB_URL}/api/v2/query?org={INFLUXDB_ORG}",
            headers={
                "Authorization": f"Token {INFLUXDB_TOKEN}",
                "Content-Type": "application/vnd.flux",
                "Accept": "application/csv",
            },
            data=flux_query,
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning(f"Query failed: {resp.status_code} {resp.text[:200]}")
            return []

        lines  = resp.text.strip().splitlines()
        if len(lines) < 2:
            return []

        header = [h.strip() for h in lines[0].split(",")]
        rows   = []
        for line in lines[1:]:
            if not line.strip() or line.startswith("#"):
                continue
            vals = line.split(",")
            if len(vals) != len(header):
                continue
            rows.append(dict(zip(header, vals)))
        return rows

    except Exception as e:
        log.warning(f"InfluxDB query error: {e}")
        return []


def write_anomaly_scores(scores: list[dict]) -> bool:
    if not scores:
        return True
    lines = []
    for s in scores:
        tags = (
            f"machine_id={s['machine_id']},"
            f"hall={s.get('hall', 'unknown')}"
        )
        fields = (
            f"anomaly_score={s['anomaly_score']:.4f},"
            f"is_anomaly={s['is_anomaly']},"
            f"confidence={s['confidence']:.4f}"
        )
        lines.append(f"anomaly_scores,{tags} {fields}")

    try:
        resp = requests.post(
            f"{INFLUXDB_URL}/api/v2/write?org={INFLUXDB_ORG}&bucket={INFLUXDB_BUCKET}&precision=s",
            data="\n".join(lines),
            headers={
                "Authorization": f"Token {INFLUXDB_TOKEN}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=5,
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        log.warning(f"Write failed: {e}")
        return False


# ── ML engine ─────────────────────────────────────────────────────────────────

class AnomalyEngine:
    def __init__(self):
        self.models:   dict = {}
        self.scalers:  dict = {}
        self.trained:  bool = False

    def _fetch_recent(self, machine_id: str, window: str = "5m") -> np.ndarray:
        query = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{window})
  |> filter(fn: (r) => r._measurement == "factory_sensors")
  |> filter(fn: (r) => r.machine_id == "{machine_id}")
  |> filter(fn: (r) => r._field =~ /vibration_mm_s|temperature_c|spindle_rpm|current_a|pressure_bar|power_kw/)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> limit(n: 120)
"""
        rows = query_influxdb(query)
        if not rows:
            return np.array([])

        data = []
        for row in rows:
            try:
                vec = [float(row.get(col, 0)) for col in SENSOR_COLS]
                if all(v != 0 for v in vec):
                    data.append(vec)
            except (ValueError, KeyError):
                continue

        return np.array(data) if data else np.array([])

    def train(self) -> bool:
        log.info("Training Isolation Forest models per machine...")
        trained = 0
        for mid in MACHINES:
            X = self._fetch_recent(mid, window="10m")
            if len(X) < 20:
                log.warning(f"  {mid}: insufficient data ({len(X)} rows), skipping")
                continue

            scaler = StandardScaler()
            X_s    = scaler.fit_transform(X)

            clf = IsolationForest(
                n_estimators=100,
                contamination=CONTAMINATION,
                random_state=42,
                n_jobs=-1,
            )
            clf.fit(X_s)

            self.scalers[mid] = scaler
            self.models[mid]  = clf
            trained += 1
            log.info(f"  {mid}: trained on {len(X)} samples")

        self.trained = trained > 0
        _status["model_trained"] = self.trained
        return self.trained

    def score(self) -> list:
        if not self.trained:
            log.info("Models not trained yet, attempting training...")
            if not self.train():
                return []

        results = []
        for mid in MACHINES:
            if mid not in self.models:
                continue

            X = self._fetch_recent(mid, window="1m")
            if len(X) == 0:
                continue

            X_s    = self.scalers[mid].transform(X)
            raw    = self.models[mid].decision_function(X_s)
            preds  = self.models[mid].predict(X_s)

            # Normalise scores to 0-1 (higher = more anomalous)
            score_min, score_max = raw.min(), raw.max()
            if score_max == score_min:
                norm = np.zeros_like(raw)
            else:
                norm = 1 - (raw - score_min) / (score_max - score_min)

            is_anomaly = int((preds == -1).any())
            avg_score  = float(norm.mean())
            confidence = float(abs(raw.mean()))

            results.append({
                "machine_id":    mid,
                "hall":          "Hall-" + mid[-1],
                "anomaly_score": avg_score,
                "is_anomaly":    is_anomaly,
                "confidence":    confidence,
            })

        return results


engine = AnomalyEngine()


def detection_loop():
    """Background loop — polls InfluxDB and writes anomaly scores."""
    log.info(f"Anomaly detection loop starting (poll every {POLL_INTERVAL}s)...")
    time.sleep(20)  # Wait for simulator to seed data

    # Initial training
    while not engine.trained:
        if engine.train():
            break
        log.info("Waiting for sensor data to accumulate...")
        time.sleep(15)

    while True:
        try:
            scores = engine.score()
            if scores:
                write_anomaly_scores(scores)
                n_anom = sum(s["is_anomaly"] for s in scores)
                _status["anomalies_detected"] += n_anom
                _status["last_run"] = datetime.now(timezone.utc).isoformat()
                _status["cycles"]  += 1
                if n_anom:
                    log.warning(f"Cycle {_status['cycles']} | {n_anom} anomalies: "
                                f"{[s['machine_id'] for s in scores if s['is_anomaly']]}")
                else:
                    log.info(f"Cycle {_status['cycles']} | All machines normal")

            # Retrain every 10 cycles
            if _status["cycles"] % 10 == 0 and _status["cycles"] > 0:
                log.info("Scheduled model retrain...")
                engine.train()

        except Exception as e:
            log.error(f"Detection loop error: {e}")

        time.sleep(POLL_INTERVAL)


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    t = threading.Thread(target=detection_loop, daemon=True)
    t.start()


@app.get("/health")
def health():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/status")
def status():
    return {**_status, "machines": len(MACHINES), "poll_interval_s": POLL_INTERVAL}


@app.get("/scores")
def get_scores():
    """Return latest anomaly scores for all machines."""
    return {"scores": engine.score(), "timestamp": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    uvicorn.run("anomaly.detector:app", host="0.0.0.0", port=8001, reload=False)
