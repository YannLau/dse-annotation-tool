import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from dse_tool.api import create_app


def prepare(source, scalar=True):
    path = source / "meta/info.json"
    info = json.loads(path.read_text())
    info["features"]["observation.state"]["names"] = {
        "motors": info["features"]["observation.state"]["names"]
    }
    feature = "observation.gripper_left"
    info["features"][feature] = {
        "dtype": "float32",
        "shape": [1],
        "names": ["left_gripper_sensor"],
    }
    path.write_text(json.dumps(info))
    path = source / "data/chunk-000/episode_000000.parquet"
    rows = pq.read_table(path).to_pylist()
    for row in rows:
        value = float(row["frame_index"] >= 30)
        row[feature] = value if scalar else [value]
    pq.write_table(pa.Table.from_pylist(rows), path)
    return feature


def context(client, source, feature):
    response = client.post("/api/import", json={"path": str(source)})
    assert response.status_code == 200, response.text
    key = response.json()["fingerprint"]
    return {
        "dataset": key,
        "episode": 0,
        "arm": "left",
        "feature": feature,
        "dimension": 0,
        "role": "feedback",
        "calibration": {
            "verified": True,
            "unit": "normalized",
            "closed_threshold": 0.2,
            "open_threshold": 0.8,
            "closed_below": True,
            "min_duration": 0.1,
            "binding": {
                "dataset": key,
                "arm": "left",
                "feature": feature,
                "dimension": 0,
            },
        },
    }


