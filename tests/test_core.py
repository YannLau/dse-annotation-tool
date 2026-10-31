import copy
import json
import shutil
import threading
import time
import numpy as np
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from dse_tool.schema import (
    default_schema,
    evaluate,
    validate_schema,
    resolve,
    head_spec,
)
from dse_tool.store import Store
from dse_tool.dataset import Dataset, content_fingerprint
from dse_tool.api import create_app
from dse_tool.adapter import DSEDataset, proprioception36, training_statistics
from dse_tool.assist import event_candidates, adviser_candidate


def test_three_value_gates():
    schema = default_schema()
    validate_schema(schema)
    condition = {"field": "self.phase", "op": "in", "values": [2]}
    assert evaluate(condition, {"left": {}}, "left", schema) is None
    assert evaluate(condition, {"left": {"phase": 8}}, "left", schema) is None
    assert (
        evaluate(
            {"field": "self.phase", "op": "eq", "value": 8},
            {"left": {"phase": 8}},
            "left",
            schema,
        )
        is True
    )
    assert evaluate({"all": [False, condition]}, {"left": {}}, "left", schema) is False
    assert evaluate({"any": [True, condition]}, {"left": {}}, "left", schema) is True
    raw = {
        "left": {
            "phase": {"value_id": 2, "review_status": "reviewed", "schema_version": 1}
        },
        "right": {"phase": {"value_id": 4}},
    }
    result = resolve(schema, raw)
    assert result["left"]["close_readiness"]["required"] is True
    assert result["left"]["release_readiness"]["applicability"] == "not_applicable"
    assert result["right"]["release_readiness"]["required"] is True
    assert result["right"]["close_readiness"]["applicability"] == "not_applicable"
    raw = {
        "left": {
            "holding": {"value_id": 2, "review_status": "reviewed", "schema_version": 1}
        }
    }
    assert resolve(schema, raw)["left"]["holding"]["effective_loss_mask"] == 1
    assert resolve(schema, raw, False)["left"]["holding"]["effective_loss_mask"] == 0


@pytest.mark.parametrize("corruption", ["duplicate", "reference", "cycle", "reuse"])
def test_schema_rejects(corruption):
    old = default_schema()
    s = copy.deepcopy(old)
    if corruption == "duplicate":
        s["fields"][0]["classes"][1]["id"] = 0
    if corruption == "reference":
        s["fields"][1]["applicable_when"] = {
            "field": "left.nope",
            "op": "eq",
            "value": 0,
        }
    if corruption == "cycle":
        s["fields"][0]["applicable_when"] = {
            "field": "self.holding",
            "op": "eq",
            "value": 0,
        }
    if corruption == "reuse":
        s["fields"][0]["classes"][0]["key"] = "reused"
    with pytest.raises(ValueError):
        validate_schema(s, old)


def test_annotations_history_and_invalidation(tmp_path):
    store = Store(tmp_path / "project")
    store.annotate("src", 0, 10, "left", "phase", 2, "state", True, True)
    store.annotate("src", 0, 10, "right", "phase", 4, "state", True, True)
    store.annotate("src", 0, 10, "right", "holding", 1, "state", True, True)
    store.annotate("src", 0, 10, "left", "holding", 2, "state", True, True)
    store.annotate("src", 0, 10, "left", "close_readiness", 2, "state", True, True)
    assert store.labels("src", 5)["left"]["close_readiness"]["effective_loss_mask"] == 1
    store.annotate("src", 5, 6, "left", "phase", 0, "state", False, False, True)
    at5 = store.labels("src", 5)
    assert at5["left"]["holding"]["value_id"] == 2
    assert at5["left"]["holding"]["review_status"] == "needs_review"
    assert at5["left"]["holding"]["applicability"] == "not_applicable"
    assert at5["right"]["phase"]["value_id"] == 4
    assert at5["right"]["holding"]["effective_loss_mask"] == 1
    assert store.labels("src", 4)["left"]["holding"]["review_status"] == "reviewed"
    assert store.labels("src", 6)["left"]["holding"]["review_status"] == "reviewed"
    assert store.undo()
    assert store.labels("src", 5)["left"]["holding"]["effective_loss_mask"] == 1
    assert store.undo(True)
    assert store.labels("src", 5)["left"]["holding"]["effective_loss_mask"] == 0
    reopened = Store(tmp_path / "project")
    assert reopened.labels("src", 5) == store.labels("src", 5)
    reopened.annotate("src", 5, 6, "left", "phase", 2, "state", True, True, True)
    assert (
        reopened.labels("src", 5)["left"]["holding"]["review_status"] == "needs_review"
    )


