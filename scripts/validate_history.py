"""Read-only real AV1 acceptance for configurable history and portable training."""

import json
import os
from pathlib import Path
import shutil

import numpy as np
from fastapi.testclient import TestClient
from dse_tool.api import create_app
from dse_tool.adapter import DSEDataset
from dse_tool.dataset import Dataset, content_fingerprint
from dse_tool.history import HistoryConfig

root = Path(__file__).resolve().parents[1]
run = os.environ.get("DSE_HISTORY_RUN", "real-history-20261031")
source = root.parent / "tron2_dataset/lerobot_v2.1_examples"
work = root / "workspaces" / run
export = root / "exports" / run
moved = export.with_name(export.name + "-moved")
if work.exists() or export.exists() or moved.exists():
    raise SystemExit("验收目标已存在，请设置新的 DSE_HISTORY_RUN")
before = content_fingerprint(source)[0]
app = create_app(work)
with TestClient(app) as client:
    response = client.post("/api/import", json={"path": str(source)})
    response.raise_for_status()
    key = response.json()["fingerprint"]
    assert (
        client.post(
            "/api/selections", json=dict(dataset=key, episode=0, start=190, end=193)
        ).status_code
        == 200
    )
    config = HistoryConfig(5, 3)
    window = client.get(
        f"/api/window/{key}/0/190?history_horizon=5&history_frames=3"
    ).json()
    assert [r["frame"] for r in window["history"]] == [40, 115, 190]
    response = client.post(
        "/api/export",
        json=dict(
            dataset=key,
            destination=str(export),
            history_config={"horizon_seconds": 5, "num_frames": 3},
        ),
    )
    response.raise_for_status()
    job_id = response.json()["id"]
    app.state.exporter.threads[job_id].join(300)
    job = client.get("/api/jobs/" + job_id).json()
    assert job["status"] == "complete", job
shutil.move(export, moved)
reader = DSEDataset(moved, work / "reader-cache")
batch = reader.batch([0, 1])
assert batch["images"].shape == (2, 3, 3, 3, 256, 256)
assert batch["image_valid_mask"].all() and batch["state_valid_mask"].all()
original = Dataset(source, work / "source-check-cache")
np.testing.assert_array_equal(
    batch["state"][0],
    [original.rows(0)[f]["observation.state"] for f in [40, 115, 190]],
)
np.testing.assert_allclose(batch["history_actual_offsets"][0], [-5, -2.5, 0], atol=1e-7)
short = DSEDataset(
    moved, work / "short-cache", history_config=HistoryConfig(0.6, 4)
).batch([0])
assert (
    short["images"].shape == (1, 4, 3, 3, 256, 256) and short["image_valid_mask"].all()
)
after = content_fingerprint(source)[0]
assert before == after
report = dict(
    history_config=reader.history_config.to_dict(),
    source_frames=[r["frame"] for r in window["history"]],
    image_shape=list(batch["images"].shape),
    state_shape=list(batch["state"].shape),
    actual_offsets=batch["history_actual_offsets"][0].tolist(),
    all_history_present=True,
    short_history_shape=list(short["images"].shape),
    moved_export=str(moved),
    source_hash_before=before,
    source_hash_after=after,
    source_unchanged=True,
    semantic_labels_created=False,
    job_summary=job["summary"],
)
(root / "docs/history-validation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False, indent=2))
