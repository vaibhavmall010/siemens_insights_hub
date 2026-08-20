"""
tests/test_pipeline.py

Siemens Insights Hub — test suite.
All tests run without a live InfluxDB instance.
"""

import os
import sys
import json
import pytest
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ── Simulator tests ───────────────────────────────────────────────────────────

class TestSimulator:
    def test_generate_reading_returns_dict(self):
        from generator.simulator import generate_reading
        r = generate_reading("SM-01", np.random.default_rng(0))
        assert isinstance(r, dict)

    def test_all_sensor_fields_present(self):
        from generator.simulator import generate_reading, SENSOR_COLS
        r = generate_reading("SM-01", np.random.default_rng(0))
        for col in SENSOR_COLS:
            assert col in r, f"Missing: {col}"

    def test_all_machines_generate(self):
        from generator.simulator import generate_reading, MACHINES
        rng = np.random.default_rng(0)
        for mid in MACHINES:
            r = generate_reading(mid, rng)
            assert r["machine_id"] == mid

    def test_healthy_reading_no_fault(self):
        from generator.simulator import generate_reading
        r = generate_reading("SM-01", np.random.default_rng(0), fault_type=None)
        assert r["fault_type"] == "none"
        assert r["is_anomaly"] == 0

    def test_fault_reading_has_fault(self):
        from generator.simulator import generate_reading
        r = generate_reading("SM-01", np.random.default_rng(0), fault_type="bearing_fault")
        assert r["fault_type"] == "bearing_fault"
        assert r["is_anomaly"] == 1

    def test_bearing_fault_increases_vibration(self):
        from generator.simulator import generate_reading
        rng = np.random.default_rng(42)
        normal = generate_reading("SM-01", rng)
        rng2   = np.random.default_rng(42)
        faulty = generate_reading("SM-01", rng2, fault_type="bearing_fault")
        assert faulty["vibration_mm_s"] > normal["vibration_mm_s"]

    def test_thermal_runaway_increases_temperature(self):
        from generator.simulator import generate_reading
        rng = np.random.default_rng(7)
        normal = generate_reading("SM-02", rng)
        rng2   = np.random.default_rng(7)
        faulty = generate_reading("SM-02", rng2, fault_type="thermal_runaway")
        assert faulty["temperature_c"] > normal["temperature_c"]

    def test_pressure_drop_reduces_pressure(self):
        from generator.simulator import generate_reading
        rng = np.random.default_rng(3)
        normal = generate_reading("SM-04", rng)
        rng2   = np.random.default_rng(3)
        faulty = generate_reading("SM-04", rng2, fault_type="pressure_drop")
        assert faulty["pressure_bar"] < normal["pressure_bar"]

    def test_sensor_values_are_positive(self):
        from generator.simulator import generate_reading, MACHINES, FAULT_TYPES
        rng = np.random.default_rng(0)
        for mid in MACHINES:
            for fault in [None] + list(FAULT_TYPES):
                r = generate_reading(mid, rng, fault_type=fault)
                for k in ["vibration_mm_s", "temperature_c", "current_a",
                          "pressure_bar", "power_kw"]:
                    assert r[k] >= 0, f"{mid}/{fault}/{k} is negative"

    def test_line_protocol_format(self):
        from generator.simulator import generate_reading, to_line_protocol
        r    = generate_reading("SM-01", np.random.default_rng(0))
        line = to_line_protocol(r)
        assert line.startswith("factory_sensors,")
        assert "vibration_mm_s=" in line
        assert "temperature_c=" in line
        assert "machine_id=SM-01" in line

    def test_line_protocol_no_non_numeric_in_fields(self):
        from generator.simulator import generate_reading, to_line_protocol
        r    = generate_reading("SM-03", np.random.default_rng(5))
        line = to_line_protocol(r)
        parts = line.split(" ")
        assert len(parts) >= 2  # tags+measurement SPACE fields
        fields = parts[1]
        for f in fields.split(","):
            k, v = f.split("=")
            float(v)  # Must be numeric


# ── Anomaly engine tests ──────────────────────────────────────────────────────

class TestAnomalyEngine:
    def _make_X(self, n=100, anomaly_rows=5, seed=0):
        rng = np.random.default_rng(seed)
        X   = rng.normal(loc=[1.2, 60, 3000, 15, 6.5, 5.0], scale=[0.1]*6, size=(n, 6))
        for i in range(anomaly_rows):
            X[i, 0] *= 4.0
            X[i, 1] *= 1.5
        return X

    def test_isolation_forest_trains(self):
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
        X      = self._make_X()
        scaler = StandardScaler()
        X_s    = scaler.fit_transform(X)
        clf    = IsolationForest(n_estimators=50, contamination=0.05, random_state=0)
        clf.fit(X_s)
        preds = clf.predict(X_s)
        assert set(preds).issubset({-1, 1})

    def test_isolation_forest_detects_anomalies(self):
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
        X      = self._make_X(n=200, anomaly_rows=10)
        scaler = StandardScaler()
        X_s    = scaler.fit_transform(X)
        clf    = IsolationForest(n_estimators=100, contamination=0.05, random_state=0)
        clf.fit(X_s)
        preds = clf.predict(X_s[:10])
        assert (preds == -1).sum() > 0

    def test_anomaly_score_normalisation(self):
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
        X      = self._make_X()
        scaler = StandardScaler()
        X_s    = scaler.fit_transform(X)
        clf    = IsolationForest(n_estimators=50, contamination=0.05, random_state=0)
        clf.fit(X_s)
        raw    = clf.decision_function(X_s)
        norm   = 1 - (raw - raw.min()) / (raw.max() - raw.min())
        assert norm.min() >= 0.0
        assert norm.max() <= 1.0


