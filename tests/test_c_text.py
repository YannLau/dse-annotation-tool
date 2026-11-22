import copy
import json

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from dse_tool.adapter import DSEDataset
from dse_tool.api import create_app
from dse_tool.dataset import Dataset, content_fingerprint, verify_manifest
from dse_tool.schema import digest


def test_export_c_text_with_schema_mapping(source, tmp_path):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    key = client.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    fields = []
    for field, scope, keys in (
        ("holding", "each_arm", ("holding", "not_holding", "unsure")),
        ("adviser.x", "each_arm", ("positive", "negative", "unsure")),
        ("scene", "global", ("visible", "occluded", "unsure")),
    ):
        fields.append(
            {
                "field_id": field,
                "type": "categorical",
                "scope": scope,
                "classes": [{"id": i, "key": k} for i, k in zip((7, 23, 91), keys)],
                "unknown_id": 91,
                "applicable_when": True,
                "required_when": False,
            }
        )
    fields[1]["applicable_when"] = {"field": "self.holding", "op": "eq", "value": 7}
    scheme = client.post(
        "/api/schemes", json={"name": "C-text", "schema": {"fields": fields}}
    ).json()
    client.headers["X-Annotation-Scheme"] = scheme["id"]
    store, exporter = app.state.schemes.get(scheme["id"])
    src = app.state.datasets[key].source_key(0, 0, key)[0]
    store.annotate(src, 18, 22, "left", "holding", 7, "state", True, True)
    store.annotate(src, 18, 21, "left", "adviser.x", 23, "behavior", True, True)
    store.annotate(src, 21, 22, "left", "adviser.x", 7, "behavior")
    store.annotate(src, 22, 24, "left", "holding", 91, "state", True, True)
    store.annotate(src, 20, 21, "right", "holding", 7, "state", True, True)
    store.annotate(src, 20, 21, "right", "adviser.x", 7, "behavior", True, True)
    store.annotate(src, 20, 22, "right", "holding", 23, "state", True, True)
    store.annotate(src, 20, 21, "global", "scene", 7, "state", True, True)
    store.select(key, 0, 20, 24)
    store.select(key, 0, 27, 28)

    template = client.get("/api/export/c-text-template").json()
    assert template["schema_hash"] == digest(store.schema())
    assert template["fields"]["holding"] == {
        "holding": "holding=holding",
        "not_holding": "holding=not_holding",
    }
    template["fields"]["holding"]["not_holding"] = ""
    template["fields"]["adviser.x"]["negative"] = "move backward"
    template["fields"]["scene"]["visible"] = "目标可见"
    dest = tmp_path / "export"
    body = {"dataset": key, "destination": str(dest), "expected_schema_version": 1}
    missing = copy.deepcopy(template)
    del missing["fields"]["adviser.x"]["negative"]
    for bad in (missing, {**template, "schema_hash": "0" * 64}):
        response = client.post(
            "/api/export", json={**body, "c_text": {"template": bad}}
        )
        assert response.status_code == 400, response.text
        assert "C-text" in response.json()["detail"]
        assert not exporter.threads and not dest.exists()

    def export(payload):
        response = client.post("/api/export", json=payload)
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        exporter.threads[job_id].join(30)
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "complete", job
        return job["summary"]

    summary = export({**body, "c_text": {"template": template}})
    verify_manifest(dest)
    assert content_fingerprint(source)[0] == key
    assert json.loads((dest / "annotations/template.json").read_text()) == template
    manifest = json.loads((dest / "annotations/manifest.json").read_text())
    assert manifest["c_text"]["template_hash"] == digest(template)
    assert manifest["c_text"]["label_source"] == "reviewed_annotations"

    dataset = Dataset(dest, tmp_path / "cache")
    assert dataset.info["features"]["c_text"]["dtype"] == "string"
    assert "c_text" not in dataset.stats
    assert all("c_text" not in ep["stats"] for ep in dataset.episode_stats)
    assert dataset.tasks == [{"task_index": 0, "task": "Synthetic test fixture"}]
    assert (
        DSEDataset(dest, tmp_path / "model-cache", size=16)[0]["task"]
        == dataset.tasks[0]["task"]
    )
    prompts = pq.read_table(dest / "annotations/prompts.parquet").to_pylist()
    keys = [(p["episode_index"], p["frame_index"]) for p in prompts]
    assert len(set(keys)) == len(keys) == dataset.info["total_frames"]
    for p in prompts:
        e, f = p["episode_index"], p["frame_index"]
        assert dataset.table(e)["c_text"].type == pa.string()
        assert dataset.rows(e)[f]["c_text"] == p["combined_prompt"]
        source_id, source_frame, _ = dataset.source_key(e, f, "")
        assert p["source_episode_uuid"] == source_id == src
        assert p["source_frame_index"] == source_frame
        assert p["template_version"] == "my-scheme-v1"
    by_source = {(p["episode_index"], p["source_frame_index"]): p for p in prompts}
    assert by_source[0, 20]["combined_prompt"] == (
        "Synthetic test fixture\nLeft arm: holding=holding; move backward. Global: 目标可见."
    )
    assert by_source[0, 21]["dse_suffix"] == "Left arm: holding=holding."
    assert by_source[0, 22]["fallback_reason"] == "no_renderable_labels"
    for frame in ((0, 18), (1, 20), (0, 24)):
        assert by_source[frame]["fallback_reason"] == "context_only"
        assert by_source[frame]["combined_prompt"] == "Synthetic test fixture"
    assert summary["c_text"]["with_suffix"] == 2

    # A second export renders fresh prompts from the base task and current mapping.
    imported = client.post(
        "/api/import", json={"path": str(dest), "restore_annotations": True}
    )
    assert imported.status_code == 200, imported.text
    body = {**body, "dataset": imported.json()["fingerprint"]}
    default_dest = tmp_path / "default-text"
    export({**body, "destination": str(default_dest), "c_text": {}})
    rows = pq.read_table(default_dest / "annotations/prompts.parquet").to_pylist()
    prompt = next(p for p in rows if p["source_frame_index"] == 20)
    assert prompt["combined_prompt"] == (
        "Synthetic test fixture\nLeft arm: holding=holding; adviser.x=negative. "
        "Right arm: holding=not_holding. Global: scene=visible."
    )
    assert prompt["template_version"] == "schema-c-text-v1"

    plain_dest = tmp_path / "plain"
    export({**body, "destination": str(plain_dest)})
    plain = Dataset(plain_dest, tmp_path / "plain-cache")
    assert "c_text" not in plain.info["features"]
    assert "c_text" not in plain.table(0).column_names
    assert not (plain_dest / "annotations/prompts.parquet").exists()