def test_deterministic_ranges_and_masks(tmp_path):
    s = Store(tmp_path / "p")
    s.annotate("x", 0, 20, "left", "phase", 2, "state")
    s.annotate("x", 4, 7, "left", "phase", 3, "state")
    s.annotate("x", 5, 6, "left", "phase", 8, "state", True, True, True)
    assert [s.labels("x", i)["left"]["phase"]["value_id"] for i in range(3, 8)] == [
        2,
        3,
        8,
        3,
        2,
    ]
    assert s.labels("x", 5)["left"]["phase"]["effective_loss_mask"] == 1
    assert (
        s.labels("x", 5)["left"]["close_readiness"]["applicability"] == "undetermined"
    )
    assert s.labels("x", 5, False)["left"]["phase"]["effective_loss_mask"] == 0
    with pytest.raises(ValueError):
        s.annotate("x", 0, 1, "left", "phase", 2, "behavior")
    with pytest.raises(ValueError):
        s.annotate("x", 0, 1, "left", "phase", 2, "state", True, False)
    s.annotate("x", 0, 1, "left", "holding", 1, "state")
    with pytest.raises(ValueError):
        s.annotate("x", 0, 1, "left", "holding", 1, "state", True, True)


def test_media_and_api(source, tmp_path):
    before, _ = content_fingerprint(source)
    app = create_app(tmp_path / "project")
    c = TestClient(app)
    r = c.post("/api/import", json={"path": str(source)})
    assert r.status_code == 200, r.text
    key = r.json()["fingerprint"]
    assert key == before
    ds = Dataset(source, tmp_path / "cache")
    assert ds.history(0, 0) == [None, None, None, 0]
    assert ds.history(0, 30) == [12, 18, 24, 30]
    w = c.get(f"/api/window/{key}/0/30?mode=state&request_id=17").json()
    assert w["request_id"] == 17
    assert min(r["timestamp"] for r in w["telemetry"]) >= 0.4 - 1e-8
    assert max(r["timestamp"] for r in w["telemetry"]) == 1
    assert not any("action" in r or any("cmd" in k for k in r) for r in w["telemetry"])
    assert not any(k.startswith("adviser") for k in w["labels"]["left"])
    assert c.get(f"/api/frame/{key}/0/0/30").status_code == 200
    assert c.get(f"/api/frame/{key}/0/9/30").status_code == 400
    w = c.get(f"/api/window/{key}/0/30?mode=behavior").json()
    assert "action" in w["telemetry"][0]
    assert "adviser.x" in w["labels"]["left"]
    assert (
        c.post(
            "/api/export", json={"dataset": key, "destination": str(source / "bad")}
        ).status_code
        == 400
    )
    assert (
        c.post("/api/import", json={"path": str(tmp_path / "project")}).status_code
        == 400
    )
    assert content_fingerprint(source)[0] == before
    (source / "meta/tasks.jsonl").write_text("{}")
    assert c.get(f"/api/window/{key}/0/1").status_code == 400


