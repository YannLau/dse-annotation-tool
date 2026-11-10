"""Atomic, cancellable, frame-exact v2.1 publication. No stream-copy trimming."""

import copy
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from fractions import Fraction
import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from .dataset import Dataset, content_fingerprint
from .schema import digest, head_spec
from . import __version__
from .history import HistoryConfig


def write_json(path, obj):
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)
    )


def write_jsonl(path, rows):
    Path(path).write_text(
        "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows)
    )


class Exporter:
    def __init__(self, store):
        self.store = store
        self.cancels = {}
        self.threads = {}

    def start(
        self,
        dataset,
        fingerprint,
        destination,
        key_ratio=0.7,
        *,
        history_config=None,
        history_context_seconds=None,
    ):
        history_config = HistoryConfig.from_dict(history_config)
        history_context_seconds = (
            history_config.horizon_seconds
            if history_context_seconds is None
            else history_context_seconds
        )
        if not history_config.horizon_seconds <= history_context_seconds <= 3600:
            raise ValueError("导出前史须不小于所选历史跨度，且不超过3600秒")
        parent_manifest = dataset.root / "annotations/manifest.json"
        if parent_manifest.exists():
            available = (
                json.loads(parent_manifest.read_text())
                .get("context_seconds", {})
                .get("history", 0.6)
            )
            if history_context_seconds > available + 1e-8:
                raise ValueError(
                    f"当前来源仅保留{available}秒前史；请从原始数据重新导出"
                )
        target = Path(destination).expanduser().resolve()
        source = dataset.root
        if (
            target == source
            or target.is_relative_to(source)
            or source.is_relative_to(target)
        ):
            raise ValueError("导出路径不能与来源目录重叠")
        if target.exists():
            raise ValueError("导出目标已存在，不覆盖")
        if not 0 <= key_ratio <= 1:
            raise ValueError("采样比例必须为0–1")
        with self.store.lock:
            if any(t.is_alive() for t in self.threads.values()):
                raise ValueError("已有导出作业在运行")
            selections = [
                dict(r)
                for r in self.store.db.execute(
                    "SELECT * FROM selections WHERE dataset=? ORDER BY episode,start",
                    (fingerprint,),
                )
            ]
            if not selections:
                raise ValueError("请先选择训练中心区间")
            frozen = self.store.snapshot()
            schema = self.store.schema()
            schema_history = [
                json.loads(r["payload"])
                for r in self.store.db.execute(
                    "SELECT payload FROM schemas ORDER BY version"
                )
            ]
        job = {
            "id": str(uuid.uuid4()),
            "status": "queued",
            "progress": 0,
            "destination": str(target),
            "started": time.time(),
            "error": None,
        }
        cancel = threading.Event()
        self.cancels[job["id"]] = cancel
        self.store.save_job(job)
        t = threading.Thread(
            target=self.run,
            args=(
                dataset,
                fingerprint,
                target,
                selections,
                frozen,
                schema,
                key_ratio,
                job,
                cancel,
                schema_history,
                history_config,
                history_context_seconds,
            ),
            daemon=True,
        )
        self.threads[job["id"]] = t
        t.start()
        return job

    def run(
        self,
        dataset,
        fingerprint,
        target,
        selections,
        frozen,
        schema,
        key_ratio,
        job,
        cancel,
        schema_history=None,
        history_config=None,
        history_context_seconds=None,
    ):
        history_config = HistoryConfig.from_dict(history_config)
        history_context_seconds = (
            history_config.horizon_seconds
            if history_context_seconds is None
            else history_context_seconds
        )
        temporary = target.parent / ("." + target.name + ".partial-" + job["id"])
        begun = time.perf_counter()

        def update(**kw):
            job.update(kw)
            self.store.save_job(job)

        def check():
            if cancel.is_set():
                raise InterruptedError("用户取消")

        try:
            update(status="running")
            check()
            new_fp, _ = content_fingerprint(dataset.root)
            if new_fp != fingerprint:
                raise ValueError("来源内容已改变，重新导入并核对身份后再导出")
            temporary.mkdir(parents=True)
            for name in ["meta", "annotations"]:
                (temporary / name).mkdir()
            # Frozen database state avoids edits racing the background export.
            from .store import Store

            frozen_store = Store(temporary / ".snapshot")
            frozen_store.restore(frozen)
            frozen_store.db.execute("DELETE FROM schemas")
            frozen_store.db.execute(
                "INSERT INTO schemas VALUES(?,?,?)",
                (schema["version"], digest(schema), json.dumps(schema)),
            )
            frozen_store.set("schema_version", schema["version"])
            frozen_store.db.commit()
            info = copy.deepcopy(dataset.info)
            parent_manifest_path = dataset.root / "annotations/manifest.json"
            parent_manifest = (
                json.loads(parent_manifest_path.read_text())
                if parent_manifest_path.exists()
                else {}
            )
            labels = []
            samples = []
            source_map = []
            ep_meta = []
            ep_stats = []
            global_index = 0
            summary = {
                "complete": 0,
                "partial": 0,
                "no_supervision": 0,
                "missing_fields": 0,
                "center_count": 0,
                "context_count": 0,
                "counts": {},
                "warnings": [],
                "segments": [],
            }
            global_numeric = {}
            video_stats = {}
            chunk_size = info.get("chunks_size", 1000)
            task_ids = set()
            for selection in selections:
                task_ids.update(
                    r["task_index"] for r in dataset.rows(selection["episode"])
                )
            task_remap = {old: new for new, old in enumerate(sorted(task_ids))}
            tasks_export = [
                {"task_index": task_remap[t["task_index"]], "task": t["task"]}
                for t in dataset.tasks
                if t["task_index"] in task_remap
            ]
            # Merge contiguous selections, never join across a gap.
            merged = []
            for s in selections:
                if (
                    merged
                    and merged[-1]["episode"] == s["episode"]
                    and s["start"] <= merged[-1]["end"]
                ):
                    merged[-1]["end"] = max(merged[-1]["end"], s["end"])
                else:
                    merged.append(dict(s))
            for new_ep, s in enumerate(merged):
                check()
                old_ep = s["episode"]
                rows = dataset.rows(old_ep)
                times = np.asarray([r["timestamp"] for r in rows])
                start = s["start"]
                end = s["end"]
                # Retain the floor row too, so irregular timestamps still support
                # exactly the same at-or-before sample after independent export.
                low = max(
                    0,
                    int(
                        np.searchsorted(
                            times,
                            times[start] - history_context_seconds + 1e-8,
                            side="right",
                        )
                        - 1
                    ),
                )
                high = int(
                    np.searchsorted(times, times[end - 1] + 0.25 + 1e-8, side="right")
                )
                selected_rows = copy.deepcopy(rows[low:high])
                chunk = new_ep // chunk_size
                summary["segments"].append(
                    {
                        "source_episode": old_ep,
                        "center": [start, end],
                        "retained": [low, high],
                        "new_episode": new_ep,
                    }
                )
                per_stats = {}
                for j, r in enumerate(selected_rows):
                    original = rows[low + j]
                    r["task_index"] = task_remap[original["task_index"]]
                    r.update(
                        episode_index=new_ep,
                        frame_index=j,
                        index=global_index + j,
                        timestamp=original["timestamp"] - times[low],
                    )
                    source, source_frame, group = dataset.source_key(
                        old_ep, low + j, fingerprint
                    )
                    center = start <= low + j < end
                    effective = frozen_store.labels(source, source_frame, center)
                    phase_values = [
                        effective[a]["phase"]["value_id"] for a in ["left", "right"]
                    ]
                    event_rows = frozen_store.db.execute(
                        "SELECT frame FROM events WHERE source=? AND status='confirmed'",
                        (source,),
                    ).fetchall()
                    event_near = any(
                        abs(e["frame"] - source_frame) <= round(0.25 * info["fps"])
                        for e in event_rows
                    )
                    event_group = (
                        source
                        + ":event:"
                        + str(
                            min(
                                event_rows, key=lambda e: abs(e["frame"] - source_frame)
                            )["frame"]
                        )
                        if event_near
                        else source + ":phase:" + str(phase_values)
                    )
                    key = any(p in [2, 3, 5, 7] for p in phase_values) or event_near
                    valid = 0
                    required = 0
                    missing = 0
                    for arm, fields in effective.items():
                        for field, record in fields.items():
                            value = record["value_id"]
                            mask = record["effective_loss_mask"]
                            valid += mask
                            required += int(record["required"] is True)
                            missing += int(record["required"] is True and mask == 0)
                            labels.append(
                                {
                                    "episode_index": new_ep,
                                    "frame_index": j,
                                    "source_episode_uuid": source,
                                    "source_frame_index": source_frame,
                                    "arm": arm,
                                    "field_id": field,
                                    **{
                                        k: record.get(k)
                                        for k in [
                                            "value_id",
                                            "applicability",
                                            "annotation_status",
                                            "review_status",
                                            "evidence_mode",
                                            "label_source",
                                            "schema_version",
                                            "revision",
                                            "future_seen",
                                        ]
                                    },
                                    "effective_loss_mask": mask,
                                    "provenance": json.dumps(
                                        record.get("provenance", {})
                                    ),
                                }
                            )
                            if center:
                                count_key = f"{arm}.{field}:{value if value is not None else 'missing'}"
                                summary["counts"][count_key] = (
                                    summary["counts"].get(count_key, 0) + 1
                                )
                    next_center = max(start, low + j + 1)
                    earliest_history = max(
                        0,
                        int(
                            np.searchsorted(
                                times,
                                times[min(next_center, end - 1)]
                                - history_context_seconds
                                + 1e-8,
                                side="right",
                            )
                            - 1
                        ),
                    )
                    is_history = (
                        history_context_seconds > 0
                        and next_center < end
                        and low + j >= earliest_history
                    )
                    is_future = low + j >= end
                    samples.append(
                        {
                            "key_zone_left": None
                            if phase_values[0] in [None, 8]
                            else int(phase_values[0] in [2, 5]),
                            "key_zone_right": None
                            if phase_values[1] in [None, 8]
                            else int(phase_values[1] in [2, 5]),
                            "episode_index": new_ep,
                            "frame_index": j,
                            "center_is_selected": center,
                            "is_history_context": is_history,
                            "is_review_context": is_future,
                            "source_episode_uuid": source,
                            "source_frame_index": source_frame,
                            "split_group": group,
                            "event_group": event_group,
                            "pool": "key" if key else "background",
                            "valid_fields": valid,
                            "sampling_weight": 0.0,
                            "boundary": any(
                                abs(low + j - x) <= 1 for x in [start, end - 1]
                            ),
                        }
                    )
                    ancestor = (dataset.source_map or {}).get((old_ep, low + j), {})
                    source_map.append(
                        {
                            "episode_index": new_ep,
                            "frame_index": j,
                            "index": global_index + j,
                            "timestamp": r["timestamp"],
                            "source_episode_uuid": source,
                            "source_frame_index": source_frame,
                            "source_episode_index": ancestor.get(
                                "source_episode_index", old_ep
                            ),
                            "source_dataset_fingerprint": ancestor.get(
                                "source_dataset_fingerprint",
                                parent_manifest.get("source_fingerprint", fingerprint),
                            ),
                            "source_timestamp": ancestor.get(
                                "source_timestamp", original["timestamp"]
                            ),
                            "source_index": ancestor.get(
                                "source_index", original["index"]
                            ),
                            "parent_episode_index": old_ep,
                            "parent_frame_index": low + j,
                            "parent_timestamp": original["timestamp"],
                            "split_group": group,
                            "media_mapping": ancestor.get("media_mapping")
                            or json.dumps(
                                {
                                    cam: {
                                        "source_ordinal": dataset.mapping(old_ep, cam)[
                                            "ordinals"
                                        ][low + j],
                                        "source_pts": dataset.mapping(old_ep, cam)[
                                            "pts"
                                        ][low + j],
                                    }
                                    for cam in dataset.cameras
                                }
                            ),
                        }
                    )
                    if center:
                        summary["center_count"] += 1
                        summary["missing_fields"] += missing
                        summary[
                            "complete"
                            if valid and missing == 0
                            else "partial"
                            if valid
                            else "no_supervision"
                        ] += 1
                    else:
                        summary["context_count"] += 1
                original_table = dataset.table(old_ep)
                columns = {}
                for col in original_table.column_names:
                    typ = original_table[col].type
                    columns[col] = pa.array([r[col] for r in selected_rows], type=typ)
                    if col == "timestamp":
                        info["features"][col]["dtype"] = (
                            "float64" if pa.types.is_float64(typ) else "float32"
                        )
                    f = info["features"].get(col, {})
                    scalar = (
                        typ.value_type
                        if pa.types.is_list(typ) or pa.types.is_fixed_size_list(typ)
                        else typ
                    )
                    if pa.types.is_float64(scalar):
                        f["dtype"] = "float64"
                    elif pa.types.is_float32(scalar):
                        f["dtype"] = "float32"
                    arr = np.asarray([r[col] for r in selected_rows], dtype=float)
                    if arr.ndim == 1:
                        arr = arr[:, None]
                    per_stats[col] = {
                        "min": arr.min(0).tolist(),
                        "max": arr.max(0).tolist(),
                        "mean": arr.mean(0).tolist(),
                        "std": arr.std(0).tolist(),
                        "count": [len(arr)],
                    }
                    global_numeric.setdefault(col, []).append(arr)
                data_path = temporary / info["data_path"].format(
                    episode_chunk=chunk, episode_index=new_ep
                )
                data_path.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(pa.table(columns), data_path)
                for cam_idx, cam in enumerate(dataset.cameras):
                    check()
                    out_path = temporary / info["video_path"].format(
                        episode_chunk=chunk, episode_index=new_ep, video_key=cam
                    )
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    m = dataset.mapping(old_ep, cam)
                    ordinals = m["ordinals"][low:high]
                    source_pts = m["pts"][low:high]
                    count = 0
                    channel_sum = np.zeros(3)
                    channel_sq = np.zeros(3)
                    channel_min = np.ones(3)
                    channel_max = np.zeros(3)
                    pixels = 0
                    with (
                        av.open(str(dataset.path("video", old_ep, cam))) as src,
                        av.open(str(out_path), "w") as out,
                    ):
                        video = src.streams.video[0]
                        video.thread_type = "AUTO"
                        enc = out.add_stream("libx264", rate=info["fps"])
                        enc.width = video.width
                        enc.height = video.height
                        enc.pix_fmt = "yuv420p"
                        enc.options = {"crf": "18", "preset": "fast"}
                        for ordinal, frame in enumerate(src.decode(video)):
                            if ordinal < ordinals[0]:
                                continue
                            if ordinal > ordinals[-1]:
                                break
                            check()
                            if ordinal != ordinals[count]:
                                raise ValueError("视频 PTS 映射包含缺帧，停止导出")
                            if (
                                abs(
                                    float(frame.pts * video.time_base)
                                    - source_pts[count]
                                )
                                > 1e-5
                            ):
                                raise ValueError("导出解码 PTS 与索引不一致")
                            frame.pts = count
                            frame.time_base = Fraction(1, info["fps"])
                            for packet in enc.encode(frame):
                                out.mux(packet)
                            count += 1
                        for packet in enc.encode():
                            out.mux(packet)
                    if count != len(selected_rows):
                        raise ValueError("输出视频帧数与数据行不一致")
                    # Stats describe the encoded, published media (lossy transcoding changes pixels).
                    channel_sum = np.zeros(3)
                    channel_sq = np.zeros(3)
                    channel_min = np.ones(3)
                    channel_max = np.zeros(3)
                    pixels = 0
                    with av.open(str(out_path)) as published:
                        for frame in published.decode(video=0):
                            flat = (frame.to_ndarray(format="rgb24") / 255.0).reshape(
                                -1, 3
                            )
                            pixels += len(flat)
                            channel_sum += flat.sum(0)
                            channel_sq += (flat * flat).sum(0)
                            channel_min = np.minimum(channel_min, flat.min(0))
                            channel_max = np.maximum(channel_max, flat.max(0))
                    mean = channel_sum / pixels
                    std = np.sqrt(np.maximum(0, channel_sq / pixels - mean * mean))

                    def shape(v):
                        return np.asarray(v).reshape(3, 1, 1).tolist()

                    per_stats[cam] = {
                        "min": shape(channel_min),
                        "max": shape(channel_max),
                        "mean": shape(mean),
                        "std": shape(std),
                        "count": [count],
                    }
                    video_stats.setdefault(cam, []).append(
                        (
                            channel_sum,
                            channel_sq,
                            pixels,
                            channel_min,
                            channel_max,
                            count,
                        )
                    )
                    info["features"][cam]["info"].update(
                        {
                            "video.codec": "h264",
                            "video.pix_fmt": "yuv420p",
                            "video.fps": info["fps"],
                        }
                    )
                    update(
                        progress=round(
                            (
                                (new_ep + (cam_idx + 1) / len(dataset.cameras))
                                / len(merged)
                            )
                            * 0.85,
                            3,
                        )
                    )
                tasks = list(
                    dict.fromkeys(
                        next(
                            t["task"]
                            for t in tasks_export
                            if t["task_index"] == r["task_index"]
                        )
                        for r in selected_rows
                    )
                )
                ep_meta.append(
                    {
                        "episode_index": new_ep,
                        "length": len(selected_rows),
                        "tasks": tasks,
                    }
                )
                ep_stats.append({"episode_index": new_ep, "stats": per_stats})
                global_index += len(selected_rows)
            check()
            # Normalize sampling within pools and groups, independent of semantic loss masks.
            eligible = [
                r for r in samples if r["center_is_selected"] and r["valid_fields"] > 0
            ]
            pools = {
                p: [r for r in eligible if r["pool"] == p]
                for p in ["key", "background"]
            }
            for p, rs in pools.items():
                if not rs:
                    summary["warnings"].append("无可用 " + p + " 采样池")
                    continue
                other = "background" if p == "key" else "key"
                mass = (
                    (key_ratio if p == "key" else 1 - key_ratio)
                    if pools[other]
                    else 1.0
                )
                groups = {}
                # Deduplicate overlapping source centers for sampling.
                for r in rs:
                    groups.setdefault(
                        (r["split_group"], r["event_group"]), {}
                    ).setdefault(
                        (r["source_episode_uuid"], r["source_frame_index"]), []
                    ).append(r)
                sources = {g[0] for g in groups}
                for group_key, members in groups.items():
                    source_groups = sum(1 for g in groups if g[0] == group_key[0])
                    for copies in members.values():
                        for r in copies:
                            r["sampling_weight"] = (
                                mass
                                / len(sources)
                                / source_groups
                                / len(members)
                                / len(copies)
                            )
            info.update(
                total_episodes=len(ep_meta),
                total_frames=global_index,
                total_chunks=(len(ep_meta) + chunk_size - 1) // chunk_size,
                total_videos=len(ep_meta) * len(dataset.cameras),
                total_tasks=len(tasks_export),
                splits={"train": f"0:{len(ep_meta)}"},
            )
            stats = {}
            for col, arrays in global_numeric.items():
                arr = np.concatenate(arrays)
                stats[col] = {
                    "min": arr.min(0).tolist(),
                    "max": arr.max(0).tolist(),
                    "mean": arr.mean(0).tolist(),
                    "std": arr.std(0).tolist(),
                    "count": [len(arr)],
                }
            for cam, parts in video_stats.items():
                sums = sum(p[0] for p in parts)
                squares = sum(p[1] for p in parts)
                n = sum(p[2] for p in parts)
                mean = sums / n

                def shape(x):
                    return np.asarray(x).reshape(3, 1, 1).tolist()

                stats[cam] = {
                    "min": shape(np.min([p[3] for p in parts], axis=0)),
                    "max": shape(np.max([p[4] for p in parts], axis=0)),
                    "mean": shape(mean),
                    "std": shape(np.sqrt(np.maximum(0, squares / n - mean**2))),
                    "count": [sum(p[5] for p in parts)],
                }
            write_json(temporary / "meta/info.json", info)
            write_json(temporary / "meta/stats.json", stats)
            write_jsonl(temporary / "meta/tasks.jsonl", tasks_export)
            write_jsonl(temporary / "meta/episodes.jsonl", ep_meta)
            write_jsonl(temporary / "meta/episodes_stats.jsonl", ep_stats)
            ann = temporary / "annotations"
            write_json(ann / "schema.json", schema)
            write_json(ann / "schema_history.json", schema_history or [schema])
            write_json(ann / "head_spec.json", head_spec(schema))
            # Explicit nullable categorical integers even when a whole field is missing.
            label_table = pa.Table.from_pylist(labels)
            for name in ["value_id", "schema_version", "revision"]:
                label_table = label_table.set_column(
                    label_table.schema.get_field_index(name),
                    name,
                    pa.array([r[name] for r in labels], type=pa.int64()),
                )
            pq.write_table(label_table, ann / "labels.parquet")
            pq.write_table(pa.Table.from_pylist(samples), ann / "samples.parquet")
            pq.write_table(pa.Table.from_pylist(source_map), ann / "source_map.parquet")
            carried_sources = {r["source_episode_uuid"] for r in source_map}
            interval_rows = [
                {"table": table, "payload": json.dumps(row, ensure_ascii=False)}
                for table, rs in frozen.items()
                for row in rs
                if (
                    row.get("source") in carried_sources
                    if table != "selections"
                    else row.get("dataset") == fingerprint
                )
            ]
            pq.write_table(
                pa.Table.from_pylist(
                    interval_rows,
                    schema=pa.schema(
                        [("table", pa.string()), ("payload", pa.string())]
                    ),
                ),
                ann / "intervals.parquet",
            )
            frozen_store.db.close()
            shutil.rmtree(temporary / ".snapshot")
            summary["elapsed_seconds"] = time.perf_counter() - begun
            write_json(ann / "validation_report.json", summary)
            checksums = {
                str(p.relative_to(temporary)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in temporary.rglob("*")
                if p.is_file()
            }
            write_json(
                ann / "manifest.json",
                {
                    "tool_version": __version__,
                    "format": "LeRobot-v2.1+DSE-1",
                    "schema_version": schema["version"],
                    "schema_hash": digest(schema),
                    "source_fingerprint": fingerprint,
                    "source_episode_uuids": sorted(carried_sources),
                    "source_dataset_fingerprints": sorted(
                        {r["source_dataset_fingerprint"] for r in source_map}
                    ),
                    "context_seconds": {
                        "history": history_context_seconds,
                        "review": 0.25,
                    },
                    "history_sampling": history_config.to_dict(),
                    "selections": selections,
                    "sampling": {"key_ratio": key_ratio, "group_first": True},
                    "encoding": {
                        "codec": "libx264",
                        "crf": 18,
                        "preset": "fast",
                        "pix_fmt": "yuv420p",
                    },
                    "checksums": checksums,
                    "source_roots_required": False,
                },
            )
            # Verify every output mapping, actual media and metadata before publication.
            output = Dataset(temporary, self.store.root / "cache" / "export-validation")
            output.validate(decode=True)
            if content_fingerprint(dataset.root)[0] != fingerprint:
                raise ValueError("来源在导出期间改变，拒绝发布")
            check()
            if target.exists():
                raise ValueError("目标目录在导出期间被创建，拒绝覆盖")
            os.rename(temporary, target)
            update(
                status="complete",
                progress=1,
                elapsed_seconds=time.perf_counter() - begun,
                summary=summary,
            )
        except Exception as exc:
            if temporary.exists():
                shutil.rmtree(temporary)
            update(
                status="cancelled" if isinstance(exc, InterruptedError) else "failed",
                error=str(exc),
                elapsed_seconds=time.perf_counter() - begun,
            )
