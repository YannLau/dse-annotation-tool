import copy
import json
import numpy as np
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from dse_tool.api import create_app
from dse_tool.store import Store
from dse_tool.schema import (
    default_schema,
    deletion_plan,
    prepare_schema,
    validate_schema,
    head_spec,
)
from dse_tool.adapter import DSEDataset


def change(field, value, arm="left", **kw):
    return dict(
        source="source",
        start=0,
        end=4,
        arm=arm,
        field=field,
        value=value,
        mode="behavior" if field.startswith("adviser.") else "state",
        **kw,
    )


def test_batch_preview_updates_gates_without_writes_and_commits_once(tmp_path):
    s = Store(tmp_path / "p")
    before = s.snapshot()
    revision = s.get("revision")
    history = s.db.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    # Children intentionally arrive before their parent: batch ordering must handle it.
    changes = [
        change("close_readiness", 2),
        change("holding", 2),
        change("phase", 2),
        change("phase", 4, "right"),
        change("release_readiness", 2, "right"),
    ]
    preview = s.annotate_batch(
        changes,
        preview=True,
        expected_version=1,
        focus={"source": "source", "frame": 0},
    )
    labels = preview["form_labels"]
    assert labels["left"]["phase"]["value_id"] == 2
    assert labels["left"]["holding"]["required"] is True
    assert labels["left"]["close_readiness"]["required"] is True
    assert labels["left"]["release_readiness"]["applicability"] == "not_applicable"
    assert labels["right"]["release_readiness"]["required"] is True
    assert all(r["affected"] == 4 and r["skipped"] == 0 for r in preview["reports"])
    assert s.snapshot() == before and s.get("revision") == revision
    assert s.db.execute("SELECT COUNT(*) FROM history").fetchone()[0] == history
    result = s.annotate_batch(changes, expected_version=1)
    assert result["saved"] is True
    assert s.get("revision") == revision + 1
    assert s.db.execute("SELECT COUNT(*) FROM history").fetchone()[0] == history + 1
    assert s.labels("source", 2)["left"]["close_readiness"]["value_id"] == 2
    assert s.undo() and s.snapshot() == before
    assert s.undo(True)
    assert s.labels("source", 2)["right"]["release_readiness"]["value_id"] == 2
    reopened = Store(tmp_path / "p")
    assert reopened.labels("source", 2) == s.labels("source", 2)


def test_batch_failure_rolls_back_every_field_and_stale_schema(tmp_path):
    s = Store(tmp_path / "p")
    before = s.snapshot()
    rev = s.get("revision")
    for preview in [True, False]:
        with pytest.raises(ValueError):
            s.annotate_batch(
                [change("phase", 2), change("holding", 99)],
                preview=preview,
                expected_version=1,
            )
        assert s.snapshot() == before and s.get("revision") == rev
        assert not s.db.in_transaction
    with pytest.raises(ValueError, match="schema 已变更"):
        s.annotate_batch([change("phase", 2)], expected_version=999)
    s.annotate_batch([change("phase", 2)])
    assert s.labels("source", 0)["left"]["phase"]["value_id"] == 2


def test_batch_respects_per_frame_gates_and_preserves_point_override(tmp_path):
    s = Store(tmp_path / "p")
    s.annotate("source", 1, 2, "left", "phase", 0, "state", point=True)
    result = s.annotate_batch(
        [change("phase", 2), change("close_readiness", 2)],
        preview=True,
        focus={"source": "source", "frame": 1},
    )
    assert result["form_labels"]["left"]["phase"]["value_id"] == 2
    assert result["form_labels"]["left"]["close_readiness"]["required"] is True
    assert result["labels"]["left"]["phase"]["value_id"] == 0
    assert result["reports"][0]["point_overrides"] == 1
    assert (
        result["reports"][1]["affected"] == 3 and result["reports"][1]["skipped"] == 1
    )
    s.annotate_batch([change("phase", 2), change("close_readiness", 2)])
    assert s.labels("source", 1)["left"]["phase"]["value_id"] == 0
    assert s.labels("source", 1)["left"]["close_readiness"]["value_id"] is None
    assert s.labels("source", 2)["left"]["close_readiness"]["value_id"] == 2