# ── Line protocol tests ───────────────────────────────────────────────────────

class TestLineProtocol:
    def test_valid_line_protocol(self):
        from generator.simulator import generate_reading, to_line_protocol
        r    = generate_reading("SM-05", np.random.default_rng(0))
        line = to_line_protocol(r)
        assert "," in line
        assert " " in line

    def test_tags_in_line(self):
        from generator.simulator import generate_reading, to_line_protocol
        r    = generate_reading("SM-02", np.random.default_rng(0))
        line = to_line_protocol(r)
        assert "machine_id=SM-02" in line
        assert "fault_type=" in line

    def test_all_sensors_in_fields(self):
        from generator.simulator import generate_reading, to_line_protocol, SENSOR_COLS
        r    = generate_reading("SM-01", np.random.default_rng(0))
        line = to_line_protocol(r)
        fields_part = line.split(" ")[1]
        for col in SENSOR_COLS:
            assert f"{col}=" in fields_part


# ── Webhook API tests ─────────────────────────────────────────────────────────

class TestAlertWebhook:
    @pytest.fixture
    def client(self, tmp_path):
        os.environ["DB_PATH"] = str(tmp_path / "test_alerts.db")
        from fastapi.testclient import TestClient
        from webhook.alert_handler import app, init_db
        init_db()
        return TestClient(app)

    def test_health_endpoint(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_receive_alert(self, client):
        payload = {
            "title":   "High vibration on SM-01",
            "state":   "alerting",
            "message": "Vibration exceeded 3.5 mm/s",
            "alerts": [{
                "status":      "alerting",
                "labels":      {"machine_id": "SM-01", "severity": "critical"},
                "annotations": {"summary": "Bearing fault suspected"},
                "fingerprint": "abc123",
            }]
        }
        r = client.post("/alert", json=payload)
        assert r.status_code == 200
        assert r.json()["inserted"] == 1

    def test_list_alerts(self, client):
        client.post("/alert", json={
            "title": "Test alert", "state": "alerting",
            "alerts": [{"status": "alerting", "labels": {"machine_id": "SM-03"},
                        "annotations": {}, "fingerprint": "xyz"}]
        })
        r = client.get("/alerts")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_alert_summary(self, client):
        r = client.get("/alerts/summary")
        assert r.status_code == 200
        assert "total" in r.json()
        assert "firing" in r.json()

    def test_filter_by_machine(self, client):
        client.post("/alert", json={
            "title": "SM-02 alert", "state": "alerting",
            "alerts": [{"status": "alerting", "labels": {"machine_id": "SM-02"},
                        "annotations": {}, "fingerprint": "abc"}]
        })
        r = client.get("/alerts?machine_id=SM-02")
        assert r.status_code == 200
        for alert in r.json()["alerts"]:
            assert alert["machine_id"] == "SM-02"


# ── Integration smoke test ────────────────────────────────────────────────────

class TestIntegration:
    def test_full_reading_to_line_protocol_pipeline(self):
        from generator.simulator import generate_reading, to_line_protocol, MACHINES, FAULT_TYPES
        rng   = np.random.default_rng(99)
        lines = []
        for mid in MACHINES:
            fault = rng.choice([None, "bearing_fault", "overload"])
            r     = generate_reading(mid, rng, fault_type=fault)
            line  = to_line_protocol(r)
            lines.append(line)

        assert len(lines) == 5
        for line in lines:
            assert line.startswith("factory_sensors,")
            assert " " in line

    def test_detector_scores_structure(self):
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
        import numpy as np

        rng = np.random.default_rng(0)
        X   = rng.normal(loc=[1.2, 60, 3000, 15, 6.5, 5.0], scale=[0.2]*6, size=(60, 6))

        scaler = StandardScaler()
        X_s    = scaler.fit_transform(X)
        clf    = IsolationForest(n_estimators=50, contamination=0.04, random_state=0)
        clf.fit(X_s)

        raw     = clf.decision_function(X_s[-5:])
        preds   = clf.predict(X_s[-5:])
        norm    = 1 - (raw - raw.min()) / (raw.max() - raw.min() + 1e-9)

        result = {
            "machine_id":    "SM-01",
            "anomaly_score": float(norm.mean()),
            "is_anomaly":    int((preds == -1).any()),
            "confidence":    float(abs(raw.mean())),
        }
        assert 0 <= result["anomaly_score"] <= 1
        assert result["is_anomaly"] in [0, 1]
