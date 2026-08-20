# Siemens Insights Hub — Factory Observability Platform

Open-source mirror of Siemens Insights Hub (formerly MindSphere) for industrial
factory observability. Simulates 5 Siemens production machines publishing sensor
telemetry at 2Hz, runs ML anomaly detection, and visualises everything in a
Grafana dashboard — deployable in one command.

---

## Industry context — Siemens Insights Hub

Siemens rebranded MindSphere to Insights Hub in 2023, positioning it as the central
data layer of their Industrial Operations X portfolio. For plants running Siemens PLCs
and drives, Insights Hub provides native connectivity to SIMATIC controllers,
built-in OEE and asset health monitoring, predictive analytics for vibration,
temperature, and process parameters, and alert triggering to maintenance teams.

This project mirrors that architecture using fully open-source tools:

| Siemens Insights Hub component | This project equivalent |
|-------------------------------|------------------------|
| SIMATIC PLC data collection | Python sensor simulator (5 machines, 2Hz) |
| MindConnect IoT gateway | MQTT → InfluxDB bridge |
| Time-series data lake | InfluxDB 2.7 with 30d retention |
| Senseye predictive analytics | Isolation Forest anomaly detector |
| Insights Hub Monitor dashboards | Grafana 10 with auto-provisioned panels |
| Alert management and routing | FastAPI webhook → SQLite alert log |

This is directly relevant to AI/ML engineer roles at Siemens Digital Industries,
Siemens Energy, and the broader Siemens Xcelerator partner ecosystem.

---

## Architecture

```
[Factory Floor Simulation]
  SM-01  SIMOTION CNC Milling Spindle       Hall-1, Line-A
  SM-02  SINUMERIK Lathe Axis Controller    Hall-1, Line-A
  SM-03  SINAMICS Conveyor Drive            Hall-2, Line-B
  SM-04  SITRANS Compressed Air System      Hall-2, Line-B
  SM-05  SIMATIC Robot Joint Controller     Hall-3, Line-C
      |
      | 6 sensor channels at 2Hz:
      | vibration_mm_s, temperature_c, spindle_rpm,
      | current_a, pressure_bar, power_kw
      |
      v
[Python Sensor Generator] (generator/simulator.py)
  Realistic per-machine operating profiles
  5 fault types: bearing_fault, overload, pressure_drop,
                 thermal_runaway, spindle_imbalance
  4% anomaly injection rate, configurable
      |
      v
[InfluxDB 2.7] — time-series storage
  30-day retention policy
  Flux query language
  Two measurements: factory_sensors, anomaly_scores
      |
      v
[Anomaly Detection Service] (anomaly/detector.py)
  Isolation Forest per machine, retrained every 10 cycles
  Polls InfluxDB every 30s
  Writes normalised anomaly scores (0-1) back to InfluxDB
  FastAPI /health, /status, /scores endpoints
      |
      v
[FastAPI Alert Webhook] (webhook/alert_handler.py)
  Receives Grafana alert callbacks (unified alerting v9+)
  Persists to SQLite with machine_id, severity, state
  /alerts, /alerts/summary REST API
      |
      v
[Grafana 10] — observability dashboard
  Auto-provisioned datasource (InfluxDB Flux)
  Auto-provisioned dashboard (factory_dashboard.json)
  Panels: live sensor time-series, anomaly scores,
          OEE gauge, fleet KPIs, alert log table,
          power consumption bar chart
  5-second refresh
```

---

## Quickstart — one command

```bash
git clone https://github.com/vaibhavmall010/siemens-insights-hub
cd siemens-insights-hub
docker-compose up --build
```

Wait about 60 seconds for InfluxDB to initialise and the simulator to seed data.

| Service | URL |
|---------|-----|
| Grafana dashboard | http://localhost:3000 |
| Grafana login | admin / siemens-grafana |
| InfluxDB UI | http://localhost:8086 |
| InfluxDB login | admin / siemens-admin-pass |
| Anomaly detector API | http://localhost:8001/docs |
| Alert webhook API | http://localhost:8002/docs |

The Grafana dashboard loads automatically — no manual setup required.

---

## Project structure

