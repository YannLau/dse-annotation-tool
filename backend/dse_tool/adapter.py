"""DSE wrapper joins stable source keys; native LeRobot does not consume DSE labels."""

import json
import random
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
from .dataset import Dataset, verify_manifest
from .schema import resolve, validate_schema
from .history import HistoryConfig


class DSEDataset:
    def __init__(self, root, cache, size=256, *, history_config=None):
        self.root = Path(root)
        verify_manifest(self.root, annotations_only=True)
        manifest = json.loads((self.root / "annotations/manifest.json").read_text())
        self.history_config = HistoryConfig.from_dict(
            history_config
            if history_config is not None
            else manifest.get("history_sampling")
        )
        capacity = manifest.get("context_seconds", {}).get("history", 0.6)
        if self.history_config.horizon_seconds > capacity + 1e-8:
            raise ValueError(
                f"请求{self.history_config.horizon_seconds}秒历史，但导出仅保留{capacity}秒；请从原数据重新导出更长前史"
            )
        self.dataset = Dataset(root, cache)
        self.size = size
        self.schema = json.loads((self.root / "annotations/schema.json").read_text())
        validate_schema(self.schema)
        self.samples = pq.read_table(
            self.root / "annotations/samples.parquet"
        ).to_pylist()
        self.centers = [r for r in self.samples if r["center_is_selected"]]
        self.labels = {}
        for r in pq.read_table(self.root / "annotations/labels.parquet").to_pylist():
            self.labels.setdefault((r["episode_index"], r["frame_index"]), []).append(r)
        sample_by_frame = {
            (r["episode_index"], r["frame_index"]): r for r in self.samples
        }
        for key, records in self.labels.items():
            raw = {"left": {}, "right": {}, "global": {}}
            for record in records:
                raw[record["arm"]][record["field_id"]] = record
            effective = resolve(
                self.schema, raw, sample_by_frame[key]["center_is_selected"]
            )
            for record in records:
                checked = effective[record["arm"]][record["field_id"]]
                if (checked["applicability"], checked["effective_loss_mask"]) != (
                    record["applicability"],
                    record["effective_loss_mask"],
                ):
                    raise ValueError("导出标签门控/mask 与共享 schema 语义不一致")
        self.cameras = sorted(
            self.dataset.cameras,
            key=lambda x: 0 if "high" in x else 1 if "left" in x else 2,
        )
        if len(self.cameras) != 3:
            raise ValueError("DSE 默认适配器需要3相机；请明确配置模型相机布局")

    def __len__(self):
        return len(self.centers)

    def __getitem__(self, index):
        sample = self.centers[index]
        e = sample["episode_index"]
        i = sample["frame_index"]
        history_samples = self.dataset.history_samples(e, i, self.history_config)
        history = [r["frame"] for r in history_samples]
        steps = self.history_config.num_frames
        rows = self.dataset.rows(e)
        images = np.zeros((steps, 3, 3, self.size, self.size), np.float32)
        mask = np.zeros((steps, 3), bool)
        state = np.zeros((steps, len(rows[i]["observation.state"])), np.float64)
        ee = np.zeros((steps, 2, 7), np.float64)
        ee_valid = np.zeros((steps, 2), bool)
        for t, frame in enumerate(history):
            if frame is None:
                continue
            state[t] = rows[frame]["observation.state"]
            for arm_idx, arm in enumerate(["left", "right"]):
                key = "observation.ee_pose_" + arm
                if key in rows[frame]:
                    ee[t, arm_idx] = rows[frame][key]
                    ee_valid[t, arm_idx] = True
            for c, camera in enumerate(self.cameras):
                image = (
                    self.dataset.decode(e, camera, frame)
                    .convert("RGB")
                    .resize((self.size, self.size))
                )
                images[t, c] = np.asarray(image).transpose(2, 0, 1) / 255.0
                mask[t, c] = True
        labels = {a: {} for a in ["left", "right", "global"]}
        loss = {a: {} for a in labels}
        for r in self.labels[(e, i)]:
            labels[r["arm"]][r["field_id"]] = (
                r["value_id"] if r["value_id"] is not None else -100
            )
            loss[r["arm"]][r["field_id"]] = r["effective_loss_mask"]
        task = next(
            t["task"]
            for t in self.dataset.tasks
            if t["task_index"] == rows[i]["task_index"]
        )
        return {
            "images": images,
            "image_valid_mask": mask,
            "state": state,
            "state_valid_mask": np.asarray([f is not None for f in history]),
            "ee_pose_feedback": ee,
            "ee_pose_valid_mask": ee_valid,
            "history_frame_indices": np.asarray(
                [f if f is not None else -1 for f in history], dtype=np.int64
            ),
            "history_target_offsets": -np.asarray(
                self.history_config.offsets, dtype=np.float64
            ),
            "history_actual_offsets": np.asarray(
                [
                    r["relative_timestamp"] if r["frame"] is not None else np.nan
                    for r in history_samples
                ],
                dtype=np.float64,
            ),
            "history_config": self.history_config.to_dict(),
            "task": task,
            "labels": labels,
            "loss_masks": loss,
            "source_keys": {
                k: sample[k]
                for k in ["source_episode_uuid", "source_frame_index", "split_group"]
            }
            | {"schema_version": self.schema["version"]},
            "coordinates_verified": False,
        }

    def batch(self, indices):
        items = [self[i] for i in indices]
        result = {
            k: np.stack([r[k] for r in items])
            for k in [
                "images",
                "image_valid_mask",
                "state",
                "state_valid_mask",
                "ee_pose_feedback",
                "ee_pose_valid_mask",
                "history_frame_indices",
                "history_target_offsets",
                "history_actual_offsets",
            ]
        }
        for key in ["task", "source_keys"]:
            result[key] = [r[key] for r in items]
        for key in ["labels", "loss_masks"]:
            result[key] = {
                a: {
                    f: np.asarray([r[key][a][f] for r in items], dtype=np.int64)
                    for f in items[0][key][a]
                }
                for a in ["left", "right", "global"]
            }
        result["coordinates_verified"] = False
        result["history_config"] = self.history_config.to_dict()
        return result

    def sample_indices(self, count, seed=0):
        weights = [r["sampling_weight"] for r in self.centers]
        if not any(weights):
            raise ValueError("所选中心无任何有效监督，不抽样")
        return random.Random(seed).choices(
            range(len(self.centers)), weights=weights, k=count
        )