def test_export_portable_and_batch(source, tmp_path):
    before, _ = content_fingerprint(source)
    app = create_app(tmp_path / "p")
    client = TestClient(app)
    key = client.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    ds = app.state.datasets[key]
    store = app.state.store
    src, _, _ = ds.source_key(0, 0, key)
    store.annotate(src, 0, 60, "left", "phase", 8, "state", True, True)
    store.annotate(src, 0, 60, "left", "holding", 2, "state", True, True)
    store.annotate(src, 0, 60, "right", "phase", 0, "state", True, True)
    # Add dynamic head, publish, then explicitly re-review a fixture value.
    schema = copy.deepcopy(store.schema())
    field = copy.deepcopy(schema["fields"][1])
    field.update(field_id="visibility", applicable_when=True, required_when=False)
    schema["fields"].append(field)
    store.publish(schema)
    store.annotate(src, 0, 60, "left", "phase", 8, "state", True, True)
    store.annotate(src, 0, 60, "left", "holding", 2, "state", True, True)
    store.annotate(src, 0, 60, "right", "phase", 0, "state", True, True)
    store.annotate(src, 27, 28, "left", "phase", 1, "state", True, True, True)
    for a, b in [(25, 30), (45, 50)]:
        assert (
            client.post(
                "/api/selections",
                json={"dataset": key, "episode": 0, "start": a, "end": b},
            ).status_code
            == 200
        )
    dest = tmp_path / "export"
    r = client.post("/api/export", json={"dataset": key, "destination": str(dest)})
    assert r.status_code == 200, r.text
    jobid = r.json()["id"]
    app.state.exporter.threads[jobid].join(60)
    job = client.get("/api/jobs/" + jobid).json()
    assert job["status"] == "complete", job
    assert job["summary"]["center_count"] == 10
    exported = Dataset(dest, tmp_path / "out-cache")
    assert len(exported.episodes) == 2
    assert exported.path("data", 1).relative_to(dest).parts[1] == "chunk-001"
    sm = pq.read_table(dest / "annotations/source_map.parquet").to_pylist()
    for row in sm:
        old = ds.rows(0)[row["source_frame_index"]]
        new = exported.rows(row["episode_index"])[row["frame_index"]]
        for k in [
            "observation.state",
            "action",
            "observation.ee_pose_left",
            "observation.ee_pose_cmd_right",
        ]:
            assert old[k] == new[k]
    assert exported.info["features"]["observation.state"]["dtype"] == "float64"
    assert content_fingerprint(source)[0] == before
    moved = tmp_path / "moved"
    shutil.copytree(dest, moved)
    shutil.rmtree(dest)
    wrapper = DSEDataset(moved, tmp_path / "adapter-cache")
    batch = wrapper.batch([0, 1])
    assert batch["images"].shape == (2, 4, 3, 3, 256, 256)
    assert batch["loss_masks"]["left"]["holding"][0] == 1
    assert batch["labels"]["left"]["holding"][0] == 2
    assert np.all(batch["loss_masks"]["left"]["visibility"] == 0)
    assert np.all(batch["labels"]["left"]["visibility"] == -100)
    assert np.all(batch["loss_masks"]["right"]["holding"] == 0)
    assert "action" not in batch and "ee_pose_cmd" not in batch
    samples = pq.read_table(moved / "annotations/samples.parquet").to_pylist()
    labs = pq.read_table(moved / "annotations/labels.parquet").to_pylist()
    contexts = {
        (r["episode_index"], r["frame_index"])
        for r in samples
        if not r["center_is_selected"]
    }
    assert all(
        r["effective_loss_mask"] == 0
        for r in labs
        if (r["episode_index"], r["frame_index"]) in contexts
    )
    assert len({r["split_group"] for r in samples}) == 1
    app2 = create_app(tmp_path / "p2")
    c2 = TestClient(app2)
    opened = c2.post("/api/import", json={"path": str(moved)})
    assert opened.status_code == 200, opened.text
    k2 = opened.json()["fingerprint"]
    newds = app2.state.datasets[k2]
    for sample in samples:
        source_uuid, sf, _ = newds.source_key(
            sample["episode_index"], sample["frame_index"], k2
        )
        result = app2.state.store.labels(source_uuid, sf, sample["center_is_selected"])
        expected = [
            r
            for r in labs
            if r["episode_index"] == sample["episode_index"]
            and r["frame_index"] == sample["frame_index"]
        ]
        for r in expected:
            actual = result[r["arm"]][r["field_id"]]
            assert (actual["value_id"], actual["effective_loss_mask"]) == (
                r["value_id"],
                r["effective_loss_mask"],
            )
    with pytest.raises(ValueError):
        proprioception36(batch["state"], batch["ee_pose_feedback"], {})
    cfg = {
        "verified": True,
        "unit": "m",
        "frame": "robot_base",
        "transform": np.eye(4).tolist(),
    }
    assert proprioception36(batch["state"], batch["ee_pose_feedback"], cfg).shape == (
        2,
        4,
        36,
    )
    assert len(wrapper.sample_indices(5)) == 5
    stats = training_statistics(wrapper, {samples[0]["split_group"]})
    assert stats["count"] == 10
    assert any(
        h["field_id"] == "visibility"
        for h in json.loads((moved / "annotations/head_spec.json").read_text())[
            "categorical_heads"
        ]
    )