def test_batch_review_validates_final_values_not_transient_conflicts(tmp_path):
    s = Store(tmp_path / "p")
    s.annotate("source", 0, 4, "left", "phase", 5, "state")
    s.annotate("source", 0, 4, "left", "holding", 1, "state")
    s.annotate("source", 0, 4, "left", "release_readiness", 1, "state")
    changes = [
        change("phase", 4, reviewed=True, confirmed=True),
        change("holding", 1, reviewed=True, confirmed=True),
        change("release_readiness", 2, reviewed=True, confirmed=True),
    ]
    s.annotate_batch(changes)
    for field in ["phase", "holding", "release_readiness"]:
        assert s.labels("source", 0)["left"][field]["effective_loss_mask"] == 1
    before = s.snapshot()
    with pytest.raises(ValueError, match="release ready"):
        s.annotate_batch(
            [change("release_readiness", 1, reviewed=True, confirmed=True)]
        )
    assert s.snapshot() == before


def test_api_batch_and_behavior_future_reference(source, tmp_path):
    app = create_app(tmp_path / "p")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    changes = [
        {k: v for k, v in change("phase", 2).items() if k != "source"},
        {k: v for k, v in change("holding", 2).items() if k != "source"},
    ]
    changes = [dict(r, dataset=key, episode=0, start=25, end=30) for r in changes]
    payload = {
        "changes": changes,
        "expected_schema_version": 1,
        "preview": True,
        "focus": {"dataset": key, "episode": 0, "frame": 25, "mode": "state"},
    }
    response = c.post("/api/annotations/batch", json=payload)
    assert response.status_code == 200, response.text
    assert "adviser.x" not in response.json()["form_labels"]["left"]
    assert not app.state.store.snapshot()["annotations"]
    payload["preview"] = False
    assert c.post("/api/annotations/batch", json=payload).status_code == 200
    assert (
        c.get(f"/api/window/{key}/0/25?mode=state").json()["labels"]["left"]["holding"][
            "value_id"
        ]
        == 2
    )
    state = c.get(f"/api/window/{key}/0/30?mode=state&future_horizon=1").json()
    assert state["future"] == [] and state["frame"] == 30
    behavior = c.get(f"/api/window/{key}/0/30?mode=behavior&request_id=27").json()
    assert behavior["frame"] == 30 and behavior["request_id"] == 27
    assert [r["frame"] for r in behavior["future"]] == [33, 36, 37]
    for r in behavior["future"]:
        assert r["timestamp"] <= 1 + r["offset_seconds"] + 1e-8
        assert c.get(f"/api/frame/{key}/0/0/{r['frame']}").status_code == 200
    near_end = c.get(f"/api/window/{key}/0/57?mode=behavior").json()
    assert all(r["frame"] is None for r in near_end["future"])
    assert c.get(f"/api/window/{key}/0/20?future_horizon=-1").status_code == 422
    assert (
        c.post(
            "/api/preferences",
            headers={"Origin": "http://127.0.0.1:8766", "Host": "127.0.0.1:8766"},
            json={"futureHorizon": 1},
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/api/preferences", headers={"Origin": "https://evil.invalid"}, json={}
        ).status_code
        == 403
    )


def custom_field(schema, key="custom"):
    field = copy.deepcopy(schema["fields"][1])
    field.update(field_id=key, applicable_when=True, required_when=False)
    field.pop("independent_when_missing", None)
    return field


def test_deletion_checks_nested_references_unknown_and_semantic_dependencies():
    s = default_schema()
    assert not deletion_plan(s, "phase")["allowed"]
    assert not deletion_plan(s, "holding")["allowed"]
    assert not deletion_plan(s, "phase", 2)["allowed"]
    unknown = deletion_plan(s, "adviser.x", 3)
    assert not unknown["allowed"] and any(
        "unknown_id" in r for r in unknown["blockers"]
    )
    f = custom_field(s)
    f["evidence"] = "behavior"
    f["applicable_when"] = {
        "any": [False, {"not": {"field": "right.adviser.x", "op": "ne", "value": 1}}]
    }
    s["fields"].append(f)
    blocked = deletion_plan(s, "adviser.x", 1)
    assert not blocked["allowed"] and any(
        "custom.applicable_when" in r for r in blocked["blockers"]
    )
    assert not deletion_plan(s, "adviser.x")["allowed"]
    s["fields"].remove(f)
    assert deletion_plan(s, "adviser.x", 1)["allowed"]
    assert deletion_plan(s, "adviser.x")["allowed"]


