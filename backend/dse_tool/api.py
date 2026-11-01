import argparse
import io
import json
import os
import threading
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import av
import time
from pathlib import Path
from typing import Literal
import pyarrow.parquet as pq
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .dataset import Dataset, content_fingerprint, verify_manifest
from .exporter import Exporter
from .store import Store
from .schema import (
    resolve,
    head_spec,
    semantic_errors,
    evaluate,
    validate_schema,
    deletion_plan,
    prepare_schema,
)
from .assist import event_candidates, adviser_candidate
from .history import HistoryConfig
from .inspector import ExportInspector, clean
from .review import review_labels
from .filesystem import directories, pick_directory
import uuid


class HistorySettings(BaseModel):
    horizon_seconds: float = Field(default=0.6, ge=0, le=3600, allow_inf_nan=False)
    num_frames: int = Field(default=4, ge=1, le=32, strict=True)

    def config(self):
        return HistoryConfig(**self.model_dump())


class ImportRequest(BaseModel):
    path: str


class DirectoryPickerRequest(BaseModel):
    initial_path: str = ""
    purpose: Literal["import", "inspect"] = "import"


class AnnotationRequest(BaseModel):
    dataset: str
    episode: int
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    arm: Literal["left", "right", "global"]
    field: str
    value: int | None
    mode: Literal["state", "behavior"]
    reviewed: bool = False
    confirmed: bool = False
    point: bool = False
    point_policy: Literal["preserve", "overwrite"] = "preserve"
    preview: bool = False
    label_source: Literal["human", "rule_suggestion", "test_fixture"] = "human"
    provenance: dict = Field(default_factory=dict)
    history_config: HistorySettings | None = None


class AnnotationFocus(BaseModel):
    dataset: str
    episode: int
    frame: int = Field(ge=0)
    mode: Literal["state", "behavior"] = "state"


class BatchAnnotationRequest(BaseModel):
    changes: list[AnnotationRequest] = Field(min_length=1, max_length=128)
    expected_schema_version: int = Field(ge=1)
    preview: bool = False
    focus: AnnotationFocus | None = None


class LabelReviewRequest(BaseModel):
    dataset: str
    episode: int
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    mode: Literal["state", "behavior"]
    confirmed: bool = False
    preview: bool = False
    expected_schema_version: int = Field(ge=1)
    expected_revision: int | None = Field(default=None, ge=0)
    history_config: HistorySettings = Field(default_factory=HistorySettings)


class SelectionRequest(BaseModel):
    dataset: str
    episode: int
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    remove: bool = False


class ExportRequest(BaseModel):
    dataset: str
    destination: str
    key_ratio: float = Field(default=0.7, ge=0, le=1)
    history_config: HistorySettings = Field(default_factory=HistorySettings)
    history_context_seconds: float | None = Field(
        default=None, ge=0, le=3600, allow_inf_nan=False
    )


class ReviewRequest(BaseModel):
    dataset: str
    episode: int
    start: int
    end: int
    status: Literal["approved", "rejected", "unreviewed"]
    reason: str = ""


