"""Read-only inspection of published files, independent of the annotation database."""

from collections import defaultdict
from pathlib import Path
import json
import math

import pyarrow.parquet as pq
from .dataset import Dataset, verify_manifest
from .history import HistoryConfig


def role(sample):
    if sample["center_is_selected"]:
        return "center"
    if sample["is_history_context"]:
        return "history"
    if sample["is_review_context"]:
        return "review"
    return "context"


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


class ExportInspector:
    def __init__(self, root, cache):
        self.root = Path(root).expanduser().resolve()
        required = [
            "manifest.json",
            "schema.json",
            "samples.parquet",
            "source_map.parquet",
            "labels.parquet",
        ]
        if not all((self.root / "annotations" / name).is_file() for name in required):
            raise ValueError(
                "请选择已导出的 LeRobot+DSE 数据集目录（需要 annotations/manifest.json、schema 和三张 Parquet 表）"
            )
        verify_manifest(self.root)  # Verify the actual published data, including media.
        self.manifest = json.loads(
            (self.root / "annotations/manifest.json").read_text()
        )
        if not self.manifest.get("checksums"):
            raise ValueError("导出清单没有文件校验和，无法确认快照完整性；请重新导出")
        self.schema = json.loads((self.root / "annotations/schema.json").read_text())
        self.config = HistoryConfig.from_dict(self.manifest.get("history_sampling"))
        self.dataset = Dataset(self.root, cache)
        self.samples = self.read("samples")
        self.mapping = self.read("source_map")
        self.labels = self.read("labels")
        self.sample_index = self.index(self.samples)
        self.map_index = self.index(self.mapping)
        self.label_index = defaultdict(list)
        self.copies = defaultdict(list)
        self.by_episode = defaultdict(list)
        for row in self.labels:
            self.label_index[(row["episode_index"], row["frame_index"])].append(row)
        if self.sample_index.keys() != self.map_index.keys():
            raise ValueError("samples 与 source_map 的帧键不一致")
        for sample in self.samples:
            key = sample["episode_index"], sample["frame_index"]
            source = self.map_index[key]
            if (sample["source_episode_uuid"], sample["source_frame_index"]) != (
                source["source_episode_uuid"],
                source["source_frame_index"],
            ):
                raise ValueError("samples 与 source_map 的源帧身份不一致")
            self.copies[
                (source["source_episode_uuid"], source["source_frame_index"])
            ].append(key)
            self.by_episode[key[0]].append(sample)
        for ep in self.dataset.episodes:
            e = ep["episode_index"]
            self.by_episode[e].sort(key=lambda row: row["frame_index"])
            if [r["frame_index"] for r in self.by_episode[e]] != list(
                range(ep["length"])
            ):
                raise ValueError("导出 episode 的样本行缺失或不连续")
        self.fields = {f["field_id"]: f for f in self.schema["fields"]}
        self.summary = self.make_summary()
        self.signatures = {
            f["path"]: (
                (self.root / f["path"]).stat().st_size,
                (self.root / f["path"]).stat().st_mtime_ns,
            )
            for f in self.summary["files"]
        }

    def ensure_unchanged(self):
        for relative, signature in self.signatures.items():
            path = self.root / relative
            if (
                not path.is_file()
                or (path.stat().st_size, path.stat().st_mtime_ns) != signature
            ):
                raise ValueError(
                    "导出文件在检查期间发生变化，请重新打开校验: " + relative
                )

    def read(self, name):
        return pq.read_table(
            self.root / "annotations" / (name + ".parquet")
        ).to_pylist()

    @staticmethod
    def index(rows):
        result = {(r["episode_index"], r["frame_index"]): r for r in rows}
        if len(result) != len(rows):
            raise ValueError("Parquet 内 episode/frame 键重复")
        return result

    def frame_summary(self, e, f):
        key = e, f
        if key not in self.sample_index:
            raise ValueError("未知导出 episode/frame")
        s = self.sample_index[key]
        m = self.map_index[key]
        labels = self.label_index[key]
        return dict(
            episode=e,
            frame=f,
            timestamp=m["timestamp"],
            source_episode_uuid=m["source_episode_uuid"],
            source_episode_index=m["source_episode_index"],
            source_frame=m["source_frame_index"],
            source_timestamp=m["source_timestamp"],
            role=role(s),
            center_is_selected=s["center_is_selected"],
            is_history_context=s["is_history_context"],
            is_review_context=s["is_review_context"],
            sampling_weight=s["sampling_weight"],
            valid_fields=sum(r["effective_loss_mask"] for r in labels),
            label_count=len(labels),
            null_labels=sum(r["value_id"] is None for r in labels),
            copies=len(
                self.copies[(m["source_episode_uuid"], m["source_frame_index"])]
            ),
        )

    def make_summary(self):
        episodes, groups = [], {}
        for ep in self.dataset.episodes:
            e = ep["episode_index"]
            rows = self.by_episode[e]
            runs = []
            for row in rows:
                f = row["frame_index"]
                m = self.map_index[(e, f)]
                kind = role(row)
                signature = kind, m["source_episode_uuid"], m["source_frame_index"] - f
                if runs and runs[-1]["signature"] == signature:
                    runs[-1]["end"] = f + 1
                    runs[-1]["source_end"] = m["source_frame_index"] + 1
                else:
                    runs.append(
                        dict(
                            signature=signature,
                            start=f,
                            end=f + 1,
                            role=kind,
                            source_episode_uuid=m["source_episode_uuid"],
                            source_start=m["source_frame_index"],
                            source_end=m["source_frame_index"] + 1,
                        )
                    )
                source = m["source_episode_uuid"]
                group = groups.setdefault(
                    source,
                    dict(
                        uuid=source,
                        source_episode_index=m["source_episode_index"],
                        fingerprint=m["source_dataset_fingerprint"],
                        start=m["source_frame_index"],
                        end=m["source_frame_index"] + 1,
                        lanes={},
                    ),
                )
                group["start"] = min(group["start"], m["source_frame_index"])
                group["end"] = max(group["end"], m["source_frame_index"] + 1)
            for run in runs:
                run.pop("signature")
                group = groups[run["source_episode_uuid"]]
                group["lanes"].setdefault(e, []).append(dict(run))
            centers = [r["frame_index"] for r in rows if r["center_is_selected"]]
            episodes.append(
                dict(
                    episode=e,
                    length=len(rows),
                    center_count=len(centers),
                    context_count=len(rows) - len(centers),
                    first_center=centers[0] if centers else None,
                    runs=runs,
                    split_groups=sorted({r["split_group"] for r in rows}),
                    duplicate_rows=sum(
                        len(
                            self.copies[
                                (
                                    self.map_index[(e, r["frame_index"])][
                                        "source_episode_uuid"
                                    ],
                                    self.map_index[(e, r["frame_index"])][
                                        "source_frame_index"
                                    ],
                                )
                            ]
                        )
                        > 1
                        for r in rows
                    ),
                )
            )
        overlaps = []
        for (source, sf), copies in sorted(self.copies.items()):
            if len(copies) < 2:
                continue
            identity = [
                (e, f - sf, role(self.sample_index[(e, f)])) for e, f in sorted(copies)
            ]
            if (
                overlaps
                and overlaps[-1]["uuid"] == source
                and overlaps[-1]["end"] == sf
                and overlaps[-1]["identity"] == identity
            ):
                overlaps[-1]["end"] = sf + 1
                for copy in overlaps[-1]["copies"]:
                    copy["end"] += 1
            else:
                overlaps.append(
                    dict(
                        uuid=source,
                        start=sf,
                        end=sf + 1,
                        identity=identity,
                        copies=[
                            dict(
                                episode=e,
                                start=f,
                                end=f + 1,
                                role=role(self.sample_index[(e, f)]),
                                split_group=self.sample_index[(e, f)]["split_group"],
                            )
                            for e, f in sorted(copies)
                        ],
                    )
                )
        for group in groups.values():
            group["lanes"] = [
                dict(episode=e, runs=runs) for e, runs in group["lanes"].items()
            ]
        for overlap in overlaps:
            overlap.pop("identity")
        files = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            entry = dict(path=str(p.relative_to(self.root)), bytes=p.stat().st_size)
            if p.suffix == ".parquet":
                meta = pq.read_metadata(p)
                entry.update(
                    rows=meta.num_rows,
                    columns=[
                        dict(name=f.name, type=str(f.type), nullable=f.nullable)
                        for f in pq.read_schema(p)
                    ],
                )
            files.append(entry)
        return dict(
            root=str(self.root),
            tasks=self.dataset.tasks,
            history_config=self.config.to_dict(),
            context_seconds=self.manifest.get("context_seconds"),
            episodes=episodes,
            source_groups=list(groups.values()),
            overlaps=overlaps[:200],
            overlap_group_count=len(overlaps),
            total_rows=len(self.samples),
            center_rows=sum(s["center_is_selected"] for s in self.samples),
            unique_source_frames=len(self.copies),
            duplicate_extra_rows=len(self.samples) - len(self.copies),
            repeated_source_frames=sum(len(c) > 1 for c in self.copies.values()),
            files=files,
            manifest=self.manifest,
            checksum_verified=True,
        )

    def episode(self, e, offset=0, limit=100):
        if e not in self.by_episode:
            raise ValueError("未知导出 episode")
        rows = self.by_episode[e]
        return clean(
            dict(
                total=len(rows),
                offset=offset,
                rows=[
                    self.frame_summary(e, r["frame_index"])
                    for r in rows[offset : offset + limit]
                ],
            )
        )

    def file(self, relative, offset=0, limit=50):
        if relative not in self.signatures:
            raise ValueError("文件不属于当前导出快照")
        path = self.root / relative
        if path.suffix == ".parquet":
            parquet = pq.ParquetFile(path)
            rows, skipped = [], 0
            for group in range(parquet.num_row_groups):
                size = parquet.metadata.row_group(group).num_rows
                if skipped + size <= offset:
                    skipped += size
                    continue
                start = max(0, offset - skipped)
                rows.extend(
                    parquet.read_row_group(group)
                    .slice(start, limit - len(rows))
                    .to_pylist()
                )
                skipped += size
                if len(rows) >= limit:
                    break
            return clean(
                dict(
                    path=relative,
                    kind="parquet",
                    total=parquet.metadata.num_rows,
                    offset=offset,
                    rows=rows,
                )
            )
        if path.suffix == ".json":
            return clean(
                dict(path=relative, kind="json", value=json.loads(path.read_text()))
            )
        if path.suffix == ".jsonl":
            lines = path.read_text().splitlines()
            return clean(
                dict(
                    path=relative,
                    kind="jsonl",
                    total=len(lines),
                    offset=offset,
                    rows=[json.loads(line) for line in lines[offset : offset + limit]],
                )
            )
        raise ValueError("该文件为视频或不支持的格式，请在逐帧画面中查看视频")

    def frame(self, e, f):
        summary = self.frame_summary(e, f)
        data = self.dataset.rows(e)[f]
        sample = self.sample_index[(e, f)]
        mapping = self.map_index[(e, f)]
        labels = []
        for row in self.label_index[(e, f)]:
            field = self.fields.get(row["field_id"], {})
            category = next(
                (c for c in field.get("classes", []) if c["id"] == row["value_id"]),
                None,
            )
            if row["effective_loss_mask"]:
                reason = "该字段在当前训练中心有有效监督"
            elif not sample["center_is_selected"]:
                reason = "仅上下文，不是训练中心；即使有标注也不计损失"
            elif row["value_id"] is None:
                reason = "value_id=null，训练 target=-100，不补成0或unknown"
            elif row["applicability"] != "applicable":
                reason = "字段不适用或适用性未确定"
            elif row["review_status"] != "reviewed":
                reason = "标签尚未审核或需要复核"
            else:
                reason = "保存的 effective_loss_mask=0；请查看原始状态与 schema 版本"
            labels.append(
                dict(
                    raw=row,
                    class_name=(
                        category.get("key")
                        or category.get("label_zh")
                        or category.get("name")
                        or category.get("label")
                        or category.get("label_en")
                        or str(row["value_id"])
                    )
                    if category
                    else None,
                    training_target=row["value_id"]
                    if row["value_id"] is not None
                    else -100,
                    reason=reason,
                )
            )
        histories = []
        if sample["center_is_selected"]:
            for h in self.dataset.history_samples(e, f, self.config):
                histories.append(
                    dict(
                        h,
                        sample=self.frame_summary(e, h["frame"])
                        if h["frame"] is not None
                        else None,
                        image_valid_mask=[h["frame"] is not None]
                        * len(self.dataset.cameras),
                        state_valid_mask=h["frame"] is not None,
                    )
                )
        copies = [
            self.frame_summary(ce, cf)
            for ce, cf in self.copies[
                (mapping["source_episode_uuid"], mapping["source_frame_index"])
            ]
        ]
        comparisons = (
            [
                dict(
                    episode=copy["episode"],
                    frame=copy["frame"],
                    role=copy["role"],
                    labels=self.label_index[(copy["episode"], copy["frame"])],
                )
                for copy in copies
            ]
            if len(copies) > 1
            else []
        )
        return clean(
            dict(
                summary=summary,
                data=data,
                sample=sample,
                source_map=mapping,
                labels=labels,
                equivalents=copies,
                comparisons=comparisons,
                history=histories,
                cameras=self.dataset.cameras,
                data_file=str(self.dataset.path("data", e).relative_to(self.root)),
                training=dict(
                    is_sample=sample["center_is_selected"],
                    image_shape=[
                        self.config.num_frames,
                        len(self.dataset.cameras),
                        3,
                        256,
                        256,
                    ],
                    labels={
                        a: {
                            r["raw"]["field_id"]: r["training_target"]
                            for r in labels
                            if r["raw"]["arm"] == a
                        }
                        for a in ["left", "right", "global"]
                    },
                    loss_masks={
                        a: {
                            r["raw"]["field_id"]: r["raw"]["effective_loss_mask"]
                            for r in labels
                            if r["raw"]["arm"] == a
                        }
                        for a in ["left", "right", "global"]
                    },
                ),
            )
        )
