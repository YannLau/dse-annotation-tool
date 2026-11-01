import pytest
from fastapi.testclient import TestClient

from dse_tool.api import create_app
from dse_tool.store import Store


def change(field="phase", value=4, **kwargs):
    return dict(
        source="source",
        start=2,
        end=6,
        arm="left",
        field=field,
        value=value,
        mode="state",
        **kwargs,
    )


@pytest.mark.parametrize("policy", [None, "preserve", "overwrite"])
def test_range_point_policy_preview_scope_and_history(tmp_path, policy):
    store = Store(tmp_path / "project")
    store.annotate("source", 0, 8, "left", "phase", 2, "state")
    for frame, value in [(1, 8), (3, None), (5, 0), (6, 3)]:
        store.annotate(
            "source", frame, frame + 1, "left", "phase", value, "state", point=True
        )
    store.annotate("source", 3, 4, "right", "phase", 4, "state", point=True)
    store.annotate("other", 3, 4, "left", "phase", 8, "state", point=True)
    before = store.snapshot()
    revision = store.get("revision")
    history = store.db.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    changes = [change(**({"point_policy": policy} if policy else {}))]
    preview = store.annotate_batch(
        changes, preview=True, focus={"source": "source", "frame": 3}
    )
    overwrite = policy == "overwrite"
    assert preview["reports"][0]["overwritten_points"] == (2 if overwrite else 0)
    assert preview["reports"][0]["point_overrides"] == (0 if overwrite else 2)
    assert preview["labels"]["left"]["phase"]["value_id"] == (4 if overwrite else None)
    assert store.snapshot() == before
    assert store.get("revision") == revision
    assert store.db.execute("SELECT COUNT(*) FROM history").fetchone()[0] == history

    result = store.annotate_batch(changes)
    assert result["reports"] == preview["reports"]
    assert [store.raw("source", i)["left"]["phase"]["value_id"] for i in range(8)] == (
        [2, 8, 4, 4, 4, 4, 3, 2] if overwrite else [2, 8, 4, None, 4, 0, 3, 2]
    )
    assert store.raw("source", 3)["right"]["phase"]["value_id"] == 4
    assert store.raw("other", 3)["left"]["phase"]["value_id"] == 8
    after = store.snapshot()
    assert store.get("revision") == revision + 1
    assert store.db.execute("SELECT COUNT(*) FROM history").fetchone()[0] == history + 1
    assert store.undo() and store.snapshot() == before
    assert store.undo(True) and store.snapshot() == after
    assert Store(tmp_path / "project").snapshot() == after


def test_overwrite_parent_recomputes_child_gates(tmp_path):
    store = Store(tmp_path / "project")
    store.annotate("source", 3, 4, "left", "phase", 0, "state", point=True)
    changes = [
        change("close_readiness", 2, point_policy="overwrite"),
        change(point_policy="overwrite"),
    ]
    # Use grasp_align so the child becomes applicable after replacing the point.
    changes[1]["value"] = 2
    before = store.snapshot()
    preview = store.annotate_batch(changes, preview=True)
    assert preview["reports"][0]["affected"] == 4
    assert preview["reports"][0]["skipped"] == 0
    assert preview["reports"][1]["overwritten_points"] == 1
    assert store.snapshot() == before
    store.annotate_batch(changes)
    assert store.labels("source", 3)["left"]["close_readiness"]["value_id"] == 2


def test_overwrite_leaves_points_on_inapplicable_frames_and_other_fields(tmp_path):
    store = Store(tmp_path / "project")
    store.annotate("source", 0, 8, "left", "phase", 2, "state")
    for frame in [3, 4]:
        store.annotate(
            "source",
            frame,
            frame + 1,
            "left",
            "close_readiness",
            2,
            "state",
            point=True,
        )
    store.annotate("source", 3, 4, "left", "phase", 0, "state", point=True)
    store.annotate("source", 4, 5, "left", "holding", 2, "state", point=True)
    other_field = store.raw("source", 4)["left"]["holding"]
    result = store.annotate_batch(
        [change("close_readiness", 1, point_policy="overwrite")]
    )
    assert result["reports"][0]["skipped"] == 1
    assert result["reports"][0]["overwritten_points"] == 1
    assert result["reports"][0]["point_overrides"] == 1
    assert store.raw("source", 3)["left"]["close_readiness"]["value_id"] == 2
    assert store.raw("source", 4)["left"]["close_readiness"]["value_id"] == 1
    assert store.raw("source", 4)["left"]["holding"] == other_field


@pytest.mark.parametrize("preview", [False, True])
def test_overwrite_failure_restores_point_records(tmp_path, preview):
    store = Store(tmp_path / "project")
    store.annotate("source", 3, 4, "left", "phase", 8, "state", point=True)
    before = store.snapshot()
    revision = store.get("revision")
    with pytest.raises(ValueError, match="无效/废弃类别"):
        store.annotate_batch(
            [change(point_policy="overwrite"), change("holding", 99)],
            preview=preview,
        )
    assert store.snapshot() == before
    assert store.get("revision") == revision
    assert not store.db.in_transaction


@pytest.mark.parametrize("endpoint", ["/api/annotations", "/api/annotations/batch"])
def test_api_point_policy_preview_and_single_frame_edits(source, tmp_path, endpoint):
    app = create_app(tmp_path / "project")
    client = TestClient(app)
    key = client.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    point = dict(
        dataset=key,
        episode=0,
        start=3,
        end=4,
        arm="left",
        field="phase",
        value=8,
        mode="state",
        point=True,
    )
    assert client.post("/api/annotations", json=point).status_code == 200
    base = dict(point, start=2, end=6, value=2, point=False)

    def assign(**options):
        annotation = dict(base, **options)
        if endpoint.endswith("/batch"):
            annotation = {
                "changes": [annotation],
                "expected_schema_version": 1,
                "preview": options.get("preview", False),
            }
        return client.post(endpoint, json=annotation)

    source_id = app.state.datasets[key].source_key(0, 3, key)[0]
    assert assign().status_code == 200
    assert app.state.store.raw(source_id, 3)["left"]["phase"]["value_id"] == 8
    before = app.state.store.snapshot()
    response = assign(point_policy="overwrite", preview=True)
    assert response.status_code == 200, response.text
    report = (
        response.json()["reports"][0]
        if endpoint.endswith("/batch")
        else response.json()
    )
    assert report["overwritten_points"] == 1
    assert app.state.store.snapshot() == before
    assert assign(point_policy="invalid").status_code == 422
    assert assign(point_policy="overwrite").status_code == 200
    assert app.state.store.raw(source_id, 3)["left"]["phase"]["value_id"] == 2
    # Subsequent ordinary single-frame edits still take priority.
    assert client.post("/api/annotations", json=point).status_code == 200
    assert app.state.store.raw(source_id, 3)["left"]["phase"]["value_id"] == 8
