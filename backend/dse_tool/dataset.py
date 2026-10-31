import hashlib
import json
import subprocess
from collections import OrderedDict
from pathlib import Path
import uuid
import threading
from functools import wraps
import av
import numpy as np
import pyarrow.parquet as pq
from .history import sample_history
from .history import HistoryConfig


def read_jsonl(path):
    return [
        json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()
    ]


def content_fingerprint(root):
    h = hashlib.sha256()
    signatures = {}
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file():
            continue
        if p.is_symlink():
            raise ValueError("来源不允许 symlink: " + str(p))
        rel = str(p.relative_to(root))
        h.update(rel.encode())
        with p.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        signatures[rel] = [p.stat().st_size, p.stat().st_mtime_ns]
    return h.hexdigest(), signatures


def verify_manifest(root, annotations_only=False):
    root = Path(root).resolve()
    path = root / "annotations/manifest.json"
    if not path.exists():
        return
    manifest = json.loads(path.read_text())
    for relative, expected in manifest.get("checksums", {}).items():
        if annotations_only and not relative.startswith("annotations/"):
            continue
        file = (root / relative).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise ValueError("导出清单引用非法或缺失文件: " + relative)
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError("导出文件校验和不一致: " + relative)


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)

    return wrapped


class Dataset:
    def __init__(self, root, cache):
        self.root = Path(root).resolve()
        self.cache = Path(cache).resolve()
        if self.cache.is_relative_to(self.root):
            raise ValueError("媒体索引/缓存不得写入来源目录")
        self.cache.mkdir(parents=True, exist_ok=True)
        self.info = json.loads((self.root / "meta/info.json").read_text())
        if self.info.get("codebase_version") != "v2.1":
            raise ValueError(
                "首版仅支持 LeRobot v2.1；拒绝 "
                + str(self.info.get("codebase_version"))
            )
        self.episodes = read_jsonl(self.root / "meta/episodes.jsonl")
        self.tasks = read_jsonl(self.root / "meta/tasks.jsonl")
        self.stats = json.loads((self.root / "meta/stats.json").read_text())
        self.episode_stats = read_jsonl(self.root / "meta/episodes_stats.jsonl")
        self.cameras = sorted(
            [k for k, v in self.info["features"].items() if v["dtype"] == "video"],
            key=lambda k: (
                0 if "high" in k else 1 if "left" in k else 2 if "right" in k else 3,
                k,
            ),
        )
        if not self.cameras:
            raise ValueError("数据没有视频相机")
        self.lock = threading.RLock()
        self.tables = OrderedDict()
        self.maps = OrderedDict()
        self.source_map = None
        manifest_path = self.root / "annotations/manifest.json"
        manifest = (
            json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        )
        self.history_sampling = (
            HistoryConfig.from_dict(manifest.get("history_sampling")).to_dict()
            if manifest is not None
            else None
        )
        self.history_capacity = (
            manifest.get("context_seconds", {}).get("history", 0.6)
            if manifest is not None
            else None
        )
        if (self.root / "annotations/source_map.parquet").exists():
            self.source_map = {
                (r["episode_index"], r["frame_index"]): r
                for r in pq.read_table(
                    self.root / "annotations/source_map.parquet"
                ).to_pylist()
            }

    def path(self, kind, episode, camera=None):
        relative = self.info[kind + "_path"].format(
            episode_index=episode,
            episode_chunk=episode // self.info.get("chunks_size", 1000),
            video_key=camera,
        )
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("路径穿越被拒绝")
        return path

    @synchronized
    def table(self, episode):
        if episode not in self.tables:
            self.tables[episode] = pq.read_table(self.path("data", episode))
            if len(self.tables) > 3:
                self.tables.popitem(last=False)
        self.tables.move_to_end(episode)
        return self.tables[episode]

    def rows(self, episode):
        return self.table(episode).to_pylist()

    @synchronized
    def mapping(self, episode, camera):
        key = (episode, camera)
        if key in self.maps:
            self.maps.move_to_end(key)
            return self.maps[key]
        path = self.path("video", episode, camera)
        if not path.exists():
            raise ValueError("缺少视频: " + str(path))
        stamp = hashlib.sha256(
            (
                "packet-v2"
                + str(path)
                + str(path.stat().st_mtime_ns)
                + str(self.path("data", episode).stat().st_mtime_ns)
            ).encode()
        ).hexdigest()
        cache = self.cache / (stamp + ".json")
        if cache.exists():
            mapping = json.loads(cache.read_text())
        else:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_packets",
                    "-show_entries",
                    "packet=pts_time,flags",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            packets = json.loads(result.stdout)["packets"]
            frames = sorted(
                [
                    {
                        "best_effort_timestamp_time": f["pts_time"],
                        "key_frame": int("K" in f.get("flags", "")),
                    }
                    for f in packets
                    if "pts_time" in f
                ],
                key=lambda f: float(f["best_effort_timestamp_time"]),
            )
            pts = np.asarray([float(f["best_effort_timestamp_time"]) for f in frames])
            if len(pts) == 0 or np.any(np.diff(pts) <= 0):
                raise ValueError("视频 PTS 缺失/不单调")
            times = np.asarray(self.table(episode)["timestamp"].to_pylist())
            nearest = np.searchsorted(pts, times)
            nearest = np.clip(nearest, 0, len(pts) - 1)
            prev = np.maximum(nearest - 1, 0)
            nearest = np.where(
                abs(pts[prev] - times) < abs(pts[nearest] - times), prev, nearest
            )
            error = np.abs(pts[nearest] - times)
            if np.any(error > 0.51 / self.info["fps"]) or len(
                set(nearest.tolist())
            ) != len(times):
                raise ValueError("无法可靠匹配数据行与视频 PTS（时间原点/容差/帧重复）")
            mapping = {
                "ordinals": nearest.tolist(),
                "pts": pts[nearest].tolist(),
                "max_error": float(error.max()),
                "keyframes": [i for i, f in enumerate(frames) if f.get("key_frame")],
                "frame_count": len(frames),
            }
            cache.write_text(json.dumps(mapping))
            files = sorted(self.cache.glob("*.json"), key=lambda p: p.stat().st_mtime)
            size = sum(p.stat().st_size for p in files)
            for old in files:
                if size <= 64 * 1024 * 1024:
                    break
                if old == cache:
                    continue
                size -= old.stat().st_size
                old.unlink()
        self.maps[key] = mapping
        if len(self.maps) > 9:
            self.maps.popitem(last=False)
        return mapping

    def decode(self, episode, camera, frame):
        m = self.mapping(episode, camera)
        target = m["ordinals"][frame]
        # Seeking by an actual keyframe PTS, then checking presentation-order ordinal.
        with av.open(str(self.path("video", episode, camera))) as c:
            stream = c.streams.video[0]
            stream.thread_type = "AUTO"
            key = max((i for i in m["keyframes"] if i <= target), default=0)
            c.seek(
                int(m["pts"][0] / float(stream.time_base))
                if key == 0
                else int(
                    float(self._ordinal_pts(episode, camera, key))
                    / float(stream.time_base)
                ),
                stream=stream,
                backward=True,
            )
            desired = m["pts"][frame]
            for decoded in c.decode(stream):
                if decoded.pts is None:
                    continue
                t = float(decoded.pts * stream.time_base)
                if abs(t - desired) < 1e-5:
                    return decoded.to_image()
                if t > desired + 1e-5:
                    break
        raise ValueError("实际解码未找到所映射 PTS，禁止替换相邻帧")

    def _ordinal_pts(self, episode, camera, ordinal):
        m = self.mapping(episode, camera)
        try:
            return m["pts"][m["ordinals"].index(ordinal)]
        except ValueError:
            return 0

    def history_samples(self, episode, frame, config=None):
        config = HistoryConfig.from_dict(config)
        if (
            self.history_capacity is not None
            and config.horizon_seconds > self.history_capacity + 1e-8
        ):
            raise ValueError(
                f"来源仅保留{self.history_capacity}秒前史；请从原始数据重新导出更长前史"
            )
        return sample_history(
            self.table(episode)["timestamp"].to_pylist(), frame, config
        )

    def history(self, episode, frame, config=None):
        return [r["frame"] for r in self.history_samples(episode, frame, config)]

    def future(self, episode, frame, horizon=0.25):
        """Behavior reference only: actual rows no later than each future target."""
        times = np.asarray(self.table(episode)["timestamp"].to_pylist())
        now = times[frame]
        result = []
        for offset in [horizon * 0.4, horizon * 0.8, horizon]:
            target = now + offset
            index = int(np.searchsorted(times, target + 1e-8, side="right") - 1)
            valid = target <= times[-1] + 1e-8 and frame < index < len(times)
            result.append(
                {
                    "offset_seconds": offset,
                    "frame": index if valid else None,
                    "timestamp": float(times[index]) if valid else None,
                }
            )
        return result

    def validate(self, decode=True):
        warnings = []
        seen = set()
        total = 0
        episodes = []
        if (
            len({t["task_index"] for t in self.tasks}) != len(self.tasks)
            or len(self.tasks) != self.info["total_tasks"]
        ):
            raise ValueError("任务数量/唯一索引错误")
        if {e["episode_index"] for e in self.episode_stats} != {
            e["episode_index"] for e in self.episodes
        }:
            raise ValueError("episodes_stats 与 episodes 不对应")
        for ep in self.episodes:
            i = ep["episode_index"]
            table = self.table(i)
            rows = table.to_pylist()
            n = len(rows)
            total += n
            if n != ep["length"] or not n:
                raise ValueError(f"episode {i}: 长度错误")
            if [r["frame_index"] for r in rows] != list(range(n)):
                raise ValueError("frame_index 必须从0连续")
            ts = np.asarray([r["timestamp"] for r in rows])
            if abs(ts[0]) > 1e-5 or np.any(np.diff(ts) <= 0):
                raise ValueError("时间戳原点或单调性错误")
            for r in rows:
                if r["episode_index"] != i or r["index"] in seen:
                    raise ValueError("episode/index 不一致或重复")
                seen.add(r["index"])
                if r["task_index"] not in [t["task_index"] for t in self.tasks]:
                    raise ValueError("任务索引不存在")
            for key, f in self.info["features"].items():
                if f["dtype"] == "video":
                    if not self.path("video", i, key).is_file():
                        raise ValueError("缺少视频: " + key)
                    if decode:
                        self.decode(i, key, 0)
                    continue
                if key not in table.column_names:
                    raise ValueError("缺少特征: " + key)
                names = f.get("names")
                if names and isinstance(names, dict):
                    names = next(iter(names.values()))
                if names and len(names) != f["shape"][0]:
                    raise ValueError("features.names 维度不匹配: " + key)
                if f.get("names"):
                    expected = int(np.prod(f["shape"]))
                    if any(
                        not isinstance(r[key], list)
                        or np.asarray(r[key]).size != expected
                        for r in rows
                    ):
                        raise ValueError("数值特征维度错误: " + key)
                actual = str(table[key].type)
                if f["dtype"].startswith("float") and (
                    "double" in actual and f["dtype"] == "float32"
                ):
                    warnings.append(
                        {
                            "episode": i,
                            "field": key,
                            "declared": f["dtype"],
                            "physical": actual,
                        }
                    )
            episodes.append(
                {
                    "episode_index": i,
                    "length": n,
                    "task": ep.get("tasks", []),
                    "warnings": [],
                }
            )
        if (
            total != self.info["total_frames"]
            or len(episodes) != self.info["total_episodes"]
        ):
            raise ValueError("meta 总数不一致")
        return {"episodes": episodes, "frames": total, "warnings": warnings}

    def source_key(self, episode, frame, fingerprint):
        if self.source_map:
            r = self.source_map[(episode, frame)]
            return r["source_episode_uuid"], r["source_frame_index"], r["split_group"]
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, fingerprint + ":" + str(episode)))
        return key, frame, key
