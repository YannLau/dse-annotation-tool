"""Schema-driven, snapshot-bound adviser previews and atomic draft application."""

import copy
import math
import time
import uuid
from collections import Counter, OrderedDict

import numpy as np
from pydantic import BaseModel, Field
from typing import Literal

from .schema import digest, field_map, references, resolve

FIELDS = tuple("adviser." + name for name in ("x", "y", "z", "orientation"))
RULE_VERSION = "schema-approved-displacement-1"
MAX_TARGET_FRAMES = 5000


class AdviserRequest(BaseModel):
    dataset: str
    episode: int
    arm: Literal["left", "right"]
    frame: int = Field(default=0, ge=0)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    calibration: dict = Field(default_factory=dict)
    mappings: dict = Field(default_factory=dict)
    plan_id: str | None = None


def class_mapping(field, explicit):
    """Return semantic roles mapped to live stable IDs, never category positions."""
    roles = (
        ("keep", "adjust")
        if field["field_id"].endswith("orientation")
        else ("none", "positive", "negative")
    )
    live = {c["id"]: c for c in field["classes"] if not c.get("deprecated")}
    supplied = explicit.get(field["field_id"], {})
    if not isinstance(supplied, dict):
        raise ValueError("类别映射必须为对象")
    mapping = {}
    for role in roles:
        matches = [c["id"] for c in live.values() if c["key"] == role]
        value = supplied.get(role, matches[0] if len(matches) == 1 else None)
        if type(value) is not int or value not in live:
            raise ValueError("缺少有效类别映射: " + role)
        mapping[role] = value
    unknown = field["unknown_id"]
    if (
        type(unknown) is not int
        or unknown not in live
        or (
            "unknown" in supplied
            and (type(supplied["unknown"]) is not int or supplied["unknown"] != unknown)
        )
    ):
        raise ValueError("unknown 必须映射到当前字段的有效 unknown_id")
    mapping["unknown"] = unknown
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("不同语义角色不能共用类别 ID")
    return mapping


def calibration_config(config, dataset, arm, info):
    feature = config.get("feature", "observation.ee_pose_" + arm)
    expected = {"dataset": dataset, "arm": arm, "feature": feature}
    if config.get("verified") is not True or config.get("binding") != expected:
        raise ValueError("请核实当前数据集、手臂和反馈字段的标定绑定")
    if config.get("unit") != "m" or config.get("frame") != "robot_base":
        raise ValueError("需要已验证的米制单位和 robot_base 坐标系")
    if feature != "observation.ee_pose_" + arm:
        raise ValueError("请选择对应手臂的实测末端反馈字段")
    metadata = info["features"].get(feature, {})
    if metadata.get("shape") != [7]:
        raise ValueError("缺少七维末端反馈位置和四元数字段")
    names = metadata.get("names")
    if isinstance(names, dict):
        names = next(iter(names.values())) if len(names) == 1 else []
    if names is not None and names != ["x", "y", "z", "qx", "qy", "qz", "qw"]:
        raise ValueError("末端反馈布局必须为 x/y/z/qx/qy/qz/qw")
    transform = np.asarray(config.get("transform", []), dtype=float)
    if (
        transform.shape != (4, 4)
        or not np.isfinite(transform).all()
        or not np.allclose(transform[3], [0, 0, 0, 1])
        or not np.allclose(
            transform[:3, :3] @ transform[:3, :3].T, np.eye(3), atol=1e-5
        )
        or not np.isclose(np.linalg.det(transform[:3, :3]), 1)
    ):
        raise ValueError("需要有限且有效的 4×4 刚体变换")
    for name in ("position_noise", "angle_noise"):
        value = config.get(name, 0)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not math.isfinite(3 * value)
            or value < 0
        ):
            raise ValueError("噪声必须是非负有限数值")
    return feature, transform


def dependency_nodes(schema, arm, field_id):
    fields = field_map(schema)
    seen = set()

    def visit(scope, name):
        for rule_name in ("applicable_when", "required_when"):
            for ref in references(fields[name][rule_name]):
                prefix, target = ref.split(".", 1)
                node = (scope if prefix == "self" else prefix, target)
                if node not in seen:
                    seen.add(node)
                    visit(*node)

    visit(arm, field_id)
    return sorted(seen)


