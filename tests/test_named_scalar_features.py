import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from dse_tool.adapter import DSEDataset
from dse_tool.api import create_app
from dse_tool.dataset import Dataset, content_fingerprint


FLAG = "complementary_info.is_intervention"


def add_flag(source, values, *, state_dim=None):
    info_path = source / "meta/info.json"
    info = json.loads(info_path.read_text())
    info["features"][FLAG] = {
        "dtype": "float32",
        "shape": [1],
        "names": ["is_intervention"],
    }
    if state_dim is not None:
        for key in ("observation.state", "action"):
            info["features"][key].update(
                shape=[state_dim], names=[f"joint_{i}" for i in range(state_dim)]
            )
    info_path.write_text(json.dumps(info))
    path = source / "data/chunk-000/episode_000000.parquet"
    rows = pq.read_table(path).to_pylist()
    for row in rows:
        row[FLAG] = values[row["frame_index"] % len(values)]
        if state_dim is not None:
            for key in ("observation.state", "action"):
                row[key] = [float(row["frame_index"] + i) for i in range(state_dim)]
    pq.write_table(pa.Table.from_pylist(rows), path)
    return rows


@pytest.mark.parametrize("storage", ["scalar", "list"])
def test_31d_named_scalar_import_browse_export_reopen(source, tmp_path, storage):
    values = [0.0, 1.0] if storage == "scalar" else [[0.0], [1.0]]
    rows = add_flag(source, values, state_dim=31)
    before = content_fingerprint(source)[0]
    app = create_app(tmp_path / "project")
    with TestClient(app) as client:
        response = client.post("/api/import", json={"path": str(source)})
        assert response.status_code == 200, response.text
        key = response.json()["fingerprint"]
        assert key == before
        state = client.get(f"/api/window/{key}/0/30?mode=state").json()
        assert len(state["telemetry"][-1]["observation.state"]) == 31
        assert all(FLAG not in row for row in state["telemetry"])
        behavior = client.get(f"/api/window/{key}/0/30?mode=behavior").json()
        assert [r[FLAG] for r in behavior["telemetry"][:2]] == [[0.0], [1.0]]
        assert client.get(f"/api/frame/{key}/0/0/30").status_code == 200
        app.state.store.select(key, 0, 30, 33)
        destination = tmp_path / "export"
        response = client.post(
            "/api/export", json={"dataset": key, "destination": str(destination)}
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        app.state.exporter.threads[job_id].join(30)
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "complete", job
        exported = Dataset(destination, tmp_path / "export-cache")
        exported.validate()
        assert (
            exported.table(0)[FLAG].type
            == pq.read_table(source / "data/chunk-000/episode_000000.parquet")[
                FLAG
            ].type
        )
        for (episode, frame), mapping in exported.source_map.items():
            actual = exported.rows(episode)[frame]
            expected = rows[mapping["source_frame_index"]]
            assert actual[FLAG] == expected[FLAG]
            assert actual["observation.state"] == expected["observation.state"]
        reopened = client.post("/api/import", json={"path": str(destination)})
        assert reopened.status_code == 200, reopened.text
        batch = DSEDataset(destination, tmp_path / "adapter-cache").batch([0, 1])
        assert batch["state"].shape == (2, 4, 31)
    assert content_fingerprint(source)[0] == before
    app.state.store.db.close()


@pytest.mark.parametrize("value", [None, "zero", [0.0, 1.0], []])
def test_invalid_named_scalar_is_rejected(source, tmp_path, value):
    add_flag(source, [value])
    with pytest.raises(ValueError, match="数值特征维度错误: " + FLAG):
        Dataset(source, tmp_path / "cache").validate(decode=False)


def test_single_joint_state_still_requires_vector_storage(source, tmp_path):
    add_flag(source, [0.0], state_dim=1)
    path = source / "data/chunk-000/episode_000000.parquet"
    table = pq.read_table(path)
    field = table.schema.get_field_index("observation.state")
    table = table.set_column(
        field, "observation.state", pa.array([0.0] * table.num_rows)
    )
    pq.write_table(table, path)
    with pytest.raises(ValueError, match="数值特征维度错误: observation.state"):
        Dataset(source, tmp_path / "cache").validate(decode=False)
