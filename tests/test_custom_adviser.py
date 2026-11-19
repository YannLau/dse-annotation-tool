import copy
import json

import numpy as np
import pytest

from dse_tool.adviser import AdviserRequest, AdviserService
from dse_tool.schema import digest
from dse_tool.store import Store


def generic_schema(*, rotation=False):
    gate = {"field": "global.enable", "op": "eq", "value": 20}
    fields = [
        {
            "field_id": "enable",
            "scope": "global",
            "type": "categorical",
            "classes": [
                {"id": 10, "key": "off"},
                {"id": 20, "key": "on"},
                {"id": 30, "key": "also_on"},
                {"id": 999, "key": "unsure"},
            ],
            "unknown_id": 999,
            "evidence": "state",
            "applicable_when": True,
            "required_when": True,
        }
    ]
    axes = ("x", "y", "z", "orientation")
    if rotation:
        axes += ("rot_x", "rot_y", "rot_z")
    for i, axis in enumerate(axes):
        roles = (
            ("keep", "adjust", "unsure")
            if axis == "orientation"
            else ("none", "positive", "negative", "unsure")
        )
        ids = (4, 8, 90) if axis == "orientation" else (11 + i, 29 + i, 43 + i, 97 + i)
        fields.append(
            {
                "field_id": "adviser." + axis,
                "scope": "each_arm",
                "type": "categorical",
                "classes": [{"id": n, "key": role} for n, role in zip(ids, roles)],
                "unknown_id": ids[-1],
                "evidence": "behavior",
                "applicable_when": copy.deepcopy(gate),
                "required_when": False,
            }
        )
    return {"version": 1, "semantic_profile": "generic", "fields": fields}


class Poses:
    def __init__(self, n=80, offset=50, step=0.05):
        self.offset = offset
        self.info = {
            "features": {
                "observation.ee_pose_" + arm: {"shape": [7]}
                for arm in ("left", "right")
            }
        }
        self.data = [
            {
                "timestamp": i * step,
                "observation.ee_pose_left": [i * 0.003, 0, 0, 0, 0, 0, 1],
                "observation.ee_pose_right": [0, -i * 0.003, 0, 0, 0, 0, 1],
            }
            for i in range(n)
        ]

    def rows(self, episode):
        assert episode == 0
        return self.data

    def source_key(self, episode, frame, fingerprint):
        return "source", frame + self.offset, "group"


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "work")
    schema = generic_schema()
    with store.db:
        store.db.execute("DELETE FROM schemas")
        store.db.execute(
            "INSERT INTO schemas VALUES(?,?,?)", (1, digest(schema), json.dumps(schema))
        )
        store.set("schema_version", 1)
        store.set(
            "scheme_info", {"project_id": "project", "id": "custom", "name": "Custom"}
        )
    ds = Poses()

    def bounds(dataset, episode, start, end):
        if not 0 <= start < end <= len(dataset.data):
            raise ValueError("Invalid range")

    service = AdviserService(store, lambda key: ds, bounds)
    store.annotate("source", 50, 130, "global", "enable", 20, "state")
    with store.transaction("approved fixture"):
        store.db.execute(
            "INSERT INTO reviews(source,start,end,status,reason) VALUES(?,?,?,?,?)",
            ("source", 50, 130, "approved", "fixture"),
        )
    calibration = {
        "verified": True,
        "unit": "m",
        "frame": "robot_base",
        "transform": np.eye(4).tolist(),
        "position_noise": 0,
        "angle_noise": 0,
        "binding": {
            "dataset": "data",
            "arm": "left",
            "feature": "observation.ee_pose_left",
        },
    }
    request = AdviserRequest(
        dataset="data", episode=0, arm="left", start=0, end=1, calibration=calibration
    )
    yield store, ds, service, request
    store.close()


def preview(setup, **updates):
    store, ds, service, request = setup
    request = request.model_copy(update=updates)
    return service.preview(request), request