def proprioception36(state, ee_pose, config):
    """18D state + (position3, rotation first two columns6) × 2."""
    if (
        not config.get("verified")
        or config.get("unit") != "m"
        or config.get("frame") != "robot_base"
    ):
        raise ValueError("规范坐标/单位未验证，请保留 raw 数据")
    state = np.asarray(state)
    poses = np.asarray(ee_pose)
    if state.shape[-1] != 18 or poses.shape[-2:] != (2, 7):
        raise ValueError("36D接口需要18维state和双臂7维反馈")
    transform = np.asarray(config["transform"], dtype=float)
    if (
        transform.shape != (4, 4)
        or not np.allclose(
            transform[:3, :3] @ transform[:3, :3].T, np.eye(3), atol=1e-5
        )
        or not np.isclose(np.linalg.det(transform[:3, :3]), 1)
    ):
        raise ValueError("无效刚体变换")
    result = [state]
    for arm in range(2):
        p = poses[..., arm, :3] @ transform[:3, :3].T + transform[:3, 3]
        q = poses[..., arm, 3:]
        norm = np.linalg.norm(q, axis=-1, keepdims=True)
        if np.any(norm < 1e-8):
            raise ValueError("无效四元数")
        x, y, z, w = np.moveaxis(q / norm, -1, 0)
        col1 = (
            np.stack(
                [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
                axis=-1,
            )
            @ transform[:3, :3].T
        )
        col2 = (
            np.stack(
                [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
                axis=-1,
            )
            @ transform[:3, :3].T
        )
        result.extend([p, col1, col2])
    return np.concatenate(result, axis=-1)


def training_statistics(dataset, split_groups):
    """Estimate continuous state normalization only on explicitly supplied training groups."""
    values = []
    seen = set()
    for sample in dataset.centers:
        key = (sample["source_episode_uuid"], sample["source_frame_index"])
        if sample["split_group"] not in split_groups or key in seen:
            continue
        seen.add(key)
        values.append(
            dataset.dataset.rows(sample["episode_index"])[sample["frame_index"]][
                "observation.state"
            ]
        )
    if not values:
        raise ValueError("训练划分为空")
    x = np.asarray(values)
    return {
        "mean": x.mean(0),
        "std": x.std(0),
        "count": len(x),
        "split_groups": sorted(split_groups),
    }


def encode_class_targets(labels, schema):
    """Map stable, potentially sparse category IDs to head logit positions."""
    fields = {field["field_id"]: field for field in schema["fields"]}
    encoded = {}
    for arm, targets in labels.items():
        encoded[arm] = {}
        for key, values in targets.items():
            order = {c["id"]: i for i, c in enumerate(fields[key]["classes"])}
            array = np.asarray(values)
            output = np.full(array.shape, -100, dtype=np.int64)
            for value in np.unique(array):
                if value == -100:
                    continue
                if value not in order:
                    raise ValueError("类别 ID 不属于当前分类头: " + key)
                output[array == value] = order[value]
            encoded[arm][key] = output
    return encoded


def to_torch_batch(value, device="cpu"):
    """Optional training helper; importing the web app never imports torch."""
    import torch

    if isinstance(value, np.ndarray):
        return torch.from_numpy(value).to(device)
    if isinstance(value, dict):
        return {key: to_torch_batch(item, device) for key, item in value.items()}
    if isinstance(value, list):
        return [to_torch_batch(item, device) for item in value]
    return value