class Evidence:
    def __init__(self, store, ds, request):
        self.schema = store.schema()
        self.request = request
        self.rows = ds.rows(request.episode)
        self.keys = [
            ds.source_key(request.episode, i, request.dataset)
            for i in range(len(self.rows))
        ]
        snapshot = store.snapshot()
        self.annotations = sorted(
            snapshot["annotations"], key=lambda r: (r["kind"] == "point", r["id"])
        )
        self.reviews = snapshot["reviews"]
        self.events = {
            (r["source"], r["frame"])
            for r in snapshot["events"]
            if r["arm"] == request.arm and r["status"] == "confirmed"
        }
        self.raw_cache, self.label_cache = {}, {}
        self.mapping = {}
        self.future_sources = set()
        try:
            self.feature, self.transform = calibration_config(
                request.calibration, request.dataset, request.arm, ds.info
            )
            self.calibration_error = None
        except (ValueError, TypeError, OverflowError) as exc:
            self.feature = "observation.ee_pose_" + request.arm
            self.transform = None
            self.calibration_error = str(exc)

    def raw(self, i):
        if i not in self.raw_cache:
            source, frame, _ = self.keys[i]
            raw = {a: {} for a in ("left", "right", "global")}
            for r in self.annotations:
                if r["source"] == source and r["start"] <= frame < r["end"]:
                    raw[r["arm"]][r["field"]] = {
                        "value_id": r["value"],
                        "review_status": r["status"],
                        "schema_version": r["version"],
                        "label_source": r["label_source"],
                    }
            self.raw_cache[i] = raw
        return self.raw_cache[i]

    def labels(self, i):
        if i not in self.label_cache:
            self.label_cache[i] = resolve(self.schema, self.raw(i))
        return self.label_cache[i]

    def approved(self, i):
        source, frame, _ = self.keys[i]
        return any(
            r["source"] == source
            and r["start"] <= frame < r["end"]
            and r["status"] == "approved"
            for r in self.reviews
        )

    def signature(self, i, nodes):
        return tuple((a, f, self.labels(i)[a][f]["value_id"]) for a, f in nodes)

    def capability(self, i, field_id):
        field = field_map(self.schema).get(field_id)
        result = {
            "field_id": field_id,
            "exists": field is not None,
            "supported": False,
            "mapping": {},
            "applicability": None,
            "required": None,
        }
        if not field:
            return {
                **result,
                "code": "missing_field",
                "reason": "当前方案没有此 adviser 字段",
            }
        if (
            field.get("scope") != "each_arm"
            or field.get("evidence") != "behavior"
            or field.get("type") != "categorical"
        ):
            return {
                **result,
                "code": "unsupported_field",
                "reason": "自动 adviser 需要按臂分类字段和行为证据",
            }
        record = self.labels(i)[self.request.arm][field_id]
        result.update(
            applicability=record["applicability"], required=record["required"]
        )
        try:
            result["mapping"] = class_mapping(field, self.request.mappings)
        except ValueError as exc:
            return {**result, "code": "invalid_mapping", "reason": str(exc)}
        result["supported"] = True
        if record["applicability"] != "applicable":
            code = (
                "inapplicable"
                if record["applicability"] == "not_applicable"
                else "undetermined"
            )
            return {**result, "code": code, "reason": record["reason"]}
        if field_id in self.raw(i)[self.request.arm]:
            return {
                **result,
                "code": "existing_label",
                "reason": "保留现有标签或显式 missing 覆盖",
            }
        if self.calibration_error:
            return {
                **result,
                "code": "invalid_calibration",
                "reason": self.calibration_error,
            }
        if self.measurement(i, field_id.endswith("orientation")) is None:
            return {
                **result,
                "code": "missing_pose",
                "reason": "缺少有效实测末端位置或四元数",
            }
        if not self.approved(i):
            return {
                **result,
                "code": "unapproved",
                "reason": "请先审核可信示教动作并设为 approved",
            }
        return {**result, "code": "ready", "reason": "可计算建议"}

    def measurement(self, i, orientation):
        try:
            pose = np.asarray(self.rows[i][self.feature], dtype=float)
            values = pose[3:7] if orientation else pose[:3]
            if pose.shape != (7,) or not np.isfinite(values).all():
                return None
            if orientation:
                norm = np.linalg.norm(values)
                return values / norm if math.isfinite(norm) and norm >= 1e-8 else None
            with np.errstate(over="ignore", invalid="ignore"):
                transformed = values @ self.transform[:3, :3].T
            return transformed if np.isfinite(transformed).all() else None
        except (KeyError, ValueError, TypeError):
            return None

    def calculate(self, start, field_id):
        capability = self.capability(start, field_id)
        source, source_frame, _ = self.keys[start]
        base = {
            "frame": start,
            "source_frame": source_frame,
            "source_episode_uuid": source,
            "arm": self.request.arm,
            "field_id": field_id,
            "status": "missing",
            "code": capability["code"],
            "reason": capability["reason"],
            "required": capability["required"],
        }
        if capability["code"] != "ready":
            return base
        orientation = field_id.endswith("orientation")
        first = self.measurement(start, orientation)
        if first is None:
            return {
                **base,
                "code": "missing_pose",
                "reason": "缺少有效实测末端位置或四元数",
            }
        t0 = self.rows[start]["timestamp"]
        if not isinstance(t0, (int, float)) or not math.isfinite(t0):
            return {**base, "code": "missing_pose", "reason": "时间戳无效"}
        nodes = dependency_nodes(self.schema, self.request.arm, field_id)
        signature = self.signature(start, nodes)
        measurements = [first]
        stop, cutoff = start, "episode_end"
        for i in range(start + 1, len(self.rows)):
            timestamp = self.rows[i]["timestamp"]
            if (
                not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp)
                or timestamp <= self.rows[i - 1]["timestamp"]
            ):
                cutoff = "invalid_timestamp"
                break
            if timestamp - t0 > 0.25 + 1e-8:
                cutoff = "horizon"
                break
            a, b = self.keys[i - 1], self.keys[i]
            if a[0] != b[0] or b[1] != a[1] + 1:
                cutoff = "source_boundary"
                break
            if not self.approved(i - 1):
                cutoff = "unapproved"
                break
            if (b[0], b[1]) in self.events:
                cutoff = "confirmed_event"
                break
            if (
                self.labels(i)[self.request.arm][field_id]["applicability"]
                != "applicable"
            ):
                cutoff = "gate_closed"
                break
            if self.signature(i, nodes) != signature:
                cutoff = "gate_context_changed"
                break
            value = self.measurement(i, orientation)
            if value is None:
                cutoff = "missing_pose"
                break
            measurements.append(value)
            self.future_sources.add(source)
            stop = i
        duration = self.rows[stop]["timestamp"] - t0
        if duration < 0.1 - 1e-8:
            return {
                **base,
                "code": "window_too_short",
                "reason": "连续 approved 窗口不足0.1秒",
                "start": start,
                "end": stop + 1,
                "duration": duration,
                "cutoff_reason": cutoff,
            }
        config = self.request.calibration
        if orientation:

            def angle(a, b):
                return 2 * math.acos(float(np.clip(abs(np.dot(a, b)), 0, 1)))

            net = angle(measurements[0], measurements[-1])
            path = sum(angle(a, b) for a, b in zip(measurements, measurements[1:]))
            dead = max(math.radians(5), 3 * config.get("angle_noise", 0))
            ambiguous = path - net > dead
            semantic = "unknown" if ambiguous else "adjust" if net > dead else "keep"
        else:
            axis = ("x", "y", "z").index(field_id.rsplit(".", 1)[1])
            positions = np.asarray(measurements)[:, axis]
            with np.errstate(over="ignore", invalid="ignore"):
                delta = positions[-1] - positions[0]
                path = np.sum(abs(np.diff(positions)))
            if not math.isfinite(delta) or not math.isfinite(path):
                return {**base, "code": "missing_pose", "reason": "末端位移数值无效"}
            dead = max(0.005, 3 * config.get("position_noise", 0))
            ambiguous = path - abs(delta) > dead
            semantic = (
                "unknown"
                if ambiguous
                else "none"
                if abs(delta) < dead
                else "positive"
                if delta > 0
                else "negative"
            )
        window = {
            "input_start": start,
            "input_end": stop + 1,
            "source_start": source_frame,
            "source_end": self.keys[stop][1] + 1,
            "start_timestamp": t0,
            "end_timestamp": self.rows[stop]["timestamp"],
            "duration": duration,
        }
        provenance = {
            "rule_version": RULE_VERSION,
            "input_dataset_fingerprint": self.request.dataset,
            "episode": self.request.episode,
            "input_frame": start,
            "source_episode_uuid": source,
            "source_frame_index": source_frame,
            "window": window,
            "gate_dependencies": [
                {"arm": a, "field_id": f, "value_id": v} for a, f, v in signature
            ],
            "mapping": capability["mapping"],
            "calibration": copy.deepcopy(config),
            "calibration_hash": digest(config),
            "schema_version": self.schema["version"],
            "schema_hash": digest(self.schema),
            "approved_basis": {
                "source": source,
                "start": source_frame,
                "end": self.keys[stop][1],
                "convention": "action-at-frame i approves pose transition i to i+1",
            },
            "cutoff_reason": cutoff,
            "needs_review": bool(ambiguous),
        }
        return {
            **base,
            "status": "suggestion",
            "code": "suggestion",
            "reason": "候选仍需人工审核",
            "value": capability["mapping"][semantic],
            "semantic": semantic,
            "start": start,
            "end": stop + 1,
            "duration": duration,
            "cutoff_reason": cutoff,
            "provenance": provenance,
        }


