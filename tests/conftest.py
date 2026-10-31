import copy
import json
from pathlib import Path
import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "meta").mkdir(parents=True)
    n = 60
    fps = 30
    features = {}
    cameras = [
        "observation.images.cam_high",
        "observation.images.cam_left_wrist",
        "observation.images.cam_right_wrist",
    ]
    for c, cam in enumerate(cameras):
        features[cam] = {
            "dtype": "video",
            "shape": [24, 32, 3],
            "names": ["height", "width", "channel"],
            "info": {
                "video.codec": "h264",
                "video.pix_fmt": "yuv420p",
                "video.fps": 30,
                "video.height": 24,
                "video.width": 32,
                "video.channels": 3,
                "video.is_depth_map": False,
            },
        }
        p = root / f"videos/chunk-000/{cam}/episode_000000.mp4"
        p.parent.mkdir(parents=True)
        with av.open(str(p), "w") as out:
            s = out.add_stream("libx264", rate=fps)
            s.width = 32
            s.height = 24
            s.pix_fmt = "yuv420p"
            for i in range(n):
                rgb = np.zeros((24, 32, 3), np.uint8)
                rgb[..., c] = i * 4
                frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
                for packet in s.encode(frame):
                    out.mux(packet)
            for packet in s.encode():
                out.mux(packet)
    for key in ["observation.state", "action"]:
        features[key] = {
            "dtype": "float32",
            "shape": [18],
            "names": ["left_" + str(i) for i in range(7)]
            + ["left_gripper"]
            + ["right_" + str(i) for i in range(7)]
            + ["right_gripper", "head0", "head1"],
        }
    for key in [
        "observation.ee_pose_left",
        "observation.ee_pose_right",
        "observation.ee_pose_cmd_left",
        "observation.ee_pose_cmd_right",
    ]:
        features[key] = {
            "dtype": "float32",
            "shape": [7],
            "names": ["x", "y", "z", "qx", "qy", "qz", "qw"],
        }
    for key in ["frame_index", "timestamp", "episode_index", "index", "task_index"]:
        features[key] = {
            "dtype": "float32" if key == "timestamp" else "int64",
            "shape": [1],
            "names": None,
        }
    rows = []
    for i in range(n):
        row = {
            "observation.state": [float(i + d) for d in range(18)],
            "action": [float(d) for d in range(18)],
            "frame_index": i,
            "timestamp": i / fps,
            "index": i,
            "episode_index": 0,
            "task_index": 0,
        }
        for key in features:
            if "ee_pose" in key:
                row[key] = [i * 0.002, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        rows.append(row)
    p = root / "data/chunk-000/episode_000000.parquet"
    p.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), p)
    info = {
        "codebase_version": "v2.1",
        "robot_type": "test",
        "fps": fps,
        "features": features,
        "total_episodes": 1,
        "total_frames": n,
        "total_tasks": 1,
        "total_chunks": 1,
        "total_videos": 3,
        "chunks_size": 1,
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "splits": {"train": "0:1"},
    }
    for file, value in [("info.json", info), ("stats.json", {})]:
        (root / "meta" / file).write_text(json.dumps(value))
    for file, value in [
        ("tasks.jsonl", {"task_index": 0, "task": "Synthetic test fixture"}),
        (
            "episodes.jsonl",
            {"episode_index": 0, "length": n, "tasks": ["Synthetic test fixture"]},
        ),
        ("episodes_stats.jsonl", {"episode_index": 0, "stats": {}}),
    ]:
        (root / "meta" / file).write_text(json.dumps(value) + "\n")
    return root
