"""Reproducible real-data acceptance. Does not fabricate semantic annotations."""

import json
import platform
import resource
import shutil
import time
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from dse_tool.api import create_app
from dse_tool.dataset import Dataset, content_fingerprint
from dse_tool.adapter import DSEDataset

root = Path(__file__).resolve().parents[1]
source = root.parent / "tron2_dataset/lerobot_v2.1_examples"
import os

run = os.environ.get("DSE_VALIDATION_RUN", "real-two-segments")
work = root / "workspaces" / run
dest = root / "exports" / run
if dest.exists():
    raise SystemExit("输出已存在；移动它后重新运行，避免覆盖")
t0 = time.perf_counter()
before, signatures = content_fingerprint(source)
app = create_app(work)
c = TestClient(app)
r = c.post("/api/import", json={"path": str(source)})
assert r.status_code == 200, r.text
result = r.json()
key = result["fingerprint"]
assert len(result["episodes"]) == 14
print("Imported", len(result["episodes"]), "episodes", flush=True)
report = {
    "hardware": {
        "platform": platform.platform(),
        "cpu": next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            platform.processor(),
        ),
        "logical_cores": __import__("os").cpu_count(),
    },
    "source": str(source),
    "fingerprint_before": before,
    "import_seconds": time.perf_counter() - t0,
    "frames": result["report"]["frames"],
    "physical_type_warnings": result["report"]["warnings"],
    "episode_lengths": [e["length"] for e in result["episodes"]],
}
for ep in range(14):
    response = c.get(f"/api/window/{key}/{ep}/0?mode=state")
    assert response.status_code == 200, response.text
report["all_episodes_opened"] = True
window = c.get(f"/api/window/{key}/0/150?mode=state&request_id=23").json()
assert [h["frame"] for h in window["history"]] == [132, 138, 144, 150]
report["history_at_5_seconds"] = window["history"]
assert window["request_id"] == 23


# Cold/hot cache timings are server request timings, not browser render timings.
def fetch_panel():
    start = time.perf_counter()
    for f in [132, 138, 144, 150]:
        for cam in range(3):
            r = c.get(f"/api/frame/{key}/0/{cam}/{f}")
            assert r.status_code == 200, r.text
    return time.perf_counter() - start


jpeg_cache = work / "cache/jpeg"
if jpeg_cache.exists():
    for p in jpeg_cache.glob("*.jpg"):
        p.unlink()
report["cache_definition"] = (
    "应用 JPEG 缓存冷/热；OS 页缓存未清空；import 为新工作项目完整校验"
)
report["panel_cold_seconds"] = fetch_panel()
report["panel_hot_seconds"] = fetch_panel()
t = time.perf_counter()
assert c.get(f"/api/frame/{key}/0/0/180").status_code == 200
report["single_seek_cold_seconds"] = time.perf_counter() - t
t = time.perf_counter()
assert c.get(f"/api/frame/{key}/0/0/180").status_code == 200
report["single_seek_hot_seconds"] = time.perf_counter() - t
for a, b in [(150, 180), (220, 245)]:
    assert (
        c.post(
            "/api/selections", json={"dataset": key, "episode": 0, "start": a, "end": b}
        ).status_code
        == 200
    )
job = c.post("/api/export", json={"dataset": key, "destination": str(dest)}).json()
app.state.exporter.threads[job["id"]].join()
job = c.get("/api/jobs/" + job["id"]).json()
assert job["status"] == "complete", job
report["export"] = job
original = app.state.datasets[key]
output = Dataset(dest, work / "cache/export-reopen")
assert len(output.episodes) == 2
sm = pq.read_table(dest / "annotations/source_map.parquet").to_pylist()
for mapping in sm:
    old = original.rows(mapping["source_episode_index"])[mapping["source_frame_index"]]
    new = output.rows(mapping["episode_index"])[mapping["frame_index"]]
    for col in old:
        if col not in ["index", "frame_index", "episode_index", "timestamp"]:
            assert new[col] == old[col], col
# Compare first, last, interior decoded source/output frames using lossy H264 error.
media = []
for ep in output.episodes:
    e = ep["episode_index"]
    n = ep["length"]
    for f in [0, n // 2, n - 1]:
        mapping = next(
            r for r in sm if r["episode_index"] == e and r["frame_index"] == f
        )
        for cam in output.cameras:
            a = np.asarray(
                original.decode(0, cam, mapping["source_frame_index"]), dtype=float
            )
            b = np.asarray(output.decode(e, cam, f), dtype=float)
            mae = float(np.mean(abs(a - b)))
            assert mae < 12
            media.append(
                {
                    "episode": e,
                    "frame": f,
                    "camera": cam,
                    "source_frame": mapping["source_frame_index"],
                    "mean_absolute_pixel_error": mae,
                }
            )
report["media_comparisons"] = media
moved = root / "exports" / (run + "-moved")
shutil.copytree(dest, moved)
app2 = create_app(root / "workspaces" / (run + "-reopened"))
c2 = TestClient(app2)
r = c2.post("/api/import", json={"path": str(moved)})
assert r.status_code == 200, r.text
wrapper = DSEDataset(moved, work / "cache/adapter")
batch = wrapper.batch([0, 30])
assert batch["images"].shape == (2, 4, 3, 3, 256, 256)
assert "action" not in batch and "ee_pose_cmd" not in batch
assert all(
    not np.any(v) for fields in batch["loss_masks"].values() for v in fields.values()
)
report["batch_images_shape"] = list(batch["images"].shape)
report["semantic_labels"] = (
    "所有真实标签保持 missing；无伪造的人工审核真值。unknown/mask 测试使用合成夹具。"
)
report["portable_reimport"] = True
report["fingerprint_after"] = content_fingerprint(source)[0]
assert report["fingerprint_after"] == before
report["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
report["total_seconds"] = time.perf_counter() - t0
(root / "docs/real-validation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(
    json.dumps(
        {
            k: v
            for k, v in report.items()
            if k not in ["physical_type_warnings", "media_comparisons", "export"]
        },
        ensure_ascii=False,
        indent=2,
    ),
    flush=True,
)
