"""Named numeric channels and reviewable, context-bound event proposals."""

import copy
import json
import re

from .assist import event_candidates
from .schema import digest


def channel_catalog(info):
    channels = []
    for feature, spec in info.get("features", {}).items():
        if not str(spec.get("dtype", "")).startswith(("float", "int", "uint", "bool")):
            continue
        shape = spec.get("shape", [])
        names = spec.get("names")
        if isinstance(names, dict):
            names = next(iter(names.values())) if len(names) == 1 else None
        if (
            len(shape) != 1
            or not isinstance(names, list)
            or len(names) != shape[0]
            or not names
        ):
            continue
        role = (
            "command"
            if feature == "action" or "cmd" in feature or "command" in feature
            else "feedback"
            if feature.startswith("observation.")
            else "unspecified"
        )
        for dimension, name in enumerate(names):
            if not isinstance(name, str) or not name.strip():
                continue
            lower = name.lower()
            arm = (
                "left"
                if re.search(r"left|(?:^|_)l(?:_|$)", lower)
                else ("right" if re.search(r"right|(?:^|_)r(?:_|$)", lower) else None)
            )
            channels.append(
                dict(
                    feature=feature,
                    dimension=dimension,
                    name=name,
                    role=role,
                    arm_hint=arm,
                )
            )
    return channels


def event_identity(source, frame, arm, kind, details):
    return (source, frame, arm, kind, details.get("feature"), details.get("dimension"))


def preview_events(store, ds, key, body, start, end, *, legacy=False):
    arm, feature, dimension = (
        body.get("arm"),
        body.get("feature"),
        body.get("dimension"),
    )
    if arm not in ("left", "right") or type(dimension) is not int:
        raise ValueError("请选择手臂和有效具名通道")
    channel = next(
        (
            c
            for c in channel_catalog(ds.info)
            if c["feature"] == feature and c["dimension"] == dimension
        ),
        None,
    )
    if channel is None:
        raise ValueError("请选择有效的具名数值通道")
    if channel["arm_hint"] and channel["arm_hint"] != arm:
        raise ValueError("通道所属手臂与事件手臂不一致")
    role = body.get("role", channel["role"])
    if role not in ("feedback", "command") or (
        feature in ("observation.state", "action") and role != channel["role"]
    ):
        raise ValueError("请明确通道是实测反馈还是动作命令")
    calibration = copy.deepcopy(body.get("calibration", {}))
    binding = dict(dataset=key, arm=arm, feature=feature, dimension=dimension)
    if not legacy and calibration.get("binding") != binding:
        raise ValueError("通道或手臂已变化，请重新核对事件标定")
    rows = ds.rows(body["episode"])
    candidates = event_candidates(rows, feature, dimension, calibration)
    context = dict(
        dataset=key,
        episode=body["episode"],
        start=start,
        end=end,
        arm=arm,
        feature=feature,
        dimension=dimension,
        role=role,
        calibration=calibration,
        schema_hash=digest(store.schema()),
        revision=store.get("revision", 0),
        scheme=store.get("scheme_info"),
    )
    existing = set()
    for record in store.db.execute("SELECT * FROM events"):
        details = json.loads(record["details"] or "{}")
        existing.add(
            event_identity(
                record["source"],
                record["frame"],
                record["arm"],
                record["type"],
                details,
            )
        )
    output, duplicates = [], 0
    for event in candidates:
        frame = event["frame"]
        if not start <= frame < end:
            continue
        source, sf, _ = ds.source_key(body["episode"], frame, key)
        details = {
            **event["details"],
            "role": role,
            "channel_name": channel["name"],
            "dataset": key,
            "scan_range": [start, end],
        }
        identity = event_identity(source, sf, arm, event["type"], details)
        duplicate = identity in existing
        duplicates += int(duplicate)
        output.append(
            dict(
                frame=frame,
                source_frame=sf,
                source=source,
                arm=arm,
                type=event["type"],
                status="suggestion",
                details=details,
                duplicate=duplicate,
            )
        )
        existing.add(identity)
    return dict(
        context=context,
        start=start,
        end=end,
        candidates=output,
        summary=dict(
            candidates=len(output),
            savable=len(output) - duplicates,
            duplicates=duplicates,
        ),
    )


def save_events(store, preview):
    proposals = [c for c in preview["candidates"] if not c["duplicate"]]
    if proposals:
        with store.transaction("接受夹爪事件建议"):
            for event in proposals:
                store.db.execute(
                    "INSERT INTO events(source,frame,arm,type,status,details) VALUES(?,?,?,?,?,?)",
                    (
                        event["source"],
                        event["source_frame"],
                        event["arm"],
                        event["type"],
                        "suggestion",
                        json.dumps(event["details"], ensure_ascii=False),
                    ),
                )
            store.set("event_calibration", preview["context"])
    return dict(saved=len(proposals), skipped=preview["summary"]["duplicates"])