```
siemens_insights_hub/
├── generator/
│   ├── simulator.py         5-machine sensor simulator, fault injection, InfluxDB writer
│   └── Dockerfile
├── anomaly/
│   ├── detector.py          Isolation Forest anomaly detection service + FastAPI
│   └── Dockerfile
├── webhook/
│   ├── alert_handler.py     Grafana alert receiver + SQLite persistence + API
│   └── Dockerfile
├── grafana/
│   └── provisioning/
│       ├── datasources/
│       │   └── influxdb.yaml    InfluxDB connection auto-configured
│       └── dashboards/
│           ├── provider.yaml
│           └── factory_dashboard.json   15-panel dashboard, auto-loaded
├── tests/
│   └── test_pipeline.py     24 tests — no live InfluxDB required
├── .github/workflows/
│   └── ci.yml               GitHub Actions CI/CD
├── docker-compose.yml
└── requirements.txt
```

---

## Grafana dashboard panels

| Panel | Type | What it shows |
|-------|------|---------------|
| Fleet machines online | Stat | Count of active machines in last 1 minute |
| Active anomalies | Stat | Machines currently in anomaly state |
| Avg power consumption | Stat | Fleet-wide average kW with trend sparkline |
| Alerts received (24h) | Stat | Total anomaly events in last 24 hours |
| OEE estimate | Gauge | Overall Equipment Effectiveness proxy (0-100%) |
| Detector service status | Stat | ML service liveness indicator |
| Vibration time-series | Time series | All 5 machines, 10s aggregation |
| Temperature time-series | Time series | All 5 machines with threshold overlays |
| Anomaly score | Time series | ML-computed anomaly score per machine (0-1) |
| Motor current | Time series | Current draw — overload detection |
| Power consumption | Bar chart | Per-machine power in last 5 minutes |
| Pressure | Time series | Pneumatic/hydraulic pressure all machines |
| Anomaly events log | Table | Recent fault events with machine, hall, type |

---

## Machines and sensor profiles

| Machine | Equipment | Normal vibration | Normal temp |
|---------|-----------|-----------------|-------------|
| SM-01 | SIMOTION CNC Spindle | 0.5-2.0 mm/s | 45-75 C |
| SM-02 | SINUMERIK Lathe | 0.3-1.5 mm/s | 40-70 C |
| SM-03 | SINAMICS Conveyor | 0.2-1.0 mm/s | 35-65 C |
| SM-04 | SITRANS Compressor | 0.4-1.8 mm/s | 50-85 C |
| SM-05 | SIMATIC Robot Joint | 0.1-0.8 mm/s | 30-55 C |

---

## Fault types and sensor signatures

| Fault | Sensors affected | Multiplier |
|-------|-----------------|------------|
| bearing_fault | vibration_mm_s, temperature_c | 3.5x, 1.3x |
| overload | current_a, power_kw | 2.2x, 2.0x |
| pressure_drop | pressure_bar | 0.25x |
| thermal_runaway | temperature_c, power_kw | 1.6x, 1.4x |
| spindle_imbalance | vibration_mm_s, spindle_rpm | 4.0x, 0.85x |

---

## Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
# Expected: 24 passed — no live InfluxDB or Grafana required
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| INFLUXDB_URL | http://influxdb:8086 | InfluxDB endpoint |
| INFLUXDB_TOKEN | siemens-insights-token | API token |
| INFLUXDB_ORG | siemens | Organisation name |
| INFLUXDB_BUCKET | factory | Bucket name |
| EMIT_INTERVAL | 0.5 | Seconds between readings (0.5 = 2Hz) |
| ANOMALY_RATE | 0.04 | Fraction of readings with injected faults |
| POLL_INTERVAL | 30 | Anomaly detector poll interval (seconds) |

---

## Why this project matters for Siemens roles

Siemens Insights Hub engineering teams are hiring for exactly this skill stack:
time-series databases (InfluxDB is the standard in industrial IoT), observability
dashboards (Grafana is widely used alongside Insights Hub for on-premise deployments),
ML-based anomaly detection running at the edge, and REST API services connecting
ML outputs to operational tools.

The architecture mirrors how Siemens builds these systems internally — a data
generator layer, a time-series storage layer, an ML inference layer, and a
visualisation layer — making this project directly relevant to technical discussions
in interviews at Siemens Digital Industries, Siemens Energy, and Siemens Healthineers.

---

## Author

Vaibhav Mall
M.Sc. Digital Engineering, RWTH Aachen
Master Thesis Researcher at Fraunhofer IPT
[linkedin.com/in/mallvaibhav](https://linkedin.com/in/mallvaibhav) | [github.com/vaibhavmall010](https://github.com/vaibhavmall010)

---

## License

MIT
