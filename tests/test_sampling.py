import json
import threading

import numpy as np
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from dse_tool.api import create_app
from dse_tool.dataset import Dataset, content_fingerprint, verify_manifest
from dse_tool.sampling import (
    default_key_classes,
    sample_pool_group,
    sampling_metadata,
    sampling_policy,
)
from dse_tool.schema import default_schema, digest
from dse_tool.schemes import Schemes


def generic_schema():
    return {
        "version": 1,
        "semantic_profile": "generic",
        "fields": [
            {
                "field_id": name,
                "type": "categorical",
                "scope": scope,
                "classes": [{"id": v, "key": str(v)} for v in (7, 23, 91)],
                "unknown_id": 91,
                "applicable_when": True,
                "required_when": True,
                "evidence": "state",
            }
            for name, scope in (("contact.mode", "each_arm"), ("quality", "global"))
        ],
    }


def test_defaults_and_custom_selection_override_legacy_phase():
    schema = default_schema()
    policy = sampling_policy(schema)
    assert policy["key_classes"] == default_key_classes(schema)
    assert policy["grouping"] == "legacy_phase"
    for left in range(9):
        for right in range(9):
            labels = {
                "left": {"phase": {"value_id": left}},
                "right": {"phase": {"value_id": right}},
            }
            pool, group = sample_pool_group(policy, "src", 0, labels, [], 30)
            assert (pool == "key") == (left in (2, 3, 5, 7) or right in (2, 3, 5, 7))
            assert group == f"src:phase:{[left, right]}"
    override = sampling_policy(
        schema, [{"arm": "left", "field_id": "phase", "class_ids": [0]}]
    )
    labels = {"left": {"phase": {"value_id": 2}}, "right": {"phase": {"value_id": 5}}}
    assert sample_pool_group(override, "src", 0, labels, [], 30)[0] == "background"
    generic = {**schema, "semantic_profile": "generic"}
    assert default_key_classes(generic) == []
    assert (
        sample_pool_group(sampling_policy(generic), "src", 0, labels, [], 30)[0]
        == "background"
    )


def test_policy_canonicalization_and_event_only_sampling():
    a = {"arm": "left", "field_id": "contact.mode", "class_ids": [23, 7, 7]}
    b = {"arm": "global", "field_id": "quality", "class_ids": [7]}
    s = generic_schema()
    assert sampling_policy(s, [a, b]) == sampling_policy(s, [b, a, a])
    metadata = sampling_metadata(sampling_policy(s, [a, b]))
    assert metadata["policy_hash"] == digest(metadata["policy"])
    labels = {"left": {}, "right": {}, "global": {}}
    policy = sampling_policy(s, [])
    assert sample_pool_group(policy, "src", 32, labels, [40], 30) == (
        "key",
        "src:event:40",
    )
    assert sample_pool_group(policy, "src", 31, labels, [40], 30)[0] == "background"
    assert (
        sample_pool_group(policy, "src", 40, labels, [42, 38], 30)[1] == "src:event:38"
    )
    assert sample_pool_group(policy, "src", 40, labels, [], 30)[0] == "background"


@pytest.mark.parametrize(
    "rules",
    [
        {},
        [None],
        [{"arm": "left", "field_id": "missing", "class_ids": [7]}],
        [{"arm": "left", "field_id": "quality", "class_ids": [7]}],
        [{"arm": "global", "field_id": "contact.mode", "class_ids": [7]}],
        *[
            [{"arm": "left", "field_id": "contact.mode", "class_ids": ids}]
            for ids in ([], [True], ["7"], [7.0], [99], None)
        ],
        [{"arm": "left", "field_id": "contact.mode", "class_ids": [7], "typo": 0}],
    ],
)
def test_invalid_key_selections(rules):
    with pytest.raises(ValueError):
        sampling_policy(generic_schema(), rules)


