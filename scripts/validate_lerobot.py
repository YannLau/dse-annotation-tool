"""Run with .validation/bin/python; uses unmodified official LeRobotDataset 0.3.3."""

import os
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ["HF_HOME"] = str(root / "workspaces/native-validation/huggingface")
os.environ["HF_DATASETS_CACHE"] = str(
    root / "workspaces/native-validation/huggingface/datasets"
)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
from importlib.metadata import version
from lerobot.datasets.lerobot_dataset import LeRobotDataset, CODEBASE_VERSION

assert version("lerobot") == "0.3.3" and CODEBASE_VERSION == "v2.1"
path = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else root / "exports/real-two-segments-moved"
)
d = LeRobotDataset("local/dse-acceptance", root=path, video_backend="pyav")
indices = [0, len(d) // 2, len(d) - 1]
frames = []
for index in indices:
    row = d[index]
    frames.append(
        {
            "index": index,
            "features": {
                k: list(v.shape) if hasattr(v, "shape") else str(v)
                for k, v in row.items()
            },
        }
    )
report = {
    "loader": "lerobot.datasets.lerobot_dataset.LeRobotDataset",
    "lerobot_version": version("lerobot"),
    "dataset_version": CODEBASE_VERSION,
    "torch_version": version("torch"),
    "datasets_version": version("datasets"),
    "video_backend": "pyav",
    "root": str(path),
    "rows": len(d),
    "episodes": d.num_episodes,
    "checked_rows": frames,
    "offline": True,
}
(root / "docs/native-validation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False, indent=2))
