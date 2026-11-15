import copy
import json
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

import pyarrow.parquet as pq
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from dse_tool.adapter import DSEDataset
from dse_tool.api import create_app
from dse_tool.schema import default_schema, semantic_errors, resolve


def unrelated_schema():
    field = copy.deepcopy(default_schema()["fields"][0])
    field.update(
        field_id="quality",
        scope="global",
        unknown_id=99,
        applicable_when=True,
        required_when=True,
        classes=[{"id": 7, "key": "pass"}, {"id": 99, "key": "unsure"}],
    )
    return {"version": 90, "fields": [field]}


def create(client, name="Quality", schema=None):
    response = client.post(
        "/api/schemes", json={"name": name, "schema": schema or unrelated_schema()}
    )
    assert response.status_code == 200, response.text
    info = response.json()
    return info, {"X-Annotation-Scheme": info["id"]}


def test_new_scheme_camera_frames_without_existing_cache(source, tmp_path):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    key = client.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    original = app.state.store.snapshot()
    info, headers = create(client)
    store, _ = app.state.schemes.get(info["id"])
    # The shared Dataset is already loaded by the original scheme. Opening it
    # does not initialize the new scheme's parent cache directory.
    assert client.get("/api/datasets/" + key, headers=headers).status_code == 200
    assert not (store.root / "cache").exists()
    for camera in range(3):
        url = f"/api/frame/{key}/0/{camera}/0?scheme={info['id']}"
        response = client.get(url)  # Image elements send the query, not headers.
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(response.content)) as image:
            assert image.size == (32, 24)
            image.verify()
        assert client.get(url).content == response.content
    assert app.state.store.snapshot() == original
    assert all(not rows for rows in store.snapshot().values())
    reopened = TestClient(create_app(tmp_path / "work"))
    assert reopened.get(url).status_code == 200


def test_independent_rules_history_and_restart(tmp_path):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    old = app.state.store
    old.annotate("src", 0, 2, "left", "phase", 2, "state", True, True)
    before, old_schema = old.snapshot(), old.schema()
    info, headers = create(client)
    project = client.get("/api/project", headers=headers).json()
    assert project["schema"]["version"] == 1
    assert project["schema"]["semantic_profile"] == "generic"
    store, _ = app.state.schemes.get(info["id"])
    assert all(not rows for rows in store.snapshot().values())
    assert semantic_errors(resolve(store.schema(), {}), store.schema()) == [
        "global: 缺少必填 quality"
    ]
    store.annotate("src", 0, 2, "global", "quality", 7, "state", True, True)
    assert store.labels("src", 0)["global"]["quality"]["effective_loss_mask"] == 1
    assert store.undo()
    assert store.raw("src", 0)["global"] == {}
    assert store.undo(True)
    changed = copy.deepcopy(store.schema())
    changed["fields"][0]["required_when"] = False
    assert client.post("/api/schema", headers=headers, json=changed).status_code == 200
    assert old.snapshot() == before and old.schema() == old_schema
    reopened = create_app(tmp_path / "work")
    new_store, _ = reopened.state.schemes.get(info["id"])
    assert new_store.schema()["version"] == 2
    assert new_store.raw("src", 0)["global"]["quality"]["value_id"] == 7
    assert reopened.state.store.snapshot() == before


def test_preview_and_invalid_creation_do_not_change_project(tmp_path):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    initial = client.get("/api/project").json()
    assert (
        client.post(
            "/api/schemes/preview", json={"schema": unrelated_schema()}
        ).status_code
        == 200
    )
    for candidate in (None, {}, {"fields": [None]}):
        assert (
            client.post(
                "/api/schemes", json={"name": "Bad", "schema": candidate}
            ).status_code
            == 400
        )
    assert client.get("/api/project").json() == initial
    assert (
        client.get(
            "/api/project", headers={"X-Annotation-Scheme": "../../bad"}
        ).status_code
        == 400
    )
    # A conflicting category identity is valid only in a new namespace.
    candidate = default_schema()
    candidate["fields"][0]["classes"][0]["key"] = "new_meaning"
    assert (
        client.post("/api/schema/import", json={"schema": candidate}).status_code == 400
    )
    info, headers = create(client, schema=candidate)
    assert (
        client.get("/api/project", headers=headers).json()["schema"]["fields"][0][
            "classes"
        ][0]["key"]
        == "new_meaning"
    )


