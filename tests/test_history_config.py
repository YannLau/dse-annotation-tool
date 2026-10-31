import json
import shutil

import numpy as np
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from dse_tool.adapter import DSEDataset, proprioception36
from dse_tool.api import create_app
from dse_tool.dataset import Dataset, content_fingerprint
from dse_tool.history import HistoryConfig, sample_history


def test_variable_history_causal_sampling_and_validation():
    config = HistoryConfig(5, 3)
    times = [0, 0.4, 1.0, 2.4, 3.1, 4.9, 5.0, 5.2]
    sampled = sample_history(times, 6, config)
    assert [r["frame"] for r in sampled] == [0, 3, 6]
    assert [r["offset_seconds"] for r in sampled] == [5, 2.5, 0]
    assert all(r["timestamp"] <= r["target_timestamp"] + 1e-8 for r in sampled)
    assert sample_history(times, 0, config)[0]["frame"] is None
    assert [r["frame"] for r in sample_history(times, 0, HistoryConfig(0, 1))] == [0]
    assert HistoryConfig.from_dict(config.to_dict()) == config
    for horizon, count in [
        (-1, 3),
        (float("nan"), 3),
        (float("inf"), 3),
        (5, 0),
        (5, 3.5),
        (5, True),
        (5, 33),
        (0, 3),
        (5, 1),
    ]:
        with pytest.raises(ValueError):
            HistoryConfig(horizon, count)
    # Distinct targets may legitimately share a real row; retain actual timing.
    duplicates = sample_history([0, 2, 5], 2, HistoryConfig(1, 4))
    assert [r["frame"] for r in duplicates] == [1, 1, 1, 2]


def test_window_variable_history_and_evidence_provenance(source, tmp_path):
    app = create_app(tmp_path / "work")
    with TestClient(app) as c:
        key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
        data = c.get(
            f"/api/window/{key}/0/45?history_horizon=1&history_frames=3"
        ).json()
        assert [r["frame"] for r in data["history"]] == [15, 30, 45]
        assert data["history_config"]["num_frames"] == 3
        assert data["telemetry"][0]["frame"] == 15
        assert all(r["frame"] <= 45 and "action" not in r for r in data["telemetry"])
        assert data["future"] == []
        long = c.get(
            f"/api/window/{key}/0/45?history_horizon=5&history_frames=3"
        ).json()
        assert [r["frame"] for r in long["history"]] == [None, None, 45]
        single = c.get(
            f"/api/window/{key}/0/45?history_horizon=0&history_frames=1"
        ).json()
        assert [r["frame"] for r in single["history"]] == [45]
        assert len(single["telemetry"]) == 1
        for query in [
            "history_horizon=-1",
            "history_frames=33",
            "history_frames=2.5",
            "history_horizon=nan",
        ]:
            assert c.get(f"/api/window/{key}/0/45?{query}").status_code in [400, 422]
        assert c.get(f"/api/window/{key}/0/45?history_frames=1").status_code == 400
        request = dict(
            dataset=key,
            episode=0,
            start=45,
            end=46,
            arm="left",
            field="phase",
            value=8,
            mode="state",
            reviewed=True,
            confirmed=True,
            history_config={"horizon_seconds": 1, "num_frames": 3},
        )
        assert c.post("/api/annotations", json=request).status_code == 200
        source_id, sf, _ = app.state.datasets[key].source_key(0, 45, key)
        assert (
            app.state.store.raw(source_id, sf)["left"]["phase"]["provenance"][
                "history_config"
            ]["horizon_seconds"]
            == 1
        )
        request["arm"] = "right"
        assert (
            c.post(
                "/api/annotations/batch",
                json={"changes": [request], "expected_schema_version": 1},
            ).status_code
            == 200
        )
        assert (
            app.state.store.raw(source_id, sf)["right"]["phase"]["provenance"][
                "history_config"
            ]["num_frames"]
            == 3
        )
        pref = {"historyConfig": request["history_config"], "historyCapacity": 5}
        assert c.post("/api/preferences", json=pref).status_code == 200
    reopened = TestClient(create_app(tmp_path / "work"))
    assert reopened.get("/api/project").json()["preferences"] == pref


