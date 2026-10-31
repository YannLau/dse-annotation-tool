import json

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from dse_tool.api import create_app
from dse_tool.history import HistoryConfig
from dse_tool.review import review_labels
from dse_tool.store import Store


def annotate(store, field, value, start=0, end=60, arm="left", **kw):
    return store.annotate(
        "source",
        start,
        end,
        arm,
        field,
        value,
        "behavior" if field.startswith("adviser.") else "state",
        **kw,
    )


def test_range_review_preserves_values_points_and_scope_with_one_undo(tmp_path):
    s = Store(tmp_path / "work")
    annotate(s, "phase", 2, end=20)
    annotate(s, "phase", 4, start=20)
    annotate(s, "holding", 2)
    annotate(s, "close_readiness", 2, end=20)
    annotate(s, "phase", 0, arm="right")
    annotate(
        s,
        "phase",
        8,
        start=15,
        end=16,
        point=True,
        provenance={"manual_correction": True},
    )
    before = s.snapshot()
    revision = s.get("revision")
    result = review_labels(
        s, "source", 10, 30, "state", preview=True, expected_version=1
    )
    assert result["reviewed_pairs"] > 0 and not result["saved"]
    assert s.snapshot() == before and s.get("revision") == revision
    result = review_labels(
        s,
        "source",
        10,
        30,
        "state",
        confirmed=True,
        expected_version=1,
        expected_revision=revision,
    )
    assert result["saved"] and result["reviewed_frames"] == 20
    assert s.get("revision") == revision + 1
    for frame, value in [(9, 2), (10, 2), (15, 8), (19, 2), (20, 4), (29, 4), (30, 4)]:
        assert s.raw("source", frame)["left"]["phase"]["value_id"] == value
        assert s.raw("source", frame)["left"]["phase"]["review_status"] == (
            "reviewed" if 10 <= frame < 30 else "draft"
        )
    point = s.db.execute(
        "SELECT * FROM annotations WHERE source='source' AND field='phase' AND arm='left' AND kind='point'"
    ).fetchone()
    assert (point["start"], point["end"], point["value"], point["status"]) == (
        15,
        16,
        8,
        "reviewed",
    )
    assert json.loads(point["provenance"])["manual_correction"] is True
    assert s.raw("source", 15)["left"]["holding"]["review_status"] == "reviewed"
    assert s.raw("source", 15)["left"]["close_readiness"]["review_status"] != "reviewed"
    assert "release_readiness" not in s.raw("source", 20)["left"]
    after = s.snapshot()
    assert s.undo() and s.snapshot() == before
    assert s.undo(True) and s.snapshot() == after
    revision_before_again = s.get("revision")
    again = review_labels(s, "source", 10, 30, "state", confirmed=True)
    assert again["reviewed_pairs"] == 0 and again["already_reviewed_pairs"] > 0
    assert s.get("revision") == revision_before_again
    assert Store(tmp_path / "work").snapshot() == after


def test_range_review_conflicts_partial_supervision_and_mode_isolation(tmp_path):
    s = Store(tmp_path / "work")
    annotate(s, "phase", 2)
    annotate(s, "holding", 1)  # Draft conflict, must not be certified.
    annotate(s, "phase", 0, arm="right")
    result = review_labels(s, "source", 0, 60, "state", confirmed=True)
    assert result["conflict_pairs"] == 120
    assert s.raw("source", 0)["left"]["phase"]["review_status"] == "draft"
    assert s.raw("source", 0)["left"]["holding"]["review_status"] == "draft"
    assert s.raw("source", 0)["right"]["phase"]["review_status"] == "reviewed"
    assert any("grasp_align" in r["reason"] for r in result["issues"])
    other = Store(tmp_path / "other")
    annotate(other, "holding", 2)
    result = review_labels(other, "source", 0, 60, "state", confirmed=True)
    assert (
        result["reviewed_pairs"] == 60
    )  # Independent holding can be reviewed with phase missing.
    assert other.labels("source", 0)["left"]["holding"]["effective_loss_mask"] == 1
    assert "phase" not in other.raw("source", 0)["left"]
    behavior = Store(tmp_path / "behavior")
    annotate(behavior, "phase", 2)
    annotate(behavior, "adviser.x", 1)
    result = review_labels(behavior, "source", 0, 60, "behavior")
    assert result["reviewed_pairs"] == 60
    assert behavior.raw("source", 0)["left"]["adviser.x"]["review_status"] == "reviewed"
    assert behavior.raw("source", 0)["left"]["phase"]["review_status"] == "draft"
    assert behavior.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_range_review_compacts_long_ranges_and_updates_history_evidence(tmp_path):
    s = Store(tmp_path / "work")
    annotate(s, "phase", 8, end=10000, provenance={"origin": "preserved"})
    result = review_labels(
        s,
        "source",
        100,
        9900,
        "state",
        confirmed=True,
        history_config=HistoryConfig(5, 3),
    )
    assert result["reviewed_pairs"] == 9800 and result["updated_records"] == 1
    assert s.db.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 3
    record = s.raw("source", 200)["left"]["phase"]
    assert record["provenance"]["history_config"]["horizon_seconds"] == 5
    assert record["provenance"]["origin"] == "preserved"
    # Re-review under another history setting refreshes evidence metadata once.
    result = review_labels(
        s,
        "source",
        100,
        9900,
        "state",
        confirmed=True,
        history_config=HistoryConfig(0, 1),
    )
    assert result["reviewed_pairs"] == 9800
    assert (
        s.raw("source", 200)["left"]["phase"]["provenance"]["history_config"][
            "num_frames"
        ]
        == 1
    )
    assert s.raw("source", 99)["left"]["phase"]["review_status"] == "draft"