def test_custom_classes_api_export_snapshot_and_weights(source, tmp_path, monkeypatch):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    key = client.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    info = client.post(
        "/api/schemes", json={"name": "Custom keys", "schema": generic_schema()}
    ).json()
    headers = {"X-Annotation-Scheme": info["id"]}
    store, exporter = app.state.schemes.get(info["id"])
    ds = app.state.datasets[key]
    src = ds.source_key(0, 0, key)[0]
    for arm, field in (
        ("left", "contact.mode"),
        ("right", "contact.mode"),
        ("global", "quality"),
    ):
        store.annotate(src, 0, 59, arm, field, 91, "state", True, True)
    for arm, field, start, end, value in (
        ("global", "quality", 0, 8, 7),
        ("left", "contact.mode", 10, 16, 7),
        ("right", "contact.mode", 20, 24, 23),
    ):
        store.annotate(src, start, end, arm, field, value, "state", True, True)
    with store.db:
        for frame, status in ((40, "confirmed"), (25, "candidate")):
            store.db.execute(
                "INSERT INTO events(source,frame,arm,type,status,details) VALUES(?,?,?,?,?,?)",
                (src, frame, "left", "test", status, "{}"),
            )
    store.select(key, 0, 0, 60)
    rules = [
        {"arm": a, "field_id": f, "class_ids": [v]}
        for a, f, v in (
            ("left", "contact.mode", 7),
            ("right", "contact.mode", 23),
            ("global", "quality", 7),
        )
    ]
    defaults = client.get("/api/project", headers=headers).json()["default_key_classes"]
    assert defaults == []
    body = {
        "dataset": key,
        "destination": str(tmp_path / "export"),
        "key_classes": rules,
        "expected_schema_version": 1,
        "key_ratio": 0.6,
    }
    assert (
        client.post(
            "/api/export", headers=headers, json={**body, "expected_schema_version": 2}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/export",
            headers=headers,
            json={
                **body,
                "key_classes": [
                    {"arm": "left", "field_id": "quality", "class_ids": [7]}
                ],
            },
        ).status_code
        == 400
    )
    entered, release = threading.Event(), threading.Event()
    run = exporter.run

    def delayed_run(*args):
        entered.set()
        assert release.wait(10)
        run(*args)

    monkeypatch.setattr(exporter, "run", delayed_run)
    response = client.post("/api/export", headers=headers, json=body)
    assert response.status_code == 200, response.text
    job = response.json()
    assert entered.wait(5)
    try:
        # Edits made after starting the job cannot enter the frozen labels/events.
        store.annotate(src, 50, 59, "global", "quality", 7, "state", True, True)
        with store.db:
            store.db.execute("DELETE FROM events")
    finally:
        release.set()
        exporter.threads[job["id"]].join(30)
    status = client.get("/api/jobs/" + job["id"], headers=headers).json()
    assert status["status"] == "complete", status
    dest = tmp_path / "export"
    verify_manifest(dest)
    samples = pq.read_table(dest / "annotations/samples.parquet").to_pylist()
    expected = (
        set(range(8)) | set(range(10, 16)) | set(range(20, 24)) | set(range(32, 49))
    )
    assert {r["source_frame_index"] for r in samples if r["pool"] == "key"} == expected
    assert all(
        r["key_zone_left"] is None and r["key_zone_right"] is None for r in samples
    )
    for pool, mass in (("key", 0.6), ("background", 0.4)):
        assert np.isclose(
            sum(r["sampling_weight"] for r in samples if r["pool"] == pool), mass
        )
    assert samples[-1]["sampling_weight"] == 0
    assert (
        len({r["event_group"] for r in samples if ":classes:" in r["event_group"]}) == 5
    )
    manifest = json.loads((dest / "annotations/manifest.json").read_text())
    assert manifest["sampling"]["policy"] == sampling_policy(store.schema(), rules)
    assert manifest["sampling"]["policy_hash"] == digest(manifest["sampling"]["policy"])
    assert manifest["schema_hash"] == digest(store.schema())
    assert status["sampling"] == sampling_metadata(manifest["sampling"]["policy"])


def test_empty_generic_selection_uses_background_only(source, tmp_path):
    schemes = Schemes(tmp_path / "work")
    info = schemes.create("Event only", generic_schema())
    store, exporter = schemes.get(info["id"])
    ds = Dataset(source, tmp_path / "cache")
    fp = content_fingerprint(source)[0]
    src = ds.source_key(0, 0, fp)[0]
    store.annotate(src, 20, 25, "global", "quality", 7, "state", True, True)
    store.select(fp, 0, 20, 25)
    rules = []
    job = exporter.start(ds, fp, tmp_path / "export", key_classes=rules)
    rules.append({"arm": "global", "field_id": "quality", "class_ids": [7]})
    exporter.threads[job["id"]].join(30)
    assert job["status"] == "complete", job
    samples = pq.read_table(tmp_path / "export/annotations/samples.parquet").to_pylist()
    assert {r["pool"] for r in samples} == {"background"}
    assert np.isclose(sum(r["sampling_weight"] for r in samples), 1)
    assert all(
        r["sampling_weight"] == 0 for r in samples if not r["center_is_selected"]
    )
    assert "无可用 key 采样池" in job["summary"]["warnings"]
    assert job["sampling"]["policy"]["key_classes"] == []