def test_export_history_capacity_portability_and_training_shapes(source, tmp_path):
    before = content_fingerprint(source)[0]
    app = create_app(tmp_path / "work")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    c.post("/api/selections", json=dict(dataset=key, episode=0, start=45, end=48))
    config = dict(horizon_seconds=1, num_frames=3)
    dest = tmp_path / "export"
    bad = c.post(
        "/api/export",
        json=dict(
            dataset=key,
            destination=str(dest),
            history_config=config,
            history_context_seconds=0.6,
        ),
    )
    assert bad.status_code == 400 and not dest.exists()
    result = c.post(
        "/api/export",
        json=dict(
            dataset=key,
            destination=str(dest),
            history_config=config,
            history_context_seconds=5,
        ),
    )
    assert result.status_code == 200, result.text
    job_id = result.json()["id"]
    app.state.exporter.threads[job_id].join(60)
    assert c.get("/api/jobs/" + job_id).json()["status"] == "complete"
    exported_key = c.post("/api/import", json={"path": str(dest)}).json()["fingerprint"]
    exported_info = c.get("/api/datasets/" + exported_key).json()
    assert exported_info["history_sampling"]["num_frames"] == 3
    assert exported_info["history_context_seconds"] == 5
    manifest = json.loads((dest / "annotations/manifest.json").read_text())
    assert manifest["context_seconds"]["history"] == 5
    assert manifest["history_sampling"]["num_frames"] == 3
    moved = tmp_path / "moved"
    shutil.move(dest, moved)
    reader = DSEDataset(moved, tmp_path / "cache", size=16)
    batch = reader.batch([0, 1])
    assert batch["images"].shape == (2, 3, 3, 3, 16, 16)
    assert batch["state"].shape == (2, 3, 18)
    assert batch["ee_pose_feedback"].shape == (2, 3, 2, 7)
    assert batch["image_valid_mask"].all() and batch["ee_pose_valid_mask"].all()
    np.testing.assert_allclose(batch["history_target_offsets"][0], [-1, -0.5, 0])
    np.testing.assert_allclose(batch["history_actual_offsets"][0], [-1, -0.5, 0])
    original = Dataset(source, tmp_path / "original-cache")
    frames = original.history(0, 45, config)
    np.testing.assert_allclose(
        batch["state"][0], [original.rows(0)[f]["observation.state"] for f in frames]
    )
    assert "action" not in batch and "future" not in batch
    transformed = proprioception36(
        batch["state"],
        batch["ee_pose_feedback"],
        dict(verified=True, unit="m", frame="robot_base", transform=np.eye(4).tolist()),
    )
    assert transformed.shape == (2, 3, 36)
    long = DSEDataset(
        moved, tmp_path / "long-cache", size=16, history_config=HistoryConfig(5, 3)
    ).batch([0])
    assert long["state_valid_mask"].tolist() == [[False, False, True]]
    assert not long["images"][0, :2].any()
    assert np.isnan(long["history_actual_offsets"][0, :2]).all()
    single = DSEDataset(
        moved,
        tmp_path / "single-cache",
        size=16,
        history_config={"horizon_seconds": 0, "num_frames": 1},
    ).batch([0])
    assert single["images"].shape == (1, 1, 3, 3, 16, 16)
    assert single["state_valid_mask"].all()
    with pytest.raises(ValueError, match="重新导出"):
        DSEDataset(
            moved,
            tmp_path / "too-long-cache",
            history_config={"horizon_seconds": 6, "num_frames": 3},
        )
    samples = pq.read_table(moved / "annotations/samples.parquet").to_pylist()
    assert any(r["is_history_context"] for r in samples if not r["center_is_selected"])
    labels = pq.read_table(moved / "annotations/labels.parquet").to_pylist()
    contexts = {
        (r["episode_index"], r["frame_index"])
        for r in samples
        if not r["center_is_selected"]
    }
    assert all(
        r["effective_loss_mask"] == 0
        for r in labels
        if (r["episode_index"], r["frame_index"]) in contexts
    )
    assert content_fingerprint(source)[0] == before
    # Older manifests without a sampling config retain the original 4-step API.
    manifest.pop("history_sampling")
    manifest["context_seconds"]["history"] = 0.6
    (moved / "annotations/manifest.json").write_text(json.dumps(manifest))
    old = DSEDataset(moved, tmp_path / "old-cache", size=16)
    assert old[0]["images"].shape == (4, 3, 3, 16, 16)
    with pytest.raises(ValueError, match="仅保留0.6秒"):
        DSEDataset(moved, tmp_path / "old-long", history_config=HistoryConfig(5, 3))
    k2 = c.post("/api/import", json={"path": str(moved)}).json()["fingerprint"]
    assert (
        c.get(f"/api/window/{k2}/0/45?history_horizon=5&history_frames=3").status_code
        == 400
    )
    assert (
        c.post(
            "/api/export",
            json=dict(
                dataset=k2,
                destination=str(tmp_path / "invented"),
                history_context_seconds=5,
            ),
        ).status_code
        == 400
    )
