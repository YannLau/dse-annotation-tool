"""Export-time key classes; independent of classification heads and loss masks."""

import json

from .schema import digest


def default_key_classes(schema):
    """Keep original DSE phase sampling, without imposing its IDs on generic schemas."""
    if schema.get("semantic_profile", "dse_v1") != "dse_v1":
        return []
    phase = next((f for f in schema["fields"] if f["field_id"] == "phase"), None)
    if not phase:
        return []
    ids = sorted({c["id"] for c in phase["classes"]} & {2, 3, 5, 7})
    return [
        {"arm": arm, "field_id": "phase", "class_ids": ids.copy()}
        for arm in ("left", "right")
        if ids
    ]


def sampling_policy(schema, key_classes=None):
    """Validate and canonicalize explicit scope/field/class IDs against the frozen schema.

    None means original defaults; [] explicitly requests event-only key sampling.
    Merge duplicate selections and sort IDs so UI ordering cannot change the fingerprint.
    """
    if key_classes is None:
        key_classes = default_key_classes(schema)
    if not isinstance(key_classes, list):
        raise ValueError("关键类别配置必须为数组")
    fields = {f["field_id"]: f for f in schema["fields"]}
    selected = {}
    for rule in key_classes:
        if not isinstance(rule, dict) or set(rule) != {"arm", "field_id", "class_ids"}:
            raise ValueError("关键类别配置需要 arm、field_id 和 class_ids")
        arm, field_id, ids = rule["arm"], rule["field_id"], rule["class_ids"]
        if not isinstance(field_id, str) or field_id not in fields:
            raise ValueError("关键类别引用未知字段")
        field = fields[field_id]
        arms = ("global",) if field["scope"] == "global" else ("left", "right")
        if arm not in arms:
            raise ValueError("关键类别作用域与字段不一致")
        valid_ids = {c["id"] for c in field["classes"]}
        if (
            not isinstance(ids, list)
            or not ids
            or any(type(v) is not int or v not in valid_ids for v in ids)
        ):
            raise ValueError("关键类别包含无效类别 ID")
        selected.setdefault((arm, field_id), set()).update(ids)
    canonical = [
        {"arm": arm, "field_id": field_id, "class_ids": sorted(ids)}
        for (arm, field_id), ids in sorted(selected.items())
    ]
    legacy = schema.get(
        "semantic_profile", "dse_v1"
    ) == "dse_v1" and canonical == default_key_classes(schema)
    return {
        "version": 1,
        "key_classes": canonical,
        "match": "any",
        "event_window_seconds": 0.25,
        "grouping": "legacy_phase" if legacy else "selected_field_values",
    }


def sampling_metadata(policy):
    return {"policy": policy, "policy_hash": digest(policy)}


def sample_pool_group(policy, source, frame, effective, event_frames, fps):
    """Derive pool/group only; never change labels, review status or supervision masks."""
    nearest = min(event_frames, key=lambda e: (abs(e - frame), e), default=None)
    if nearest is not None and abs(nearest - frame) <= round(
        policy["event_window_seconds"] * fps
    ):
        return "key", source + ":event:" + str(nearest)
    values = [
        [
            r["arm"],
            r["field_id"],
            effective[r["arm"]].get(r["field_id"], {}).get("value_id"),
        ]
        for r in policy["key_classes"]
    ]
    key = any(v[2] in r["class_ids"] for r, v in zip(policy["key_classes"], values))
    if policy["grouping"] == "legacy_phase":
        phases = [
            effective[a].get("phase", {}).get("value_id") for a in ("left", "right")
        ]
        group = source + ":phase:" + str(phases)
    else:
        group = source + ":classes:" + json.dumps(values, separators=(",", ":"))
    return "key" if key else "background", group