class AdviserService:
    def __init__(self, store, get_dataset, bounds):
        self.store, self.get_dataset, self.bounds = store, get_dataset, bounds
        self.plans = OrderedDict()
        self.latest_config = {}

    @staticmethod
    def request_context(body):
        values = body.model_dump(exclude={"plan_id"})
        start = body.start if body.start is not None else body.frame
        values.update(
            start=start,
            end=body.end if body.end is not None else start + 1,
            frame=start,
        )
        return values

    def context(self, body, schema, start, end):
        return {
            "scheme": self.store.get("scheme_info"),
            "dataset": body.dataset,
            "episode": body.episode,
            "arm": body.arm,
            "start": start,
            "end": end,
            "schema_version": schema["version"],
            "schema_hash": digest(schema),
            "revision": self.store.get("revision", 0),
            "calibration_hash": digest(body.calibration),
            "mapping_hash": digest(body.mappings),
        }

    def capabilities(self, body):
        with self.store.lock:
            ds = self.get_dataset(body.dataset)
            self.bounds(ds, body.episode, body.frame, body.frame + 1)
            evidence = Evidence(self.store, ds, body)
            fields = [evidence.capability(body.frame, field) for field in FIELDS]
            context = self.context(body, evidence.schema, body.frame, body.frame + 1)
            # Capability requests are the UI's registration of its current profile.
            # This invalidates a prior preview when another tab changes calibration
            # or mapping, without changing labels or registering future exposure.
            self.latest_config[(str(self.store.root), body.dataset, body.arm)] = (
                context["calibration_hash"],
                context["mapping_hash"],
            )
            return {
                **context,
                "fields": fields,
                "supported": any(f["supported"] for f in fields),
                "available": any(f["code"] == "ready" for f in fields),
                "feature": evidence.feature,
                "calibration_valid": evidence.calibration_error is None,
                "calibration_reason": evidence.calibration_error,
                "approved": evidence.approved(body.frame),
            }

    def preview(self, body):
        start = body.start if body.start is not None else body.frame
        end = body.end if body.end is not None else start + 1
        if end - start > MAX_TARGET_FRAMES:
            raise ValueError("单次 adviser 选区最多5000帧，请缩小选区")
        with self.store.lock:
            ds = self.get_dataset(body.dataset)
            self.bounds(ds, body.episode, start, end)
            evidence = Evidence(self.store, ds, body)
            context = self.context(body, evidence.schema, start, end)
            results = [
                evidence.calculate(i, field)
                for i in range(start, end)
                for field in FIELDS
            ]
            candidates = [r for r in results if r["status"] == "suggestion"]
            for source in evidence.future_sources:
                self.store.set("future_seen:" + source, True)
            self.store.db.commit()
            summary = {
                "frames": end - start,
                "candidates": len(candidates),
                "savable": len(candidates),
                "skipped": len(results) - len(candidates),
                "reasons": dict(
                    Counter(r["code"] for r in results if r["status"] != "suggestion")
                ),
            }
            plan_id = str(uuid.uuid4())
            result = {
                "plan_id": plan_id,
                "context": context,
                "summary": summary,
                "results": results,
                "fields": [evidence.capability(start, field) for field in FIELDS],
            }
            key = (str(self.store.root), body.dataset, body.arm)
            self.latest_config[key] = (
                context["calibration_hash"],
                context["mapping_hash"],
            )
            self.plans[plan_id] = {
                "created": time.monotonic(),
                "root": str(self.store.root),
                "body": self.request_context(body),
                "result": result,
                "config_key": key,
            }
            while len(self.plans) > 8:
                self.plans.popitem(last=False)
            return result

    def apply(self, body):
        with self.store.lock:
            plan = self.plans.get(body.plan_id)
            if (
                not plan
                or plan["root"] != str(self.store.root)
                or time.monotonic() - plan["created"] > 900
            ):
                raise ValueError("候选已过期，请重新预览")
            if self.request_context(body) != plan["body"]:
                raise ValueError("候选上下文已变化，请重新预览")
            with self.store.db:
                # Serialize cross-process writers before rechecking the preview snapshot.
                self.store.db.execute("BEGIN IMMEDIATE")
                ds = self.get_dataset(body.dataset)
                old = plan["result"]
                context = self.context(
                    body,
                    self.store.schema(),
                    old["context"]["start"],
                    old["context"]["end"],
                )
                if context != old["context"] or self.latest_config.get(
                    plan["config_key"]
                ) != (context["calibration_hash"], context["mapping_hash"]):
                    raise ValueError("标注、规则或标定已变化，请重新预览")
                evidence = Evidence(self.store, ds, body)
                candidates = [r for r in old["results"] if r["status"] == "suggestion"]
                # Validate every saved value and window again before beginning the batch.
                for candidate in candidates:
                    if (
                        evidence.calculate(candidate["frame"], candidate["field_id"])
                        != candidate
                    ):
                        raise ValueError("候选依据已变化，请重新预览")
                if candidates:
                    with self.store.transaction("adviser 候选接受为草稿"):
                        for candidate in candidates:
                            report = self.store.annotate(
                                candidate["source_episode_uuid"],
                                candidate["source_frame"],
                                candidate["source_frame"] + 1,
                                candidate["arm"],
                                candidate["field_id"],
                                candidate["value"],
                                "behavior",
                                point=True,
                                label_source="rule_suggestion",
                                provenance=candidate["provenance"],
                            )
                            if report["affected"] != 1:
                                raise ValueError("候选保存范围已变化，整组已回滚")
            self.plans.pop(body.plan_id, None)
            return {
                "saved": len(candidates),
                "skipped": old["summary"]["skipped"],
                "failed": 0,
                "revision": self.store.get("revision", 0),
            }


def install_adviser_routes(app, store, get_dataset, bounds):
    service = AdviserService(store, get_dataset, bounds)

    @app.post("/api/adviser/capabilities")
    def capabilities(body: AdviserRequest):
        return service.capabilities(body)

    @app.post("/api/adviser/preview")
    def preview(body: AdviserRequest):
        return service.preview(body)

    @app.post("/api/adviser/apply")
    def apply(body: AdviserRequest):
        return service.apply(body)

    @app.post("/api/adviser/suggest")
    def legacy_suggest(body: AdviserRequest):
        result = service.preview(
            body.model_copy(update={"start": body.frame, "end": body.frame + 1})
        )
        values = {
            r["field_id"].split(".", 1)[1]: r["value"]
            for r in result["results"]
            if r["status"] == "suggestion"
        }
        return {
            **result,
            "values": values,
            "status": "suggestion" if values else "missing",
            "reason": "候选仍需人工审核"
            if values
            else "没有满足门控、审核和标定条件的候选",
        }

    return service