def test_export_cancel_no_publish(source, tmp_path):
    app = create_app(tmp_path / "p")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    ds = app.state.datasets[key]
    store = app.state.store
    store.select(key, 0, 0, 60)
    exporter = app.state.exporter
    flag = threading.Event()
    flag.set()
    job = {"id": "cancel-fixture", "status": "queued"}
    frozen = store.snapshot()
    dest = tmp_path / "cancelled"
    exporter.run(
        ds,
        key,
        dest,
        [{"episode": 0, "start": 0, "end": 60}],
        frozen,
        store.schema(),
        0.7,
        job,
        flag,
    )
    assert not dest.exists()
    assert not list(tmp_path.glob(".*.partial-*"))
    assert job["status"] == "cancelled"


def test_assist_requires_calibration():
    rows = [
        {
            "timestamp": i / 30,
            "observation.state": [0 if i < 15 else 1],
            "observation.ee_pose_left": [i * 0.002, 0, 0, 0, 0, 0, 1],
        }
        for i in range(30)
    ]
    with pytest.raises(ValueError):
        event_candidates(rows, "observation.state", 0, {})
    ev = event_candidates(
        rows,
        "observation.state",
        0,
        {
            "verified": True,
            "unit": "test",
            "closed_threshold": 0.2,
            "open_threshold": 0.8,
            "closed_below": True,
            "min_duration": 0.1,
        },
    )
    assert ev[0]["frame"] == 15 and ev[0]["status"] == "suggestion"
    with pytest.raises(ValueError):
        adviser_candidate(rows, 0, lambda i: True, lambda i: 2, set(), {})
    cfg = {
        "verified": True,
        "unit": "m",
        "frame": "robot_base",
        "transform": np.eye(4).tolist(),
        "feature": "observation.ee_pose_left",
    }
    r = adviser_candidate(rows, 0, lambda i: True, lambda i: 2, set(), cfg)
    assert r["values"]["x"] == 1
    assert (
        adviser_candidate(rows, 0, lambda i: i < 1, lambda i: 2, set(), cfg)["status"]
        == "missing"
    )
    assert (
        adviser_candidate(rows, 0, lambda i: True, lambda i: 2, {2}, cfg)["status"]
        == "missing"
    )


def test_parallel_requests_are_consistent(source, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    app = create_app(tmp_path / "concurrent")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]

    def request(i):
        with TestClient(app) as worker:
            if i % 3 == 0:
                return worker.post("/api/preferences", json={"test": i}).status_code
            if i % 3 == 1:
                return worker.get(f"/api/frame/{key}/0/{i % 3}/{i % 60}").status_code
            return worker.get(
                f"/api/window/{key}/0/{i % 60}?request_id={i}"
            ).status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert set(pool.map(request, range(48))) == {200}


def test_reject_invalid_media_and_versions(source, tmp_path):
    import pyarrow as pa

    info_path = source / "meta/info.json"
    info = json.loads(info_path.read_text())
    info["codebase_version"] = "v3.0"
    info_path.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="仅支持"):
        Dataset(source, tmp_path / "cache")
    info["codebase_version"] = "v2.1"
    info_path.write_text(json.dumps(info))
    ds = Dataset(source, tmp_path / "cache")
    ds.mapping(0, ds.cameras[0])
    data = ds.path("data", 0)
    rows = pq.read_table(data).to_pylist()
    for row in rows:
        row["timestamp"] += 2
    pq.write_table(pa.Table.from_pylist(rows), data)
    changed = Dataset(source, tmp_path / "cache")
    with pytest.raises(ValueError, match="无法可靠匹配"):
        changed.mapping(0, changed.cameras[0])
    changed.path("video", 0, changed.cameras[1]).unlink()
    with pytest.raises(ValueError, match="缺少视频"):
        changed.mapping(0, changed.cameras[1])


def test_causal_schema_cannot_gate_on_behavior():
    schema = default_schema()
    schema["fields"][1]["applicable_when"] = {
        "field": "right.adviser.x",
        "op": "eq",
        "value": 0,
    }
    with pytest.raises(ValueError, match="未来行为"):
        validate_schema(schema)