def create_app(work_root):
    app = FastAPI(title="DSE Annotation Tool", version="0.1.0")
    store = Store(work_root)
    log_dir = store.root / "logs"
    log_dir.mkdir(exist_ok=True)
    logger = logging.getLogger("dse." + str(store.root))
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_dir / "server.log", maxBytes=2 * 1024 * 1024, backupCount=3
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    exporter = Exporter(store)
    datasets = {}
    inspectors = {}
    media_lock = threading.RLock()
    app.state.store = store
    app.state.exporter = exporter
    app.state.datasets = datasets

    @app.middleware("http")
    async def origin_guard(request, call_next):
        origin = request.headers.get("origin")
        same_local_origin = origin == str(request.base_url).rstrip(
            "/"
        ) and request.url.hostname in ["127.0.0.1", "localhost"]
        if (
            origin
            and not same_local_origin
            and origin
            not in [
                "http://127.0.0.1:8765",
                "http://localhost:8765",
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            ]
        ):
            return JSONResponse({"detail": "只接受本机应用来源"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def validation_error(request, exc):
        logger.warning("%s: %s", request.url.path, exc)
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing_error(request, exc):
        return JSONResponse({"detail": "文件不存在: " + str(exc)}, status_code=400)

    @app.exception_handler(av.error.FFmpegError)
    @app.exception_handler(subprocess.CalledProcessError)
    async def media_error(request, exc):
        logger.exception("媒体读取失败")
        return JSONResponse(
            {"detail": "媒体无法解码或建立 PTS 索引: " + str(exc)}, status_code=400
        )

    @app.exception_handler(Exception)
    async def server_error(request, exc):
        logger.exception("请求失败")
        return JSONResponse(
            {"detail": "服务内部错误，详见工作目录 logs/server.log"}, status_code=500
        )

    def get_dataset(key):
        if key not in datasets:
            row = store.db.execute(
                "SELECT * FROM datasets WHERE fingerprint=?", (key,)
            ).fetchone()
            if not row:
                raise ValueError("未登记的数据集")
            datasets[key] = Dataset(row["root"], store.root / "cache" / "indices" / key)
        ds = datasets[key]
        row = store.db.execute(
            "SELECT signatures FROM datasets WHERE fingerprint=?", (key,)
        ).fetchone()
        if not row:
            raise ValueError("数据集登记尚未提交，请重新导入")
        for relative, (size, mtime) in json.loads(row[0]).items():
            p = ds.root / relative
            if not p.exists() or [p.stat().st_size, p.stat().st_mtime_ns] != [
                size,
                mtime,
            ]:
                raise ValueError("来源发生变化，请重新导入验证；已阻止使用旧映射和标签")
        return ds

    def bounds(ds, episode, start, end):
        n = len(ds.table(episode))
        if not 0 <= start < end <= n:
            raise ValueError(f"需要半开帧区间 [start,end)，范围 0–{n}")

    def key_range(ds, episode, start, end, fingerprint):
        keys = [ds.source_key(episode, i, fingerprint) for i in range(start, end)]
        if len({k[0] for k in keys}) != 1 or [k[1] for k in keys] != list(
            range(keys[0][1], keys[0][1] + len(keys))
        ):
            raise ValueError("区间跨越来源边界")
        return keys[0][0], keys[0][1], keys[-1][1] + 1

    def selected(key, episode, frame):
        return (
            store.db.execute(
                "SELECT 1 FROM selections WHERE dataset=? AND episode=? AND start<=? AND end>?",
                (key, episode, frame, frame),
            ).fetchone()
            is not None
        )

    def episode_summary(key, ds):
        result = []
        for ep in ds.episodes:
            e = ep["episode_index"]
            segments = [
                dict(r)
                for r in store.db.execute(
                    "SELECT * FROM selections WHERE dataset=? AND episode=? ORDER BY start",
                    (key, e),
                )
            ]
            source, first_source_frame, _ = ds.source_key(e, 0, key)
            count = store.db.execute(
                "SELECT COUNT(*) FROM annotations WHERE source=? AND status='reviewed'",
                (source,),
            ).fetchone()[0]
            records = [
                dict(r)
                for r in store.db.execute(
                    "SELECT * FROM annotations WHERE source=? ORDER BY CASE kind WHEN 'point' THEN 1 ELSE 0 END,id",
                    (source,),
                )
            ]
            reviewed_frames = 0
            version = store.schema()["version"]
            for source_frame in range(
                first_source_frame, first_source_frame + ep["length"]
            ):
                effective = {
                    (r["arm"], r["field"]): r
                    for r in records
                    if r["start"] <= source_frame < r["end"]
                }
                reviewed_frames += int(
                    any(
                        r["status"] == "reviewed" and r["version"] == version
                        for r in effective.values()
                    )
                )
            result.append(
                {
                    **ep,
                    "selections": segments,
                    "reviewed_records": count,
                    "reviewed_frames": reviewed_frames,
                }
            )
        return result

    @app.get("/api/filesystem/directories")
    def list_directories(
        path: str = "", show_hidden: bool = False, initial: bool = False
    ):
        return directories(path, show_hidden, initial)

    @app.post("/api/filesystem/pick-directory")
    def select_directory(body: DirectoryPickerRequest):
        return pick_directory(body.initial_path, body.purpose)

    def get_inspector(handle):
        with media_lock:
            reader = inspectors.get(handle)
        if reader is None:
            raise ValueError("检查会话已关闭或服务已重启，请重新打开导出目录")
        reader.ensure_unchanged()
        return reader

    @app.post("/api/inspect/open")
    def inspect_open(body: ImportRequest):
        handle = uuid.uuid4().hex
        reader = ExportInspector(body.path, store.root / "cache" / "inspector" / handle)
        with media_lock:
            inspectors[handle] = reader
            if len(inspectors) > 3:
                inspectors.pop(next(iter(inspectors)))
        return clean({"id": handle, **reader.summary})

    @app.get("/api/inspect/{handle}/episode/{episode}")
    def inspect_episode(
        handle: str,
        episode: int,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=250),
    ):
        return get_inspector(handle).episode(episode, offset, limit)

    @app.get("/api/inspect/{handle}/frame/{episode}/{frame}")
    def inspect_frame(handle: str, episode: int, frame: int):
        return get_inspector(handle).frame(episode, frame)

    @app.get("/api/inspect/{handle}/file")
    def inspect_file(
        handle: str,
        path: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return get_inspector(handle).file(path, offset, limit)

    @app.get("/api/inspect/{handle}/image/{episode}/{camera}/{frame}")
    def inspect_image(handle: str, episode: int, camera: int, frame: int):
        reader = get_inspector(handle)
        reader.frame_summary(episode, frame)
        if not 0 <= camera < len(reader.dataset.cameras):
            raise ValueError("未知相机")
        cache = store.root / "cache" / "jpeg"
        cache.mkdir(exist_ok=True)
        filename = cache / f"inspect-{handle}-{episode}-{camera}-{frame}.jpg"
        with media_lock:
            if not filename.exists():
                reader.dataset.decode(
                    episode, reader.dataset.cameras[camera], frame
                ).save(filename, quality=86)
                files = sorted(cache.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
                size = sum(p.stat().st_size for p in files)
                for p in files:
                    if size <= 256 * 1024 * 1024:
                        break
                    if p != filename:
                        size -= p.stat().st_size
                        p.unlink()
        return FileResponse(
            filename,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/api/project")
    def project():
        return {
            "work_root": str(store.root),
            "schema": store.schema(),
            "datasets": [
                {
                    "fingerprint": r["fingerprint"],
                    "root": r["root"],
                    "report": json.loads(r["report"]),
                }
                for r in store.db.execute("SELECT * FROM datasets")
            ],
            "preferences": store.get("preferences", {}),
            "jobs": [
                json.loads(r["payload"]) for r in store.db.execute("SELECT * FROM jobs")
            ],
            "revision": store.get("revision", 0),
        }

    @app.post("/api/import")
    def import_dataset(body: ImportRequest):
        root = Path(body.path).expanduser().resolve()
        if store.root == root or store.root.is_relative_to(root):
            raise ValueError("工作目录不得放在来源数据内")
        fingerprint, signatures = content_fingerprint(root)
        verify_manifest(root)
        ds = Dataset(root, store.root / "cache" / "indices" / fingerprint)
        existing = store.db.execute(
            "SELECT * FROM datasets WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        report = (
            json.loads(existing["report"]) if existing else ds.validate(decode=True)
        )
        logger.info(
            "Import %s fingerprint=%s episodes=%s", root, fingerprint, len(ds.episodes)
        )
        with store.lock, store.db:
            store.db.execute(
                "INSERT OR REPLACE INTO datasets VALUES(?,?,?,?,?)",
                (
                    fingerprint,
                    str(root),
                    json.dumps(ds.info),
                    json.dumps(report),
                    json.dumps(signatures),
                ),
            )
            if (root / "annotations/schema.json").exists() and not existing:
                schema = json.loads((root / "annotations/schema.json").read_text())
                validate_schema(schema)
                current = store.db.execute(
                    "SELECT hash FROM schemas WHERE version=?", (schema["version"],)
                ).fetchone()
                from .schema import digest

                if (
                    current
                    and current[0] != digest(schema)
                    and store.db.execute("SELECT COUNT(*) FROM annotations").fetchone()[
                        0
                    ]
                ):
                    raise ValueError(
                        "工作项目 schema 版本冲突，请为该导出集使用独立工作目录"
                    )
                store.db.execute(
                    "INSERT OR REPLACE INTO schemas VALUES(?,?,?)",
                    (schema["version"], digest(schema), json.dumps(schema)),
                )
                store.set("schema_version", schema["version"])
                history_path = root / "annotations/schema_history.json"
                for old in (
                    json.loads(history_path.read_text())
                    if history_path.exists()
                    else []
                ):
                    validate_schema(old)
                    previous = store.db.execute(
                        "SELECT hash FROM schemas WHERE version=?", (old["version"],)
                    ).fetchone()
                    if (
                        previous
                        and previous[0] != digest(old)
                        and store.db.execute(
                            "SELECT COUNT(*) FROM annotations"
                        ).fetchone()[0]
                    ):
                        raise ValueError("历史 schema 冲突，请使用独立工作目录")
                    store.db.execute(
                        "INSERT OR REPLACE INTO schemas VALUES(?,?,?)",
                        (old["version"], digest(old), json.dumps(old)),
                    )
                for record in pq.read_table(
                    root / "annotations/intervals.parquet"
                ).to_pylist():
                    if record["table"] not in ["annotations", "reviews", "events"]:
                        continue
                    r = json.loads(record["payload"])
                    r.pop("id", None)
                    table = record["table"]
                    allowed = {
                        c["name"]
                        for c in store.db.execute("PRAGMA table_info(" + table + ")")
                    }
                    if set(r) - allowed:
                        raise ValueError("导入区间含无效列")
                    cols = ",".join(r)
                    vals = tuple(r.values())
                    if not store.db.execute(
                        "SELECT 1 FROM "
                        + table
                        + " WHERE "
                        + " AND ".join(k + " IS ?" for k in r),
                        vals,
                    ).fetchone():
                        store.db.execute(
                            "INSERT INTO "
                            + table
                            + " ("
                            + cols
                            + ") VALUES("
                            + ",".join("?" for _ in r)
                            + ")",
                            vals,
                        )
                samples = pq.read_table(
                    root / "annotations/samples.parquet"
                ).to_pylist()
                for e in {r["episode_index"] for r in samples}:
                    centers = sorted(
                        r["frame_index"]
                        for r in samples
                        if r["episode_index"] == e and r["center_is_selected"]
                    )
                    runs = []
                    for i in centers:
                        if runs and runs[-1][1] == i:
                            runs[-1][1] = i + 1
                        else:
                            runs.append([i, i + 1])
                    for a, b in runs:
                        store.db.execute(
                            "INSERT INTO selections(dataset,episode,start,end) VALUES(?,?,?,?)",
                            (fingerprint, e, a, b),
                        )
            store.history_barrier()
            store.db.commit()
        datasets[fingerprint] = ds
        return {
            "fingerprint": fingerprint,
            "info": ds.info,
            "history_sampling": ds.history_sampling,
            "history_context_seconds": ds.history_capacity,
            "report": report,
            "episodes": episode_summary(fingerprint, ds),
        }

    @app.get("/api/datasets/{key}")
    def dataset_info(key: str):
        ds = get_dataset(key)
        return {
            "fingerprint": key,
            "root": str(ds.root),
            "history_sampling": ds.history_sampling,
            "history_context_seconds": ds.history_capacity,
            "info": ds.info,
            "report": json.loads(
                store.db.execute(
                    "SELECT report FROM datasets WHERE fingerprint=?", (key,)
                ).fetchone()[0]
            ),
            "episodes": episode_summary(key, ds),
        }

    @app.get("/api/window/{key}/{episode}/{frame}")
    def window(
        key: str,
        episode: int,
        frame: int,
        mode: Literal["state", "behavior"] = "state",
        request_id: int = 0,
        future_horizon: float = Query(default=0.25, ge=0.25, le=10),
        history_horizon: float = Query(default=0.6, ge=0, le=3600),
        history_frames: int = Query(default=4, ge=1, le=32),
    ):
        ds = get_dataset(key)
        bounds(ds, episode, frame, frame + 1)
        rows = ds.rows(episode)
        history_config = HistoryConfig(history_horizon, history_frames)
        history_samples = ds.history_samples(episode, frame, history_config)
        history = [r["frame"] for r in history_samples]
        source, sf, group = ds.source_key(episode, frame, key)
        if mode == "behavior":
            with store.lock:
                store.set("future_seen:" + source, True)
                store.db.commit()
        with store.lock:
            labels = store.labels(source, sf, selected(key, episode, frame))
            window_revision = store.get("revision", 0)
        if mode == "state":
            labels = {
                a: {
                    k: r
                    for k, r in fields.items()
                    if next(
                        f for f in store.schema()["fields"] if f["field_id"] == k
                    ).get("evidence", "state")
                    == "state"
                }
                for a, fields in labels.items()
            }
        lo = (
            next((i for i in history if i is not None), frame) if mode == "state" else 0
        )
        hi = frame + 1 if mode == "state" else len(rows)
        telemetry = []
        for i in range(lo, hi):
            r = {"frame": i, "timestamp": rows[i]["timestamp"]}
            for k, v in rows[i].items():
                if isinstance(v, list) and (
                    mode == "behavior"
                    or (k.startswith("observation.") and "cmd" not in k)
                ):
                    r[k] = v
            telemetry.append(r)
        tracks = []
        if mode == "behavior":
            tracks = [
                dict(r)
                for r in store.db.execute(
                    "SELECT * FROM annotations WHERE source=?", (source,)
                )
            ]
        return {
            "request_id": request_id,
            "revision": window_revision,
            "frame": frame,
            "timestamp": rows[frame]["timestamp"],
            "history": history_samples,
            "history_config": history_config.to_dict(),
            "future": ds.future(episode, frame, future_horizon)
            if mode == "behavior"
            else [],
            "evidence_mode": mode,
            "cameras": ds.cameras,
            "labels": labels,
            "telemetry": telemetry,
            "source_key": {"uuid": source, "frame": sf, "split_group": group},
            "selections": [
                dict(r)
                for r in store.db.execute(
                    "SELECT * FROM selections WHERE dataset=? AND episode=? ORDER BY start",
                    (key, episode),
                )
            ],
            "events": [
                dict(r)
                for r in store.db.execute(
                    "SELECT * FROM events WHERE source=?", (source,)
                )
            ]
            if mode == "behavior"
            else [],
            "reviews": [
                dict(r)
                for r in store.db.execute(
                    "SELECT * FROM reviews WHERE source=?", (source,)
                )
            ]
            if mode == "behavior"
            else [],
            "tracks": tracks,
            "complete_errors": semantic_errors(store.labels(source, sf))
            if mode == "behavior"
            else [],
            "task": next(
                t["task"]
                for t in ds.tasks
                if t["task_index"] == rows[frame]["task_index"]
            ),
        }

    @app.get("/api/frame/{key}/{episode}/{camera}/{frame}")
    def image_frame(
        key: str, episode: int, camera: int, frame: int, request_id: int = 0
    ):
        ds = get_dataset(key)
        bounds(ds, episode, frame, frame + 1)
        if camera < 0 or camera >= len(ds.cameras):
            raise ValueError("未知相机")
        cache = store.root / "cache" / "jpeg"
        cache.mkdir(exist_ok=True)
        filename = cache / f"{key}-{episode}-{camera}-{frame}.jpg"
        with media_lock:
            if not filename.exists():
                picture = ds.decode(episode, ds.cameras[camera], frame)
                picture.save(filename, quality=86)
                files = sorted(cache.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
                size = sum(p.stat().st_size for p in files)
                for p in files:
                    if size <= 256 * 1024 * 1024:
                        break
                    if p == filename:
                        continue
                    size -= p.stat().st_size
                    p.unlink()
        return FileResponse(
            filename,
            media_type="image/jpeg",
            headers={
                "X-Cursor-Version": str(request_id),
                "Cache-Control": "private, max-age=3600",
            },
        )

    @app.post("/api/annotations/review-range")
    def review_existing_labels(body: LabelReviewRequest):
        ds = get_dataset(body.dataset)
        bounds(ds, body.episode, body.start, body.end)
        config = body.history_config.config()
        if body.mode == "state":
            ds.history_samples(body.episode, body.start, config)
        source, a, b = key_range(ds, body.episode, body.start, body.end, body.dataset)
        result = review_labels(
            store,
            source,
            a,
            b,
            body.mode,
            confirmed=body.confirmed,
            preview=body.preview,
            expected_version=body.expected_schema_version,
            expected_revision=body.expected_revision,
            history_config=config,
        )
        source_offset = a - body.start
        for issue in result["issues"]:
            issue.update(source_start=issue["start"], source_end=issue["end"])
            issue["start"] -= source_offset
            issue["end"] -= source_offset
        result.update(
            start=body.start,
            end=body.end,
            training_center_frames=sum(
                max(0, min(body.end, row["end"]) - max(body.start, row["start"]))
                for row in store.db.execute(
                    "SELECT start,end FROM selections WHERE dataset=? AND episode=?",
                    (body.dataset, body.episode),
                )
            ),
        )
        return result

    @app.post("/api/annotations")
    def annotate(body: AnnotationRequest):
        ds = get_dataset(body.dataset)
        bounds(ds, body.episode, body.start, body.end)
        if body.point and body.end != body.start + 1:
            raise ValueError("逐帧覆盖仅能作用于一帧")
        source, a, b = key_range(ds, body.episode, body.start, body.end, body.dataset)
        return store.annotate(
            source,
            a,
            b,
            body.arm,
            body.field,
            body.value,
            body.mode,
            body.reviewed,
            body.confirmed,
            body.point,
            body.preview,
            body.label_source,
            annotation_provenance(body),
            point_policy=body.point_policy,
        )

    def annotation_provenance(body):
        provenance = dict(body.provenance)
        if body.mode == "state":
            provenance["history_config"] = (
                body.history_config.config() if body.history_config else HistoryConfig()
            ).to_dict()
        return provenance

    @app.post("/api/annotations/batch")
    def annotate_batch(body: BatchAnnotationRequest):
        changes = []
        for item in body.changes:
            ds = get_dataset(item.dataset)
            bounds(ds, item.episode, item.start, item.end)
            if item.point and item.end != item.start + 1:
                raise ValueError("逐帧覆盖仅能作用于一帧")
            source, a, b = key_range(
                ds, item.episode, item.start, item.end, item.dataset
            )
            changes.append(
                dict(
                    source=source,
                    start=a,
                    end=b,
                    arm=item.arm,
                    field=item.field,
                    value=item.value,
                    mode=item.mode,
                    reviewed=item.reviewed,
                    confirmed=item.confirmed,
                    point=item.point,
                    point_policy=item.point_policy,
                    label_source=item.label_source,
                    provenance=annotation_provenance(item),
                )
            )
        focus = None
        if body.focus:
            item = body.focus
            ds = get_dataset(item.dataset)
            bounds(ds, item.episode, item.frame, item.frame + 1)
            source, sf, _ = ds.source_key(item.episode, item.frame, item.dataset)
            focus = {
                "source": source,
                "frame": sf,
                "center": selected(item.dataset, item.episode, item.frame),
            }
        result = store.annotate_batch(
            changes,
            preview=body.preview,
            expected_version=body.expected_schema_version,
            focus=focus,
        )
        if body.focus and body.focus.mode == "state":
            visible = {
                f["field_id"]
                for f in store.schema()["fields"]
                if f.get("evidence", "state") == "state"
            }
            for key in ["labels", "form_labels"]:
                result[key] = {
                    arm: {field: r for field, r in fields.items() if field in visible}
                    for arm, fields in result[key].items()
                }
        return result

    @app.get("/api/completeness/{key}/{episode}/{frame}")
    def completeness(key: str, episode: int, frame: int):
        ds = get_dataset(key)
        bounds(ds, episode, frame, frame + 1)
        source, sf, _ = ds.source_key(episode, frame, key)
        labels = store.labels(source, sf)
        errors = semantic_errors(labels)
        for arm, fields in labels.items():
            for key, record in fields.items():
                if (
                    record["required"] is True
                    and record["applicability"] == "applicable"
                    and not record["effective_loss_mask"]
                ):
                    errors.append(arm + ": " + key + " 未完成当前 schema 审核")
        return {"complete": not errors, "errors": errors}

    @app.post("/api/selections")
    def selection(body: SelectionRequest):
        ds = get_dataset(body.dataset)
        bounds(ds, body.episode, body.start, body.end)
        store.select(body.dataset, body.episode, body.start, body.end, body.remove)
        return {"saved": True}

    @app.post("/api/history/{direction}")
    def history(direction: str):
        if direction not in ["undo", "redo"]:
            raise ValueError("未知历史操作")
        return {"changed": store.undo(direction == "redo")}

    @app.post("/api/schema/draft")
    def schema_draft(body: dict):
        with store.lock:
            store.set("schema_draft", body)
            store.db.commit()
        return {"saved": True}

    @app.post("/api/schema/delete-preview")
    def schema_delete_preview(body: dict):
        schema = body.get("schema", store.schema())
        plan = deletion_plan(schema, body["field_id"], body.get("class_id"))
        args = [body["field_id"]]
        where = "field=?"
        if body.get("class_id") is not None:
            where += " AND value=?"
            args.append(body["class_id"])
        plan["historical_annotation_records"] = store.db.execute(
            "SELECT COUNT(*) FROM annotations WHERE " + where, args
        ).fetchone()[0]
        return plan

    @app.post("/api/schema/preview")
    def schema_preview(body: dict):
        schema = prepare_schema(body.get("schema", store.schema()), store.schema())
        validate_schema(schema, store.schema())
        return {
            "migration": store.publish(schema, True),
            "labels": resolve(
                schema,
                {
                    "left": {"phase": {"value_id": body.get("left", 2)}},
                    "right": {"phase": {"value_id": body.get("right", 4)}},
                },
            ),
        }

    @app.post("/api/schema")
    def publish_schema(body: dict):
        return store.publish(body)

    @app.get("/api/head-spec")
    def heads():
        return head_spec(store.schema())

    @app.post("/api/preferences")
    def preferences(body: dict):
        with store.lock:
            store.set("preferences", body)
            store.db.commit()
        return {"saved": True}

    @app.post("/api/reviews")
    def review(body: ReviewRequest):
        ds = get_dataset(body.dataset)
        bounds(ds, body.episode, body.start, body.end)
        source, a, b = key_range(ds, body.episode, body.start, body.end, body.dataset)
        with store.transaction("动作质量审核"):
            store._split_write("reviews", "source=?", (source,), a, b)
            store.db.execute(
                "INSERT INTO reviews(source,start,end,status,reason) VALUES(?,?,?,?,?)",
                (source, a, b, body.status, body.reason),
            )
        return {"saved": True}

    @app.post("/api/events")
    def events(body: dict):
        ds = get_dataset(body["dataset"])
        bounds(ds, body["episode"], body["frame"], body["frame"] + 1)
        source, sf, _ = ds.source_key(body["episode"], body["frame"], body["dataset"])
        with store.transaction("手动事件/确认候选"):
            if body.get("id"):
                store.db.execute(
                    "UPDATE events SET frame=?,type=?,status=? WHERE id=? AND source=?",
                    (
                        sf,
                        body["type"],
                        body.get("status", "confirmed"),
                        body["id"],
                        source,
                    ),
                )
            else:
                store.db.execute(
                    "INSERT INTO events(source,frame,arm,type,status,details) VALUES(?,?,?,?,?,?)",
                    (
                        source,
                        sf,
                        body["arm"],
                        body["type"],
                        body.get("status", "confirmed"),
                        json.dumps({"source": "human"}),
                    ),
                )
        return {"saved": True}

    @app.post("/api/events/suggest")
    def suggest_events(body: dict):
        ds = get_dataset(body["dataset"])
        rows = ds.rows(body["episode"])
        feature = body["feature"]
        dimension = body["dimension"]
        names = ds.info["features"][feature].get("names", [])
        if feature not in ["action", "observation.state"] or not 0 <= dimension < len(
            names
        ):
            raise ValueError("请选择具名反馈/命令通道")
        candidates = event_candidates(rows, feature, dimension, body["calibration"])
        source, _, _ = ds.source_key(body["episode"], 0, body["dataset"])
        with store.transaction("生成事件候选"):
            for r in candidates:
                _, sf, _ = ds.source_key(body["episode"], r["frame"], body["dataset"])
                store.db.execute(
                    "INSERT INTO events(source,frame,arm,type,status,details) VALUES(?,?,?,?,?,?)",
                    (
                        source,
                        sf,
                        body["arm"],
                        r["type"],
                        "suggestion",
                        json.dumps(r["details"]),
                    ),
                )
            store.set("event_calibration", body)
        return {"candidates": candidates}

    @app.post("/api/adviser/suggest")
    def suggest_adviser(body: dict):
        ds = get_dataset(body["dataset"])
        rows = ds.rows(body["episode"])
        source, sf, _ = ds.source_key(body["episode"], body["frame"], body["dataset"])
        offset = sf - body["frame"]
        arm = body["arm"]

        def approved(i):
            return (
                store.db.execute(
                    "SELECT 1 FROM reviews WHERE source=? AND start<=? AND end>? AND status='approved'",
                    (source, i + offset, i + offset),
                ).fetchone()
                is not None
            )

        def phase(i):
            return store.raw(source, i + offset)[arm].get("phase", {}).get("value_id")

        event_frames = {
            r["frame"] - offset
            for r in store.db.execute(
                "SELECT frame FROM events WHERE source=?", (source,)
            )
        }
        config = body["calibration"]
        config["feature"] = "observation.ee_pose_" + arm
        return adviser_candidate(
            rows, body["frame"], approved, phase, event_frames, config
        )

    @app.post("/api/export")
    def export(body: ExportRequest):
        ds = get_dataset(body.dataset)
        target = Path(body.destination).expanduser().resolve()
        for r in store.db.execute("SELECT root FROM datasets"):
            root = Path(r[0]).resolve()
            if (
                target == root
                or target.is_relative_to(root)
                or root.is_relative_to(target)
            ):
                raise ValueError("导出不得与已登记来源目录重叠")
        return exporter.start(
            ds,
            body.dataset,
            body.destination,
            body.key_ratio,
            history_config=body.history_config.config(),
            history_context_seconds=body.history_context_seconds,
        )

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        r = store.db.execute(
            "SELECT payload FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not r:
            raise ValueError("未知作业")
        return json.loads(r[0])

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        if job_id in exporter.cancels:
            exporter.cancels[job_id].set()
        return {"requested": True}

    dist = Path(__file__).resolve().parents[2] / "frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default="./workspaces/default")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(args.work), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
