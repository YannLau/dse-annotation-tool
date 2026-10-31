import copy
import shutil

import pytest
from fastapi.testclient import TestClient

from dse_tool.api import create_app
from dse_tool.dataset import content_fingerprint
from dse_tool.adapter import DSEDataset
from dse_tool.history import HistoryConfig
from dse_tool.inspector import ExportInspector


@pytest.fixture
def exported(source, tmp_path):
    app = create_app(tmp_path / "annotation-work")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    ds = app.state.datasets[key]
    store = app.state.store
    src, _, _ = ds.source_key(0, 0, key)
    for a, b in [(20, 24), (30, 34)]:
        store.select(key, 0, a, b)
        store.annotate(
            src,
            a,
            b,
            "left",
            "phase",
            8,
            "state",
            True,
            True,
            label_source="test_fixture",
        )
        store.annotate(
            src,
            a,
            b,
            "left",
            "holding",
            2,
            "state",
            True,
            True,
            label_source="test_fixture",
        )
        store.annotate(
            src,
            a,
            b,
            "right",
            "phase",
            0,
            "state",
            True,
            True,
            label_source="test_fixture",
        )
    dest = tmp_path / "overlap-export"
    job = app.state.exporter.start(ds, key, dest)
    app.state.exporter.threads[job["id"]].join(60)
    assert job["status"] == "complete", job
    return dest


def test_inspector_reads_real_values_and_overlap_roles(exported, tmp_path):
    reader = ExportInspector(exported, tmp_path / "inspect-cache")
    overview = reader.summary
    assert overview["checksum_verified"]
    assert len(overview["episodes"]) == 2
    assert overview["center_rows"] == 8
    assert overview["duplicate_extra_rows"] == overview["repeated_source_frames"] == 19
    assert overview["total_rows"] == 58
    assert overview["unique_source_frames"] == 39
    overlap = next(r for r in overview["overlaps"] if r["start"] == 20)
    assert (overlap["start"], overlap["end"]) == (20, 24)
    assert [c["role"] for c in overlap["copies"]] == ["center", "history"]
    assert len({c["split_group"] for c in overlap["copies"]}) == 1
    center = reader.frame(0, 18)  # original source frame20
    prefix = reader.frame(1, 8)  # same source frame20, second episode prefix
    assert center["data"]["observation.state"] == prefix["data"]["observation.state"]
    assert center["data"]["action"] == prefix["data"]["action"]
    assert (
        center["source_map"]["source_frame_index"]
        == prefix["source_map"]["source_frame_index"]
        == 20
    )
    assert (
        center["summary"]["role"] == "center" and prefix["summary"]["role"] == "history"
    )
    assert [h["sample"]["source_frame"] for h in center["history"]] == [2, 8, 14, 20]

    def labels(value):
        return next(
            record
            for record in value["labels"]
            if record["raw"]["arm"] == "left" and record["raw"]["field_id"] == "phase"
        )

    assert labels(center)["raw"]["value_id"] == labels(prefix)["raw"]["value_id"] == 8
    assert labels(center)["raw"]["effective_loss_mask"] == 1
    assert labels(prefix)["raw"]["effective_loss_mask"] == 0
    assert labels(center)["class_name"] == "unknown"
    assert center["training"]["labels"]["left"]["phase"] == 8
    assert prefix["training"]["is_sample"] is False
    assert len(center["equivalents"]) == 2
    assert center["sample"]["is_history_context"] is True  # Multiple roles remain raw.
    missing = reader.frame(1, 0)  # unannotated original frame12, still real observation
    assert missing["data"]["observation.state"][0] == 12
    assert missing["labels"][0]["raw"]["value_id"] is None
    assert missing["labels"][0]["training_target"] == -100
    assert all(
        record["raw"]["effective_loss_mask"] == 0 for record in missing["labels"]
    )
    label_file = next(
        f for f in overview["files"] if f["path"] == "annotations/labels.parquet"
    )
    assert any(c["name"] == "value_id" and c["nullable"] for c in label_file["columns"])
    assert reader.episode(0, 1, 2)["rows"][0]["frame"] == 1
    training = DSEDataset(exported, tmp_path / "training-cache", size=16)[0]
    assert center["training"]["labels"] == training["labels"]
    assert center["training"]["loss_masks"] == training["loss_masks"]
    assert [h["frame"] for h in center["history"]] == training[
        "history_frame_indices"
    ].tolist()
    with pytest.raises(ValueError):
        reader.frame(0, 500)