def test_failed_export_preserves_existing_target(source, tmp_path):
    app = create_app(tmp_path / "p")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    app.state.store.select(key, 0, 10, 20)
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("unchanged")
    response = c.post("/api/export", json={"dataset": key, "destination": str(target)})
    assert response.status_code == 400
    assert (target / "keep.txt").read_text() == "unchanged"


def test_reexport_keeps_ancestral_source_mapping(source, tmp_path):
    app = create_app(tmp_path / "p")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    app.state.store.select(key, 0, 25, 30)
    first = tmp_path / "first"
    job = c.post("/api/export", json={"dataset": key, "destination": str(first)}).json()
    app.state.exporter.threads[job["id"]].join(30)
    assert c.get("/api/jobs/" + job["id"]).json()["status"] == "complete"
    imported = c.post("/api/import", json={"path": str(first)})
    assert imported.status_code == 200, imported.text
    second_key = imported.json()["fingerprint"]
    second = tmp_path / "second"
    job = c.post(
        "/api/export", json={"dataset": second_key, "destination": str(second)}
    ).json()
    app.state.exporter.threads[job["id"]].join(30)
    assert c.get("/api/jobs/" + job["id"]).json()["status"] == "complete"
    original = Dataset(source, tmp_path / "cache").rows(0)
    a = pq.read_table(first / "annotations/source_map.parquet").to_pylist()
    b = pq.read_table(second / "annotations/source_map.parquet").to_pylist()
    for r in b:
        ancestor = next(
            x for x in a if x["source_frame_index"] == r["source_frame_index"]
        )
        assert r["source_timestamp"] == original[r["source_frame_index"]]["timestamp"]
        for key in [
            "source_episode_uuid",
            "source_frame_index",
            "source_index",
            "media_mapping",
        ]:
            assert r[key] == ancestor[key]
    # Integrity failure is rejected before a new source registration is committed.
    with (second / "annotations/labels.parquet").open("ab") as stream:
        stream.write(b"corrupted fixture")
    count = len(c.get("/api/project").json()["datasets"])
    response = c.post("/api/import", json={"path": str(second)})
    assert response.status_code == 400 and "校验和" in response.text
    assert len(c.get("/api/project").json()["datasets"]) == count


def test_explicit_cross_arm_gate_invalidation(tmp_path):
    s = Store(tmp_path / "cross")
    schema = copy.deepcopy(s.schema())
    custom = copy.deepcopy(schema["fields"][1])
    custom.update(
        field_id="cross_signal",
        applicable_when={"field": "left.phase", "op": "eq", "value": 2},
        required_when=False,
    )
    schema["fields"].append(custom)
    s.publish(schema)
    s.annotate("src", 0, 2, "left", "phase", 2, "state", True, True)
    s.annotate("src", 0, 2, "right", "cross_signal", 2, "state", True, True)
    assert s.labels("src", 0)["right"]["cross_signal"]["effective_loss_mask"] == 1
    s.annotate("src", 0, 1, "left", "phase", 1, "state")
    assert (
        s.labels("src", 0)["right"]["cross_signal"]["review_status"] == "needs_review"
    )
    assert s.labels("src", 1)["right"]["cross_signal"]["effective_loss_mask"] == 1


def test_import_and_schema_are_undo_boundaries(source, tmp_path):
    app = create_app(tmp_path / "p")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    store = app.state.store
    ds = app.state.datasets[key]
    uuid, _, _ = ds.source_key(0, 0, key)
    store.annotate(uuid, 0, 1, "left", "phase", 2, "state")
    assert c.post("/api/import", json={"path": str(source)}).status_code == 200
    assert store.undo() is False
    assert store.labels(uuid, 0)["left"]["phase"]["value_id"] == 2
    store.annotate(uuid, 0, 1, "left", "phase", 8, "state", point=True)
    assert store.undo() is True
    assert store.labels(uuid, 0)["left"]["phase"]["value_id"] == 2
    assert store.undo() is False
    assert store.undo(True) is True
    schema = copy.deepcopy(store.schema())
    schema["fields"][0]["description"] = "Updated UI description"
    store.publish(schema)
    assert store.undo() is False
    assert store.db.execute("SELECT COUNT(*) FROM history").fetchone()[0] >= 2
