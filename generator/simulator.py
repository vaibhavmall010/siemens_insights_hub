"""
generator/simulator.py

Siemens factory sensor simulator.

Mirrors the data ingestion layer of Siemens Insights Hub (formerly MindSphere).
Simulates 5 production machines publishing telemetry at 2Hz over MQTT,
bridged to InfluxDB via line protocol.

Machines modelled (matching real Siemens plant equipment):
  SM-01  SIMOTION CNC milling spindle
  SM-02  SINUMERIK lathe axis controller
  SM-03  SINAMICS conveyor drive
  SM-04  SITRANS compressed air system
  SM-05  SIMATIC robot joint controller

Sensor channels (6 per machine):
  vibration_mm_s   — vibration velocity (mm/s RMS)
  temperature_c    — bearing/motor temperature
  spindle_rpm      — spindle/motor speed
  current_a        — motor current draw
  pressure_bar     — pneumatic/hydraulic pressure
  power_kw         — active power consumption

Fault types (matches Siemens Senseye fault taxonomy):
  bearing_fault    — elevated vibration + temperature
  overload         — current + power spike
  pressure_drop    — pneumatic system leak
  thermal_runaway  — temperature runaway
  spindle_imbalance — vibration spike at resonance frequency
"""

import os
import sys
import time
import json
import random
import logging
import numpy as np
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SIMULATOR] %(message)s",
)
log = logging.getLogger(__name__)

INFLUXDB_URL    = os.getenv("INFLUXDB_URL",    "http://influxdb:8086")
INFLUXDB_TOKEN  = os.getenv("INFLUXDB_TOKEN",  "siemens-insights-token")
INFLUXDB_ORG    = os.getenv("INFLUXDB_ORG",    "siemens")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "factory")
EMIT_INTERVAL   = float(os.getenv("EMIT_INTERVAL", "0.5"))   # seconds (2Hz)
ANOMALY_RATE    = float(os.getenv("ANOMALY_RATE",   "0.04"))  # 4% anomaly rate

MACHINES = {
    "SM-01": {"name": "SIMOTION CNC Spindle",          "line": "Line-A", "hall": "Hall-1"},
    "SM-02": {"name": "SINUMERIK Lathe Controller",    "line": "Line-A", "hall": "Hall-1"},
    "SM-03": {"name": "SINAMICS Conveyor Drive",       "line": "Line-B", "hall": "Hall-2"},
    "SM-04": {"name": "SITRANS Compressor",            "line": "Line-B", "hall": "Hall-2"},
    "SM-05": {"name": "SIMATIC Robot Joint Controller","line": "Line-C", "hall": "Hall-3"},
}

# Normal operating ranges per machine
PROFILES = {
    "SM-01": {"vibration_mm_s": (0.5, 2.0), "temperature_c": (45, 75),
               "spindle_rpm": (2800, 3200), "current_a": (12, 18),
               "pressure_bar": (5.5, 7.5),  "power_kw": (3.5, 6.0)},
    "SM-02": {"vibration_mm_s": (0.3, 1.5), "temperature_c": (40, 70),
               "spindle_rpm": (1200, 1800), "current_a": (8, 15),
               "pressure_bar": (4.0, 6.0),  "power_kw": (2.5, 4.5)},
    "SM-03": {"vibration_mm_s": (0.2, 1.0), "temperature_c": (35, 65),
               "spindle_rpm": (1450, 1550), "current_a": (6, 12),
               "pressure_bar": (0.5, 2.0),  "power_kw": (1.8, 3.2)},
    "SM-04": {"vibration_mm_s": (0.4, 1.8), "temperature_c": (50, 85),
               "spindle_rpm": (2800, 3000), "current_a": (15, 25),
               "pressure_bar": (6.0, 9.0),  "power_kw": (4.5, 7.5)},
    "SM-05": {"vibration_mm_s": (0.1, 0.8), "temperature_c": (30, 55),
               "spindle_rpm": (200, 600),   "current_a": (4, 10),
               "pressure_bar": (3.0, 6.0),  "power_kw": (1.2, 2.8)},
}

SENSOR_COLS = [
    "vibration_mm_s", "temperature_c", "spindle_rpm",
    "current_a", "pressure_bar", "power_kw",
]

FAULT_TYPES = [
    "bearing_fault", "overload", "pressure_drop",
    "thermal_runaway", "spindle_imbalance",
]