def test_inspection_api_is_read_only_portable_and_decodes_own_media(exported, tmp_path):
    moved = tmp_path / "moved"
    shutil.move(exported, moved)
    before = content_fingerprint(moved)[0]
    app = create_app(tmp_path / "inspector-work")
    c = TestClient(app)
    snapshot = copy.deepcopy(app.state.store.snapshot())
    version = app.state.store.get("revision")
    schema = app.state.store.schema()
    result = c.post("/api/inspect/open", json={"path": str(moved)})
    assert result.status_code == 200, result.text
    key = result.json()["id"]
    frame = c.get(f"/api/inspect/{key}/frame/1/8").json()
    assert len(frame["comparisons"]) == 2
    raw_file = c.get(
        f"/api/inspect/{key}/file",
        params={"path": "annotations/samples.parquet", "offset": 1, "limit": 2},
    ).json()
    assert raw_file["rows"][0]["frame_index"] == 1 and len(raw_file["rows"]) == 2
    assert (
        c.get(
            f"/api/inspect/{key}/file", params={"path": "annotations/schema.json"}
        ).json()["value"]["version"]
        == 1
    )
    assert (
        c.get(f"/api/inspect/{key}/file", params={"path": "meta/tasks.jsonl"}).json()[
            "rows"
        ][0]["task"]
        == "Synthetic test fixture"
    )
    assert (
        c.get(
            f"/api/inspect/{key}/file", params={"path": "../project.sqlite3"}
        ).status_code
        == 400
    )
    assert (
        c.get(
            f"/api/inspect/{key}/file",
            params={"path": "annotations/intervals.parquet", "offset": 10000},
        ).json()["rows"]
        == []
    )
    assert frame["source_map"]["source_frame_index"] == 20
    assert frame["summary"]["copies"] == 2
    picture = c.get(f"/api/inspect/{key}/image/1/0/8")
    assert (
        picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg"
    )
    assert picture.content[:2] == b"\xff\xd8"
    assert c.get(f"/api/inspect/{key}/image/1/99/8").status_code == 400
    assert c.get(f"/api/inspect/{key}/image/1/0/999").status_code == 400
    assert (
        c.get(f"/api/inspect/{key}/episode/0?offset=2&limit=3").json()["rows"][0][
            "frame"
        ]
        == 2
    )
    assert c.get(f"/api/inspect/{key}/episode/0?limit=999").status_code == 422
    assert c.get(f"/api/inspect/{key}/episode/99").status_code == 400
    assert c.get("/api/inspect/not-a-handle/frame/0/0").status_code == 400
    assert app.state.store.snapshot() == snapshot
    assert app.state.store.get("revision") == version
    assert app.state.store.schema() == schema
    assert (
        app.state.store.db.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0
    )
    assert content_fingerprint(moved)[0] == before
    manifest = moved / "annotations/manifest.json"
    manifest.write_text(manifest.read_text() + "\n")
    assert c.get(f"/api/inspect/{key}/frame/0/0").status_code == 400
    assert (
        c.post(
            "/api/inspect/open", json={"path": str(tmp_path / "missing")}
        ).status_code
        == 400
    )


def test_inspector_rejects_tampered_export(exported, tmp_path):
    file = exported / "annotations/schema.json"
    file.write_text(file.read_text() + "\n")
    with pytest.raises(ValueError, match="校验和"):
        ExportInspector(exported, tmp_path / "cache")


def test_inspector_displays_real_episode_start_padding(source, tmp_path):
    app = create_app(tmp_path / "early-work")
    client = TestClient(app)
    key = client.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    app.state.store.select(key, 0, 0, 2)
    dest = tmp_path / "early-export"
    job = app.state.exporter.start(
        app.state.datasets[key], key, dest, history_config=HistoryConfig(5, 3)
    )
    app.state.exporter.threads[job["id"]].join(60)
    assert job["status"] == "complete"
    reader = ExportInspector(dest, tmp_path / "early-cache")
    value = reader.frame(0, 0)
    assert value["training"]["is_sample"]
    assert [h["frame"] for h in value["history"]] == [None, None, 0]
    assert value["history"][0]["image_valid_mask"] == [False, False, False]
    assert value["history"][0]["sample"] is None
    assert value["history"][2]["state_valid_mask"] is True