def test_deleted_identity_never_reused_even_in_later_versions(tmp_path):
    store = Store(tmp_path / "p")
    schema = copy.deepcopy(store.schema())
    schema["fields"].append(custom_field(schema, "retire_me"))
    store.publish(schema)
    removed = deletion_plan(store.schema(), "retire_me")["schema_after"]
    store.publish(removed)
    assert "retire_me" in store.schema()["retired_fields"]
    invalid = copy.deepcopy(store.schema())
    invalid["fields"].append(custom_field(invalid, "retire_me"))
    with pytest.raises(ValueError, match="不可复用"):
        store.publish(invalid)
    removed_class = deletion_plan(store.schema(), "adviser.x", 1)["schema_after"]
    store.publish(removed_class)
    assert 1 in [c["id"] for c in store.schema()["retired_classes"]["adviser.x"]]
    invalid = copy.deepcopy(store.schema())
    next(f for f in invalid["fields"] if f["field_id"] == "adviser.x")[
        "classes"
    ].append({"id": 1, "key": "different", "label_zh": "不同含义"})
    with pytest.raises(ValueError, match="不可复用"):
        store.publish(invalid)
    # Omitting tombstones in a subsequent client payload must not clear the registry.
    stripped = copy.deepcopy(store.schema())
    stripped.pop("retired_fields")
    stripped.pop("retired_classes")
    store.publish(stripped)
    assert "retire_me" in store.schema()["retired_fields"]
    assert store.schema()["retired_classes"]["adviser.x"][0]["id"] == 1


def test_deleted_labels_retained_in_history_masked_and_exportable(source, tmp_path):
    app = create_app(tmp_path / "p")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    store = app.state.store
    ds = app.state.datasets[key]
    uuid, _, _ = ds.source_key(0, 0, key)
    store.annotate(uuid, 20, 30, "left", "phase", 2, "state", True, True)
    store.annotate(uuid, 20, 30, "left", "adviser.x", 1, "behavior", True)
    response = c.post(
        "/api/schema/delete-preview", json={"field_id": "adviser.x", "class_id": 1}
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["allowed"] and plan["historical_annotation_records"] == 1
    assert store.labels(uuid, 25)["left"]["adviser.x"]["value_id"] == 1
    assert c.post("/api/schema", json=plan["schema_after"]).status_code == 200
    retired = store.labels(uuid, 25)["left"]["adviser.x"]
    assert retired["value_id"] is None and retired["retired_value_id"] == 1
    assert (
        retired["effective_loss_mask"] == 0
        and retired["review_status"] == "needs_review"
    )
    assert store.raw(uuid, 25)["left"]["adviser.x"]["value_id"] == 1
    assert (
        1
        not in next(
            h
            for h in head_spec(store.schema())["categorical_heads"]
            if h["field_id"] == "adviser.x"
        )["class_ids"]
    )
    plan = c.post("/api/schema/delete-preview", json={"field_id": "adviser.y"}).json()
    assert plan["allowed"]
    assert c.post("/api/schema", json=plan["schema_after"]).status_code == 200
    store.select(key, 0, 25, 30)
    dest = tmp_path / "export"
    job = c.post("/api/export", json={"dataset": key, "destination": str(dest)}).json()
    app.state.exporter.threads[job["id"]].join(30)
    assert c.get("/api/jobs/" + job["id"]).json()["status"] == "complete"
    wrapper = DSEDataset(dest, tmp_path / "cache")
    batch = wrapper.batch([0])
    assert (
        batch["labels"]["left"]["adviser.x"][0] == -100
        and batch["loss_masks"]["left"]["adviser.x"][0] == 0
    )
    assert "adviser.y" not in batch["labels"]["left"]
    reopened = TestClient(create_app(tmp_path / "reopen"))
    r = reopened.post("/api/import", json={"path": str(dest)})
    assert r.status_code == 200, r.text
    w = reopened.get(f"/api/window/{r.json()['fingerprint']}/0/18?mode=behavior").json()
    assert (
        w["labels"]["left"]["adviser.x"]["value_id"] is None
        and "adviser.y" not in w["labels"]["left"]
    )