def test_generic_sparse_ids_preview_apply_undo_and_review(setup):
    store, ds, service, request = setup
    before = store.snapshot()
    capabilities = service.capabilities(request)
    assert capabilities["supported"] and capabilities["available"]
    assert all(f["required"] is False for f in capabilities["fields"])
    result, request = preview(setup, end=50)
    assert store.snapshot() == before
    assert result["summary"]["savable"] == 200  # Exceeds the old 128-change limit.
    assert {r["field_id"]: r["value"] for r in result["results"][:4]} == {
        "adviser.x": 29,
        "adviser.y": 12,
        "adviser.z": 13,
        "adviser.orientation": 4,
    }
    assert result["results"][0]["provenance"]["window"]["input_end"] > 1
    assert result["results"][0]["provenance"]["window"]["source_start"] == 50
    applied = service.apply(request.model_copy(update={"plan_id": result["plan_id"]}))
    assert applied["saved"] == 200
    assert store.labels("source", 50)["left"]["adviser.x"]["effective_loss_mask"] == 0
    assert (
        store.raw("source", 50)["left"]["adviser.x"]["label_source"]
        == "rule_suggestion"
    )
    assert not store.raw("source", 100)["left"].get("adviser.x")
    assert store.undo() and store.snapshot() == before
    assert store.undo(True)


@pytest.mark.parametrize(
    "value,code", [(10, "inapplicable"), (999, "undetermined"), (None, "undetermined")]
)
def test_three_value_gates(setup, value, code):
    store, ds, service, request = setup
    store.annotate("source", 50, 130, "global", "enable", value, "state")
    result, _ = preview(setup)
    assert result["summary"]["savable"] == 0
    assert {r["code"] for r in result["results"]} == {code}


def test_explicit_unknown_branch_and_custom_class_mapping(setup):
    store, ds, service, request = setup
    schema = store.schema()
    for field in schema["fields"][1:]:
        field["applicable_when"]["value"] = 999
    schema["fields"][1]["classes"][1]["key"] = "forward_custom"
    # A new independent fixture permits these deliberately different identities.
    with store.db:
        store.db.execute(
            "UPDATE schemas SET payload=?,hash=? WHERE version=1",
            (json.dumps(schema), digest(schema)),
        )
    store.annotate("source", 50, 130, "global", "enable", 999, "state")
    result, _ = preview(setup)
    assert result["results"][0]["code"] == "invalid_mapping"
    result, _ = preview(setup, mappings={"adviser.x": {"positive": 29}})
    assert result["results"][0]["value"] == 29


def test_gate_context_changes_and_unapproved_are_not_skipped(setup):
    store, ds, service, request = setup
    schema = store.schema()
    for field in schema["fields"][1:]:
        field["applicable_when"] = {
            "field": "global.enable",
            "op": "in",
            "values": [20, 30],
        }
    with store.db:
        store.db.execute(
            "UPDATE schemas SET payload=?,hash=? WHERE version=1",
            (json.dumps(schema), digest(schema)),
        )
    store.annotate("source", 52, 130, "global", "enable", 30, "state")
    result, _ = preview(setup)
    assert result["results"][0]["cutoff_reason"] == "gate_context_changed"
    assert result["results"][0]["code"] == "window_too_short"
    store.annotate("source", 52, 130, "global", "enable", 20, "state")
    with store.transaction("rejected fixture"):
        store._split_write("reviews", "source=?", ("source",), 51, 52)
        store.db.execute(
            "INSERT INTO reviews(source,start,end,status,reason) VALUES(?,?,?,?,?)",
            ("source", 51, 52, "rejected", "bad"),
        )
    result, _ = preview(setup)
    assert result["results"][0]["cutoff_reason"] == "unapproved"


@pytest.mark.parametrize(
    "arm,status,expected",
    [("right", "confirmed", 4), ("left", "suggestion", 4), ("left", "confirmed", 0)],
)
def test_only_confirmed_same_arm_events_truncate(setup, arm, status, expected):
    store, ds, service, request = setup
    with store.transaction("event fixture"):
        store.db.execute(
            "INSERT INTO events(source,frame,arm,type,status,details) VALUES(?,?,?,?,?,?)",
            ("source", 52, arm, "open", status, "{}"),
        )
    result, _ = preview(setup)
    assert result["summary"]["savable"] == expected


def test_invalid_quaternion_does_not_discard_valid_translation(setup):
    store, ds, service, request = setup
    for row in ds.data:
        row["observation.ee_pose_left"][3:] = [0, 0, 0, 0]
    result, _ = preview(setup)
    assert result["summary"]["savable"] == 3
    assert result["results"][-1]["code"] == "missing_pose"


@pytest.mark.parametrize("change", ["revision", "context", "calibration", "source"])
def test_stale_plan_is_rejected(setup, change):
    store, ds, service, request = setup
    result, request = preview(setup)
    body = request.model_copy(update={"plan_id": result["plan_id"]})
    if change == "revision":
        store.annotate("source", 50, 51, "global", "enable", 20, "state")
    elif change == "context":
        body = body.model_copy(update={"end": 2})
    elif change == "calibration":
        calibration = copy.deepcopy(request.calibration)
        calibration["position_noise"] = 0.1
        service.preview(request.model_copy(update={"calibration": calibration}))
    elif change == "source":
        ds.offset += 1
    with pytest.raises(ValueError):
        service.apply(body)