@pytest.mark.parametrize("scalar", [True, False])
def test_event_preview_dedupe_reject_delete_and_undo(source, tmp_path, scalar):
    feature = prepare(source, scalar)
    app = create_app(tmp_path / "work")
    with TestClient(app) as client:
        body = context(client, source, feature)
        channels = client.get("/api/event-channels/" + body["dataset"]).json()[
            "channels"
        ]
        assert any(
            c["feature"] == "observation.state" and c["dimension"] == 7
            for c in channels
        )
        assert any(
            c["feature"] == feature and c["arm_hint"] == "left" for c in channels
        )
        store = app.state.store
        revision = store.get("revision")
        preview = client.post(
            "/api/events/preview", json={**body, "start": 20, "end": 40}
        ).json()
        assert [c["frame"] for c in preview["candidates"]] == [30]
        assert store.get("revision") == revision
        assert not store.db.execute("SELECT 1 FROM events").fetchone()
        accepted = client.post(
            "/api/events/apply",
            json={**body, "start": 20, "end": 40, "plan_id": preview["plan_id"]},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["saved"] == 1
        event = dict(store.db.execute("SELECT * FROM events").fetchone())
        assert json.loads(event["details"])["calibration"]["unit"] == "normalized"
        assert (
            client.post("/api/events/preview", json=body).json()["summary"]["savable"]
            == 0
        )
        identity = {"dataset": body["dataset"], "episode": 0, "id": event["id"]}
        assert client.post("/api/events/reject", json=identity).status_code == 200
        assert (
            client.post("/api/events/preview", json=body).json()["summary"][
                "duplicates"
            ]
            == 1
        )
        assert client.post("/api/events/delete", json=identity).json()["deleted"] == 1
        assert not store.db.execute("SELECT 1 FROM events").fetchone()
        assert store.undo()
        assert store.db.execute("SELECT status FROM events").fetchone()[0] == "rejected"
        assert store.undo(True)
        assert not store.db.execute("SELECT 1 FROM events").fetchone()
        assert client.post("/api/events/delete", json=identity).status_code == 400


def test_stale_event_plan_wrong_arm_and_binding(source, tmp_path):
    feature = prepare(source)
    app = create_app(tmp_path / "work")
    with TestClient(app) as client:
        body = context(client, source, feature)
        for bad in (
            {**body, "arm": "right"},
            {**body, "calibration": {**body["calibration"], "binding": {}}},
        ):
            assert client.post("/api/events/preview", json=bad).status_code == 400
        preview = client.post("/api/events/preview", json=body).json()
        client.post(
            "/api/reviews",
            json={
                "dataset": body["dataset"],
                "episode": 0,
                "start": 0,
                "end": 45,
                "status": "approved",
            },
        )
        assert (
            client.post(
                "/api/events/apply", json={**body, "plan_id": preview["plan_id"]}
            ).status_code
            == 400
        )
        assert not app.state.store.db.execute("SELECT 1 FROM events").fetchone()


def test_protocol_export_restore_and_checked_manifest(source, tmp_path):
    feature = prepare(source)
    app = create_app(tmp_path / "work")
    with TestClient(app) as client:
        body = context(client, source, feature)
        key = body["dataset"]
        profile = {
            "event": body["calibration"],
            "coordinate": {"verified": True, "unit": "m"},
        }
        pref = {
            "behaviorSettings": {
                "profiles": {key + ":left": profile},
                "datasets": {
                    key: {
                        "taskSettings": {"target": "handle", "criteria": "Visible grip"}
                    }
                },
            }
        }
        client.post("/api/preferences", json=pref)
        app.state.store.select(key, 0, 28, 34)
        destination = tmp_path / "export"
        result = client.post(
            "/api/export", json={"dataset": key, "destination": str(destination)}
        ).json()
        app.state.exporter.threads[result["id"]].join(20)
        job = client.get("/api/jobs/" + result["id"]).json()
        assert job["status"] == "complete", job
        protocol_path = destination / "annotations/behavior_protocol.json"
        protocol = json.loads(protocol_path.read_text())
        manifest = json.loads((destination / "annotations/manifest.json").read_text())
        assert protocol["task_settings"]["target"] == "handle"
        assert (
            manifest["behavior_protocol"]["sha256"]
            == hashlib.sha256(protocol_path.read_bytes()).hexdigest()
        )
        app2 = create_app(tmp_path / "restored")
        with TestClient(app2) as reopened:
            response = reopened.post(
                "/api/import",
                json={"path": str(destination), "restore_annotations": True},
            )
            assert response.status_code == 200, response.text
            newkey = response.json()["fingerprint"]
            restored = reopened.get("/api/project").json()["preferences"][
                "behaviorSettings"
            ]
            assert restored["datasets"][newkey]["taskSettings"]["target"] == "handle"
            assert restored["profiles"][newkey + ":left"]["event"]["verified"] is False
            assert (
                restored["profiles"][newkey + ":left"]["coordinate"]["verified"]
                is False
            )


def test_historical_custom_event_can_be_confirmed_and_moved(source, tmp_path):
    feature = prepare(source)
    app = create_app(tmp_path / "work")
    with TestClient(app) as client:
        body = context(client, source, feature)
        source_id = app.state.datasets[body["dataset"]].source_key(
            0, 0, body["dataset"]
        )[0]
        store = app.state.store
        with store.transaction("Historical event fixture"):
            cursor = store.db.execute(
                "INSERT INTO events(source,frame,arm,type,status,details) VALUES(?,?,?,?,?,?)",
                (source_id, 8, "left", "grasp_start", "suggestion", "{}"),
            )
            event_id = cursor.lastrowid
        update = {
            "dataset": body["dataset"],
            "episode": 0,
            "id": event_id,
            "frame": 12,
            "arm": "left",
            "type": "grasp_start",
            "status": "confirmed",
        }
        response = client.post("/api/events", json=update)
        assert response.status_code == 200, response.text
        record = store.db.execute(
            "SELECT * FROM events WHERE id=?", (event_id,)
        ).fetchone()
        assert (record["type"], record["frame"], record["status"]) == (
            "grasp_start",
            12,
            "confirmed",
        )
        assert (
            client.post("/api/events", json={**update, "id": None}).status_code == 400
        )