def test_rule_preview_uses_current_scheme_fields_and_scopes(tmp_path):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    schema = unrelated_schema()
    dependent = copy.deepcopy(schema["fields"][0])
    dependent.update(
        field_id="contact.mode",
        scope="each_arm",
        applicable_when={"field": "global.quality", "op": "eq", "value": 7},
        required_when={"field": "global.quality", "op": "eq", "value": 7},
    )
    schema["fields"].append(dependent)
    info, headers = create(client, schema=schema)
    store, _ = app.state.schemes.get(info["id"])
    original = store.schema()
    values = {
        "global": {"quality": 7},
        "left": {"contact.mode": 7},
        "right": {"contact.mode": 99},
    }
    response = client.post(
        "/api/schema/preview", headers=headers, json={"values": values}
    )
    assert response.status_code == 200, response.text
    labels = response.json()["labels"]
    assert labels["global"]["quality"]["value_id"] == 7
    assert set(labels["left"]) == set(labels["right"]) == {"contact.mode"}
    assert labels["left"]["contact.mode"]["applicability"] == "applicable"
    assert labels["left"]["contact.mode"]["required"] is True
    assert labels["right"]["contact.mode"]["value_id"] == 99
    values["global"]["quality"] = 99
    unknown = client.post(
        "/api/schema/preview", headers=headers, json={"values": values}
    ).json()["labels"]
    assert unknown["left"]["contact.mode"]["applicability"] == "undetermined"
    empty = client.post("/api/schema/preview", headers=headers, json={}).json()[
        "labels"
    ]
    assert empty["left"]["contact.mode"]["applicability"] == "undetermined"
    assert empty["global"]["quality"]["value_id"] is None
    assert store.schema() == original
    assert all(not rows for rows in store.snapshot().values())
    assert all(
        record["effective_loss_mask"] == 0
        for fields in labels.values()
        for record in fields.values()
    )


@pytest.mark.parametrize(
    "values",
    [
        [],
        {"middle": {}},
        {"global": []},
        {"left": {"quality": 7}},
        {"global": {"phase": 2}},
        {"global": {"quality": 8}},
        {"global": {"quality": "7"}},
        {"global": {"quality": True}},
    ],
)
def test_rule_preview_rejects_values_outside_current_scheme(tmp_path, values):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    _, headers = create(client)
    response = client.post(
        "/api/schema/preview", headers=headers, json={"values": values}
    )
    assert response.status_code == 400, response.text


def test_rule_preview_does_not_invent_phase_values_and_accepts_legacy_arguments(
    tmp_path,
):
    client = TestClient(create_app(tmp_path / "work"))
    empty = client.post("/api/schema/preview", json={}).json()["labels"]
    assert empty["left"]["phase"]["value_id"] is None
    assert empty["right"]["phase"]["value_id"] is None
    legacy = client.post("/api/schema/preview", json={"left": 2, "right": 4})
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["labels"]["left"]["phase"]["value_id"] == 2
    explicit = client.post(
        "/api/schema/preview", json={"left": 2, "values": {"right": {"phase": 5}}}
    ).json()["labels"]
    assert explicit["left"]["phase"]["value_id"] is None
    assert explicit["right"]["phase"]["value_id"] == 5


def test_concurrent_requests_never_cross_schemes(tmp_path):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    _, a = create(client, "A")
    _, b = create(client, "B")

    def edit(headers, value):
        for _ in range(3):
            assert (
                client.post(
                    "/api/preferences", headers=headers, json={"owner": value}
                ).status_code
                == 200
            )
            assert client.get("/api/project", headers=headers).json()[
                "preferences"
            ] == {"owner": value}

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(edit, a, "A"), pool.submit(edit, b, "B")]
        for f in futures:
            f.result(timeout=20)
    assert client.get("/api/project").json()["preferences"] == {}