def test_explicit_missing_and_existing_human_values_protected(setup):
    store, ds, service, request = setup
    store.annotate("source", 50, 51, "left", "adviser.x", None, "behavior", point=True)
    store.annotate(
        "source", 50, 51, "left", "adviser.y", 98, "behavior", reviewed=True, point=True
    )
    result, request = preview(setup)
    assert result["summary"]["savable"] == 2
    assert result["summary"]["reasons"]["existing_label"] == 2
    service.apply(request.model_copy(update={"plan_id": result["plan_id"]}))
    assert store.raw("source", 50)["left"]["adviser.x"]["value_id"] is None
    assert store.raw("source", 50)["left"]["adviser.y"]["value_id"] == 98


def test_apply_failure_rolls_back_every_field(setup, monkeypatch):
    store, ds, service, request = setup
    result, request = preview(setup)
    before = store.snapshot()
    original = store.annotate
    count = 0

    def fail(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 3:
            raise ValueError("Injected write failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "annotate", fail)
    with pytest.raises(ValueError, match="Injected"):
        service.apply(request.model_copy(update={"plan_id": result["plan_id"]}))
    assert store.snapshot() == before


def test_calibration_binding_and_finite_values(setup):
    store, ds, service, request = setup
    config = copy.deepcopy(request.calibration)
    config["binding"]["arm"] = "right"
    result, _ = preview(setup, calibration=config)
    assert result["summary"]["reasons"] == {"invalid_calibration": 4}
    config = copy.deepcopy(request.calibration)
    config["transform"][0][0] = float("nan")
    result, _ = preview(setup, calibration=config)
    assert result["summary"]["savable"] == 0


def test_motion_reversal_and_quaternion_sign_are_semantic_unknowns(setup):
    store, ds, service, request = setup
    values = [0, 0.03, 0.01, 0.0, 0.0, 0.0]
    for i, x in enumerate(values):
        ds.data[i]["observation.ee_pose_left"][0] = x
        ds.data[i]["observation.ee_pose_left"][3:] = [0, 0, 0, -1 if i % 2 else 1]
    result, _ = preview(setup)
    assert result["results"][0]["value"] == 97
    assert result["results"][-1]["semantic"] == "keep"
    assert result["results"][0]["provenance"]["needs_review"]


def record_rotation(ds, axis, angles):
    # Start tilted 90 degrees about Y so base- and tool-frame axes differ.
    start_xyz = np.array([0, 2**-0.5, 0])
    start_w = 2**-0.5
    for i, angle in enumerate(angles):
        half = np.deg2rad(angle) / 2
        xyz, w = np.array(axis) * np.sin(half), np.cos(half)
        pose_xyz = w * start_xyz + start_w * xyz + np.cross(xyz, start_xyz)
        pose_w = w * start_w - np.dot(xyz, start_xyz)
        # Scale and flip equivalent quaternions on alternating frames.
        ds.data[i]["observation.ee_pose_left"][3:] = (
            np.append(pose_xyz, pose_w) * (-1.2 if i % 2 else 1)
        ).tolist()


def test_rotation_preview_uses_calibrated_base_and_preserves_legacy_fields(setup):
    store, ds, service, request = setup
    record_rotation(ds, (0.6, 0.8, 0), np.linspace(0, 20, 6))
    request.calibration["transform"] = [
        [0, -1, 0, 0],
        [1, 0, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
    ]
    legacy = service.preview(request)["results"]
    assert len(legacy) == 4
    orientation = legacy[-1]
    assert orientation["semantic"] == "adjust"
    rotation = orientation["provenance"]["rotation"]
    assert rotation["frame"] == "robot_base"
    assert rotation["rotation_vector_deg"] == pytest.approx([-16, 12, 0])
    assert rotation["path_angle_deg"] == pytest.approx(20)
    assert rotation["net_angle_deg"] == pytest.approx(20)

    store.publish(generic_schema(rotation=True))
    caps = service.capabilities(request)["fields"]
    assert all(f["code"] == "ready" for f in caps)
    result = service.preview(request)
    assert result["summary"]["savable"] == 7
    assert [row["semantic"] for row in result["results"][-3:]] == [
        "negative",
        "positive",
        "none",
    ]
    assert all(r["provenance"]["rotation"] == rotation for r in result["results"][-3:])

    noisy_request = request.model_copy(
        update={"calibration": {**request.calibration, "angle_noise": np.deg2rad(5)}}
    )
    noisy = service.preview(noisy_request)["results"]
    assert [row["semantic"] for row in noisy[-3:]] == ["negative", "none", "none"]


@pytest.mark.parametrize(
    "angles, semantics, ambiguity, orientation",
    [
        ([0, 20, 0, 0, 0, 0], ["none", "none", "unknown"], "axis_reversal", "unknown"),
        (np.linspace(0, 180, 6), ["unknown"] * 3, "half_turn", "adjust"),
        (
            np.linspace(0, 360, 6),
            ["none", "none", "unknown"],
            "axis_path_mismatch",
            "unknown",
        ),
    ],
)
def test_rotation_directions_require_consistent_motion(
    setup, angles, semantics, ambiguity, orientation
):
    store, ds, _, _ = setup
    store.publish(generic_schema(rotation=True))
    record_rotation(ds, (0, 0, 1), angles)
    result, _ = preview(setup)
    rows = result["results"]
    assert rows[3]["semantic"] == orientation
    assert [row["semantic"] for row in rows[-3:]] == semantics
    provenance = rows[-1]["provenance"]
    assert provenance["needs_review"]
    assert provenance["rotation_ambiguity"] == ambiguity
    assert (provenance["rotation"]["rotation_vector_deg"] is None) == (
        ambiguity == "half_turn"
    )


def test_partial_field_and_arm_gates(setup):
    store, ds, service, request = setup
    schema = store.schema()
    schema["fields"] = [schema["fields"][0], schema["fields"][1], schema["fields"][-1]]
    schema["fields"][1]["applicable_when"] = {
        "field": "left.enable_local",
        "op": "eq",
        "value": 1,
    }
    schema["fields"].append(
        {
            "field_id": "enable_local",
            "scope": "each_arm",
            "type": "categorical",
            "classes": [
                {"id": 0, "key": "off"},
                {"id": 1, "key": "on"},
                {"id": 2, "key": "unknown"},
            ],
            "unknown_id": 2,
            "evidence": "state",
            "applicable_when": True,
            "required_when": True,
        }
    )
    with store.db:
        store.db.execute(
            "UPDATE schemas SET payload=?,hash=? WHERE version=1",
            (json.dumps(schema), digest(schema)),
        )
    store.annotate("source", 50, 130, "left", "enable_local", 1, "state")
    result, _ = preview(setup)
    assert result["summary"]["savable"] == 2
    assert result["summary"]["reasons"] == {"missing_field": 2}
    store.annotate("source", 50, 130, "left", "enable_local", 0, "state")
    result, _ = preview(setup)
    assert result["summary"]["savable"] == 1


@pytest.mark.parametrize("step,expected", [(0.1, 4), (0.25, 4), (0.3, 0)])
def test_real_timestamp_window_boundaries(setup, step, expected):
    store, ds, service, request = setup
    for i, row in enumerate(ds.data):
        row["timestamp"] = i * step
    result, _ = preview(setup)
    assert result["summary"]["savable"] == expected


def test_rejects_wrong_named_pose_layout(setup):
    store, ds, service, request = setup
    ds.info["features"]["observation.ee_pose_left"]["names"] = {
        "pose": ["x", "y", "z", "qw", "qx", "qy", "qz"]
    }
    result, _ = preview(setup)
    assert result["summary"]["reasons"] == {"invalid_calibration": 4}


def test_api_generic_preview_accept_review_export_roundtrip(source, tmp_path):
    from fastapi.testclient import TestClient
    import pyarrow.parquet as pq
    from dse_tool.api import create_app
    from dse_tool.adapter import DSEDataset
    from dse_tool.review import review_labels

    app = create_app(tmp_path / "api-work")
    with TestClient(app) as client:
        imported = client.post("/api/import", json={"path": str(source)})
        assert imported.status_code == 200, imported.text
        key = imported.json()["fingerprint"]
        created = client.post(
            "/api/schemes",
            json={"name": "Generic adviser", "schema": generic_schema(rotation=True)},
        )
        assert created.status_code == 200, created.text
        scheme = created.json()
        headers = {"X-Annotation-Scheme": scheme["id"]}
        store, exporter = app.state.schemes.get(scheme["id"])
        source_id = app.state.datasets[key].source_key(0, 0, key)[0]
        store.annotate(
            source_id,
            0,
            60,
            "global",
            "enable",
            20,
            "state",
            reviewed=True,
            confirmed=True,
        )
        with store.transaction("approved synthetic trajectory"):
            store.db.execute(
                "INSERT INTO reviews(source,start,end,status,reason) VALUES(?,?,?,?,?)",
                (source_id, 0, 60, "approved", "synthetic"),
            )
        body = {
            "dataset": key,
            "episode": 0,
            "arm": "left",
            "start": 10,
            "end": 13,
            "calibration": {
                "verified": True,
                "unit": "m",
                "frame": "robot_base",
                "transform": np.eye(4).tolist(),
                "binding": {
                    "dataset": key,
                    "arm": "left",
                    "feature": "observation.ee_pose_left",
                },
            },
        }
        cap = client.post(
            "/api/adviser/capabilities", headers=headers, json={**body, "frame": 10}
        )
        assert cap.status_code == 200, cap.text
        assert cap.json()["available"]
        result = client.post("/api/adviser/preview", headers=headers, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["summary"]["savable"] == 21
        assert (
            client.post(
                "/api/adviser/apply",
                headers=headers,
                json={**body, "plan_id": result.json()["plan_id"]},
            ).json()["saved"]
            == 21
        )
        assert (
            store.labels(source_id, 10)["left"]["adviser.x"]["effective_loss_mask"] == 0
        )
        review_labels(store, source_id, 10, 13, "behavior")
        store.select(key, 0, 10, 13)
        destination = tmp_path / "export"
        response = client.post(
            "/api/export",
            headers=headers,
            json={"dataset": key, "destination": str(destination)},
        )
        assert response.status_code == 200, response.text
        exporter.threads[response.json()["id"]].join(30)
        job = client.get("/api/jobs/" + response.json()["id"], headers=headers).json()
        assert job["status"] == "complete", job
        labels = pq.read_table(destination / "annotations/labels.parquet").to_pylist()
        valid = [
            r
            for r in labels
            if r["field_id"] == "adviser.x" and r["effective_loss_mask"]
        ]
        assert len(valid) == 3
        provenance = json.loads(valid[0]["provenance"])
        assert provenance["rule_version"] == "schema-approved-displacement-1"
        assert provenance["window"]["source_start"] == valid[0]["source_frame_index"]
        rotation_labels = [
            r
            for r in labels
            if r["field_id"] == "adviser.rot_z" and r["effective_loss_mask"]
        ]
        assert len(rotation_labels) == 3
        rotation_provenance = json.loads(rotation_labels[0]["provenance"])
        assert rotation_provenance["rule_version"] == "schema-approved-rotation-1"
        assert rotation_provenance["rotation"]["rotation_vector_deg"] == [0, 0, 0]
        reader = DSEDataset(destination, tmp_path / "reader-cache", size=16)
        sample = reader[0]
        assert sample["loss_masks"]["left"]["adviser.x"] == 1
        assert sample["loss_masks"]["left"]["adviser.rot_z"] == 1
        reopened = client.post(
            "/api/import",
            headers=headers,
            json={"path": str(destination), "restore_annotations": True},
        )
        assert reopened.status_code == 200, reopened.text
    for instance, _ in app.state.schemes.instances.values():
        instance.close()


def test_capability_profile_change_invalidates_prior_plan(setup):
    store, ds, service, request = setup
    result, request = preview(setup)
    changed = copy.deepcopy(request.calibration)
    changed["position_noise"] = 0.1
    before = store.snapshot()
    service.capabilities(request.model_copy(update={"calibration": changed}))
    assert store.snapshot() == before
    with pytest.raises(ValueError, match="标定"):
        service.apply(request.model_copy(update={"plan_id": result["plan_id"]}))


def test_range_cursor_browsing_does_not_change_target_plan(setup):
    store, ds, service, request = setup
    result, request = preview(setup, end=3)
    body = request.model_copy(update={"plan_id": result["plan_id"], "frame": 2})
    assert service.apply(body)["saved"] == 12


def test_capability_and_closed_gate_do_not_register_future_exposure(setup):
    store, ds, service, request = setup
    service.capabilities(request)
    assert not store.get("future_seen:source", False)
    store.annotate("source", 50, 130, "global", "enable", 10, "state")
    service.preview(request)
    assert not store.get("future_seen:source", False)
    store.annotate("source", 50, 130, "global", "enable", 20, "state")
    service.preview(request)
    assert store.get("future_seen:source") is True
