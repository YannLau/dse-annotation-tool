import copy

import pytest
from fastapi.testclient import TestClient

from dse_tool.api import create_app
from dse_tool.schema import default_schema, resolve


def test_import_preview_publish_and_reopen(tmp_path):
    work = tmp_path / "work"
    client = TestClient(create_app(work))
    original = client.get("/api/project").json()["schema"]
    candidate = copy.deepcopy(original)
    candidate["version"] = 99
    candidate["fields"][1]["applicable_when"] = True
    candidate["fields"][1]["required_when"] = True
    response = client.post("/api/schema/import", json={"schema": candidate})
    assert response.status_code == 200
    draft = response.json()["schema"]
    assert draft["version"] == original["version"]
    assert client.get("/api/project").json()["schema"] == original
    preview = client.post("/api/schema/preview", json={"schema": draft})
    assert preview.status_code == 200
    assert preview.json()["migration"]["changed_fields"] == ["holding"]
    assert client.post("/api/schema", json=draft).status_code == 200
    reopened = TestClient(create_app(work)).get("/api/project").json()["schema"]
    assert reopened["version"] == 2
    assert reopened["fields"][1]["applicable_when"] is True


@pytest.mark.parametrize(
    "candidate", [None, [], {}, {"fields": [None]}, {"fields": [{}]}]
)
def test_invalid_import_is_rejected_without_changes(tmp_path, candidate):
    client = TestClient(create_app(tmp_path / "work"))
    original = client.get("/api/project").json()["schema"]
    assert (
        client.post("/api/schema/import", json={"schema": candidate}).status_code == 400
    )
    assert client.get("/api/project").json()["schema"] == original


def test_import_preserves_identity_and_rule_validation(tmp_path):
    client = TestClient(create_app(tmp_path / "work"))
    candidate = default_schema()
    candidate["fields"][0]["classes"][0]["key"] = "different_meaning"
    assert (
        client.post("/api/schema/import", json={"schema": candidate}).status_code == 400
    )
    candidate = default_schema()
    candidate["fields"][1]["applicable_when"] = {
        "field": "self.holding",
        "op": "eq",
        "value": 0,
    }
    assert (
        client.post("/api/schema/import", json={"schema": candidate}).status_code == 400
    )
    candidate = default_schema()
    candidate["fields"][1]["applicable_when"] = {
        "field": "self.adviser.x",
        "op": "eq",
        "value": 0,
    }
    assert (
        client.post("/api/schema/import", json={"schema": candidate}).status_code == 400
    )


def test_presets_can_switch_and_gate_supervision(tmp_path):
    client = TestClient(create_app(tmp_path / "work"))
    presets = client.get("/api/schema/presets").json()
    assert len(presets) == 4
    for preset in presets + presets[:1]:
        response = client.post("/api/schema/import", json={"schema": preset})
        assert response.status_code == 200
        draft = response.json()["schema"]
        assert client.post("/api/schema", json=draft).status_code == 200
        live = client.get("/api/project").json()["schema"]
        raw = {
            "left": {
                f["field_id"]: {
                    "value_id": 0,
                    "review_status": "reviewed",
                    "schema_version": live["version"],
                }
                for f in live["fields"]
            }
        }
        raw["left"]["phase"]["value_id"] = 2
        labels = resolve(live, raw)["left"]
        if preset["preset"]["id"] != "default":
            assert labels["adviser.x"]["effective_loss_mask"] == 0
        if preset["preset"]["id"] == "phase-holding":
            assert labels["close_readiness"]["effective_loss_mask"] == 0
        raw["left"]["phase"]["value_id"] = 0
        idle = resolve(live, raw)["left"]["holding"]
        assert idle["effective_loss_mask"] == int(
            preset["preset"]["id"] == "state-full-holding"
        )