def test_range_review_confirmation_versions_and_atomic_rollback(tmp_path, monkeypatch):
    s = Store(tmp_path / "work")
    annotate(s, "phase", 8)
    annotate(s, "phase", 0, arm="right")
    before = s.snapshot()
    revision = s.get("revision")
    with pytest.raises(ValueError, match="整个审核范围"):
        review_labels(s, "source", 0, 10, "state")
    with pytest.raises(ValueError, match="schema 已变更"):
        review_labels(s, "source", 0, 10, "state", confirmed=True, expected_version=2)
    with pytest.raises(ValueError, match="标注已变化"):
        review_labels(
            s, "source", 0, 10, "state", confirmed=True, expected_revision=revision - 1
        )
    original = s._split_write
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("test failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(s, "_split_write", fail_second)
    with pytest.raises(RuntimeError, match="test failure"):
        review_labels(s, "source", 0, 10, "state", confirmed=True)
    assert s.snapshot() == before and s.get("revision") == revision
    assert not s.db.in_transaction


def test_range_review_api_masks_export_and_read_only_completeness(source, tmp_path):
    app = create_app(tmp_path / "work")
    c = TestClient(app)
    key = c.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    src, _, _ = app.state.datasets[key].source_key(0, 0, key)
    s = app.state.store
    s.annotate(src, 5, 25, "left", "phase", 8, "state")
    s.annotate(src, 0, 60, "right", "phase", 0, "state")
    s.select(key, 0, 20, 40)
    revision = s.get("revision")
    body = dict(
        dataset=key,
        episode=0,
        start=10,
        end=30,
        mode="state",
        expected_schema_version=1,
        expected_revision=revision,
        history_config={"horizon_seconds": 0.6, "num_frames": 4},
    )
    preview = c.post("/api/annotations/review-range", json={**body, "preview": True})
    assert preview.status_code == 200, preview.text
    assert preview.json()["reviewed_pairs"] == 35
    assert s.get("revision") == revision
    assert c.post("/api/annotations/review-range", json=body).status_code == 400
    reviewed = c.post("/api/annotations/review-range", json={**body, "confirmed": True})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["training_center_frames"] == 10
    assert c.get(f"/api/window/{key}/0/10").json()["revision"] == revision + 1
    before = s.snapshot()
    check = c.get(f"/api/completeness/{key}/0/20").json()
    assert not check[
        "complete"
    ]  # holding still missing, phase supervision remains valid.
    assert s.snapshot() == before
    assert (
        c.get(f"/api/window/{key}/0/20").json()["labels"]["left"]["phase"][
            "effective_loss_mask"
        ]
        == 1
    )
    assert (
        c.get(f"/api/window/{key}/0/10").json()["labels"]["left"]["phase"][
            "effective_loss_mask"
        ]
        == 0
    )
    assert (
        c.post(
            "/api/annotations/review-range", json={**body, "confirmed": True}
        ).status_code
        == 400
    )
    dest = tmp_path / "reviewed-export"
    response = c.post("/api/export", json={"dataset": key, "destination": str(dest)})
    jobid = response.json()["id"]
    app.state.exporter.threads[jobid].join(60)
    assert c.get("/api/jobs/" + jobid).json()["status"] == "complete"
    rows = pq.read_table(dest / "annotations/labels.parquet").to_pylist()
    phases = [r for r in rows if r["field_id"] == "phase"]
    assert sum(r["effective_loss_mask"] for r in phases) == 15
    # Imported independent export retains original source frame identity; diagnostics use local frame coordinates.
    app2 = create_app(tmp_path / "second")
    c2 = TestClient(app2)
    k2 = c2.post("/api/import", json={"path": str(dest)}).json()["fingerprint"]
    report = c2.post(
        "/api/annotations/review-range",
        json=dict(
            dataset=k2,
            episode=0,
            start=0,
            end=2,
            mode="state",
            preview=True,
            expected_schema_version=1,
        ),
    ).json()
    assert report["issues"] and all(
        0 <= r["start"] < r["end"] <= 2 for r in report["issues"]
    )
    assert report["issues"][0]["source_start"] >= 2