FAULT_MULTIPLIERS = {
    "bearing_fault":     {"vibration_mm_s": 3.5, "temperature_c": 1.3},
    "overload":          {"current_a": 2.2,       "power_kw": 2.0},
    "pressure_drop":     {"pressure_bar": 0.25},
    "thermal_runaway":   {"temperature_c": 1.6,   "power_kw": 1.4},
    "spindle_imbalance": {"vibration_mm_s": 4.0,  "spindle_rpm": 0.85},
}


def generate_reading(machine_id: str, rng: np.random.Generator,
                     fault_type: str = None) -> dict:
    profile = PROFILES[machine_id]
    reading = {}

    for sensor, (lo, hi) in profile.items():
        mid   = (lo + hi) / 2
        sigma = (hi - lo) * 0.04
        reading[sensor] = float(np.clip(
            mid + sigma * rng.standard_normal(), lo, hi
        ))

    if fault_type and fault_type in FAULT_MULTIPLIERS:
        for sensor, mult in FAULT_MULTIPLIERS[fault_type].items():
            if sensor in reading:
                reading[sensor] = float(np.clip(
                    reading[sensor] * mult, 0, reading[sensor] * 5
                ))

    reading["machine_id"]  = machine_id
    reading["machine_name"] = MACHINES[machine_id]["name"]
    reading["line"]         = MACHINES[machine_id]["line"]
    reading["hall"]         = MACHINES[machine_id]["hall"]
    reading["fault_type"]   = fault_type or "none"
    reading["is_anomaly"]   = int(fault_type is not None)
    reading["timestamp"]    = datetime.now(timezone.utc).isoformat()

    for k in ["vibration_mm_s", "temperature_c", "spindle_rpm",
              "current_a", "pressure_bar", "power_kw"]:
        reading[k] = round(reading[k], 4)

    return reading


def to_line_protocol(reading: dict) -> str:
    """Convert reading to InfluxDB line protocol."""
    tags = (
        f"machine_id={reading['machine_id']},"
        f"line={reading['line']},"
        f"hall={reading['hall']},"
        f"fault_type={reading['fault_type']}"
    )
    fields = ",".join(
        f"{k}={v}"
        for k, v in reading.items()
        if k not in ["machine_id", "machine_name", "line", "hall",
                     "fault_type", "timestamp"]
        and isinstance(v, (int, float))
    )
    return f"factory_sensors,{tags} {fields}"


def write_to_influxdb(lines: list[str], session) -> bool:
    try:
        url  = f"{INFLUXDB_URL}/api/v2/write?org={INFLUXDB_ORG}&bucket={INFLUXDB_BUCKET}&precision=s"
        resp = session.post(
            url,
            data="\n".join(lines),
            headers={
                "Authorization": f"Token {INFLUXDB_TOKEN}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=5,
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        log.warning(f"InfluxDB write failed: {e}")
        return False


def wait_for_influxdb(max_retries: int = 30, delay: float = 3.0):
    import requests
    log.info("Waiting for InfluxDB to be ready...")
    for i in range(max_retries):
        try:
            r = requests.get(f"{INFLUXDB_URL}/ping", timeout=3)
            if r.status_code == 204:
                log.info("InfluxDB is ready")
                return True
        except Exception:
            pass
        log.info(f"  Retrying ({i+1}/{max_retries})...")
        time.sleep(delay)
    log.error("InfluxDB not available after max retries")
    return False


def run():
    import requests
    rng = np.random.default_rng(42)

    if not wait_for_influxdb():
        sys.exit(1)

    session = requests.Session()
    cycle   = 0
    log.info(f"Starting simulation: {len(MACHINES)} machines at {1/EMIT_INTERVAL:.0f}Hz, "
             f"anomaly rate={ANOMALY_RATE:.0%}")

    while True:
        lines    = []
        cycle   += 1
        readings = []

        for machine_id in MACHINES:
            fault = None
            if rng.random() < ANOMALY_RATE:
                fault = rng.choice(FAULT_TYPES)

            r = generate_reading(machine_id, rng, fault)
            readings.append(r)
            lines.append(to_line_protocol(r))

        ok = write_to_influxdb(lines, session)

        if cycle % 20 == 0:
            anomalies = [r["fault_type"] for r in readings if r["is_anomaly"]]
            log.info(
                f"Cycle {cycle:>5} | Wrote {len(lines)} points "
                f"| Anomalies: {anomalies or 'none'} "
                f"| InfluxDB: {'ok' if ok else 'FAIL'}"
            )

        time.sleep(EMIT_INTERVAL)


if __name__ == "__main__":
    run()