def test_shared_source_export_reader_and_explicit_restore(source, tmp_path):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    key = client.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    ds = app.state.datasets[key]
    src, _, _ = ds.source_key(0, 0, key)
    app.state.store.annotate(src, 0, 4, "left", "phase", 2, "state", True, True)
    old = app.state.store.snapshot()
    info, headers = create(client)
    data = client.get("/api/datasets/" + key, headers=headers)
    assert data.status_code == 200, data.text
    assert (
        client.get("/api/project", headers=headers).json()["datasets"][0]["fingerprint"]
        == key
    )
    response = client.post(
        "/api/annotations",
        headers=headers,
        json={
            "dataset": key,
            "episode": 0,
            "start": 3,
            "end": 8,
            "arm": "global",
            "field": "quality",
            "value": 7,
            "mode": "state",
            "reviewed": True,
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    store, exporter = app.state.schemes.get(info["id"])
    store.select(key, 0, 3, 8)
    target = tmp_path / "export"
    job = client.post(
        "/api/export",
        headers=headers,
        json={"dataset": key, "destination": str(target)},
    ).json()
    exporter.threads[job["id"]].join(30)
    assert target.exists(), (
        store.get_job(job["id"]) if hasattr(store, "get_job") else job
    )
    assert app.state.store.snapshot() == old
    manifest = json.loads((target / "annotations/manifest.json").read_text())
    assert manifest["annotation_scheme"] == info
    reader = DSEDataset(target, tmp_path / "reader", size=16)
    assert reader.annotation_scheme == info
    item = reader[0]
    assert item["labels"]["global"]["quality"] == 7
    assert item["loss_masks"]["global"]["quality"] == 1
    assert item["labels"]["left"] == {}
    assert all(
        r["key_zone_left"] is None
        for r in pq.read_table(target / "annotations/samples.parquet").to_pylist()
    )
    _, fresh_headers = create(client, "Fresh")
    fresh_info = client.get("/api/project", headers=fresh_headers).json()
    imported = client.post(
        "/api/import", headers=fresh_headers, json={"path": str(target)}
    )
    assert imported.status_code == 200, imported.text
    fresh, _ = app.state.schemes.get(fresh_info["scheme"]["id"])
    assert all(not rows for rows in fresh.snapshot().values())
    assert fresh.schema() == fresh_info["schema"]
    # Explicit restoration works even when this source is already registered.
    response = client.post(
        "/api/import",
        headers=fresh_headers,
        json={"path": str(target), "restore_annotations": True},
    )
    assert response.status_code == 200, response.text
    assert fresh.labels(src, 3)["global"]["quality"]["effective_loss_mask"] == 1
    snapshot = fresh.snapshot()
    assert (
        client.post(
            "/api/import",
            headers=fresh_headers,
            json={"path": str(target), "restore_annotations": True},
        ).status_code
        == 200
    )
    assert fresh.snapshot() == snapshot
    # Never replace an existing unrelated annotation namespace on restore.
    response = client.post(
        "/api/import", json={"path": str(target), "restore_annotations": True}
    )
    assert response.status_code == 400
    assert app.state.store.snapshot() == old


def test_scheme_trash_restore_purge_and_empty_project(source, tmp_path):
    root = tmp_path / "work"
    app = create_app(root)
    client = TestClient(app)
    key = client.post("/api/import", json={"path": str(source)}).json()["fingerprint"]
    info, headers = create(client)
    store, _ = app.state.schemes.get(info["id"])
    store.annotate("src", 0, 2, "global", "quality", 7, "state", True, True)
    before = store.snapshot()
    endpoint = "/api/schemes/" + info["id"]
    assert (
        client.post(
            endpoint + "/purge", json={"confirmation": info["name"]}
        ).status_code
        == 400
    )
    trashed = client.post(endpoint + "/trash", headers=headers).json()
    assert trashed["trash"][0]["annotation_count"] == 1
    assert client.get("/api/project", headers=headers).status_code == 400
    assert (
        client.post("/api/preferences", headers=headers, json={"bad": True}).status_code
        == 400
    )
    assert client.get("/api/schemes", headers=headers).status_code == 200
    assert client.post(endpoint + "/restore", headers=headers).status_code == 200
    assert store.snapshot() == before
    assert client.post(endpoint + "/trash").status_code == 200
    assert (
        client.post(endpoint + "/purge", json={"confirmation": "wrong"}).status_code
        == 400
    )
    export = store.root / "my-export"
    export.mkdir()
    (export / "keep.txt").write_text("exported data")
    assert (
        client.post(
            endpoint + "/purge", json={"confirmation": info["name"]}
        ).status_code
        == 200
    )
    assert not (store.root / "project.sqlite3").exists()
    assert (export / "keep.txt").exists()
    assert client.post(endpoint + "/restore").status_code == 400
    default_name = app.state.schemes.base.get("scheme_info")["name"]
    assert client.post("/api/schemes/default/trash").json()["schemes"] == []
    assert (
        client.post(
            "/api/schemes/default/purge", json={"confirmation": default_name}
        ).status_code
        == 200
    )
    reopened = TestClient(create_app(root))
    assert reopened.get("/api/schemes").json() == {"schemes": [], "trash": []}
    assert reopened.get("/api/project").status_code == 400
    _, fresh_headers = create(reopened, "After deletion")
    project = reopened.get("/api/project", headers=fresh_headers).json()
    assert project["datasets"][0]["fingerprint"] == key
    assert source.exists()


def test_default_restore_and_running_export_guard(tmp_path):
    app = create_app(tmp_path / "work")
    client = TestClient(app)
    store = app.state.store
    store.annotate("src", 0, 2, "left", "phase", 2, "state", True, True)
    before = store.snapshot()
    import threading

    stop = threading.Event()
    worker = threading.Thread(target=stop.wait)
    app.state.exporter.threads["test"] = worker
    worker.start()
    try:
        assert client.post("/api/schemes/default/trash").status_code == 400
    finally:
        stop.set()
        worker.join()
    assert client.post("/api/schemes/default/trash").status_code == 200
    reopened = TestClient(create_app(tmp_path / "work"))
    assert reopened.get("/api/project").status_code == 400
    assert reopened.post("/api/schemes/default/restore").status_code == 200
    assert store.snapshot() == before
    assert reopened.get("/api/project").status_code == 200
