"""Review existing effective labels without propagating values across a selection."""

import copy
import json
from .history import HistoryConfig
from .schema import resolve, semantic_errors


def review_labels(
    store,
    source,
    start,
    end,
    mode,
    *,
    confirmed=False,
    preview=False,
    expected_version=None,
    expected_revision=None,
    history_config=None,
):
    if mode not in ["state", "behavior"] or not 0 <= start < end:
        raise ValueError("需要有效审核模式与半开选区")
    if mode == "state" and not preview and not confirmed:
        raise ValueError("请确认整个审核范围的允许观测能够支持标签")
    config = HistoryConfig.from_dict(history_config).to_dict()
    with store.lock:
        schema = store.schema()
        revision = store.get("revision", 0)
        if expected_version is not None and expected_version != schema["version"]:
            raise ValueError("schema 已变更，请刷新后重新审核")
        if expected_revision is not None and expected_revision != revision:
            raise ValueError("标注已变化，请等待画面刷新后重新预检查/审核")
        records = [
            dict(row)
            for row in store.db.execute(
                "SELECT * FROM annotations WHERE source=? AND start<? AND end>? ORDER BY CASE kind WHEN 'point' THEN 1 ELSE 0 END,id",
                (source, end, start),
            )
        ]
        boundaries = sorted(
            {start, end}
            | {max(start, r["start"]) for r in records}
            | {min(end, r["end"]) for r in records}
        )
        fields = [
            (arm, f)
            for f in schema["fields"]
            if f.get("evidence", "state") == mode
            for arm in (["global"] if f["scope"] == "global" else ["left", "right"])
        ]
        report = dict(
            start=start,
            end=end,
            mode=mode,
            frames=end - start,
            saved=False,
            revision_before=revision,
            schema_version=schema["version"],
            eligible_pairs=0,
            reviewed_pairs=0,
            already_reviewed_pairs=0,
            missing_pairs=0,
            inapplicable_pairs=0,
            undetermined_pairs=0,
            conflict_pairs=0,
            retired_pairs=0,
            reviewed_frames=0,
            reviewable_frames=0,
            updated_records=0,
        )
        issues, issue_last, plans = [], {}, {}
        for a, b in zip(boundaries, boundaries[1:]):
            effective = {
                (r["arm"], r["field"]): r for r in records if r["start"] <= a < r["end"]
            }
            raw = {"left": {}, "right": {}, "global": {}}
            for (arm, field), r in effective.items():
                raw[arm][field] = dict(
                    value_id=r["value"],
                    review_status=r["status"],
                    schema_version=r["version"],
                )
            prospective = copy.deepcopy(raw)
            for arm, f in fields:
                record = prospective[arm].get(f["field_id"])
                if record and any(
                    c["id"] == record["value_id"] and not c.get("deprecated")
                    for c in f["classes"]
                ):
                    record.update(
                        review_status="reviewed", schema_version=schema["version"]
                    )
            labels = resolve(schema, prospective)
            conflicts = [
                error for error in semantic_errors(labels) if "缺少必填" not in error
            ]
            segment_reviewable = segment_changed = False
            for arm, f in fields:
                field = f["field_id"]
                record = effective.get((arm, field))
                value = labels[arm][field]
                code = reason = None
                if (
                    record is not None
                    and record["value"] is not None
                    and not any(
                        c["id"] == record["value"] and not c.get("deprecated")
                        for c in f["classes"]
                    )
                ):
                    code, reason = "retired", "类别已删除或废弃，需重新标注"
                elif value["applicability"] == "not_applicable":
                    code, reason = "inapplicable", "当前阶段/规则不适用"
                elif record is None or record["value"] is None:
                    code, reason = "missing", "未填写，不补值"
                elif value["applicability"] == "undetermined":
                    code, reason = "undetermined", "依赖缺失或unknown，适用性无法确定"
                else:
                    errors = [
                        error for error in conflicts if error.startswith(arm + ":")
                    ]
                    if errors:
                        code, reason = "conflict", "；".join(errors)
                if code:
                    report[code + "_pairs"] += b - a
                    signature = arm, field, code, reason
                    previous = issue_last.get(signature)
                    if previous is not None and issues[previous]["end"] == a:
                        issues[previous]["end"] = b
                    else:
                        issue_last[signature] = len(issues)
                        issues.append(
                            dict(
                                start=a,
                                end=b,
                                arm=arm,
                                field=field,
                                code=code,
                                reason=reason,
                            )
                        )
                    continue
                report["eligible_pairs"] += b - a
                segment_reviewable = True
                provenance = json.loads(record["provenance"] or "{}")
                evidence_matches = (
                    mode != "state"
                    or provenance.get("history_config", HistoryConfig().to_dict())
                    == config
                )
                if (
                    record["status"] == "reviewed"
                    and record["version"] == schema["version"]
                    and record["mode"] == mode
                    and evidence_matches
                ):
                    report["already_reviewed_pairs"] += b - a
                    continue
                segment_changed = True
                report["reviewed_pairs"] += b - a
                plan = plans.setdefault(record["id"], dict(record=record, runs=[]))
                if plan["runs"] and plan["runs"][-1][1] == a:
                    plan["runs"][-1][1] = b
                else:
                    plan["runs"].append([a, b])
            if segment_reviewable:
                report["reviewable_frames"] += b - a
            if segment_changed:
                report["reviewed_frames"] += b - a
        report.update(
            issues=issues[:100],
            issue_groups=len(issues),
            skipped_pairs=sum(
                report[k + "_pairs"]
                for k in [
                    "missing",
                    "inapplicable",
                    "undetermined",
                    "conflict",
                    "retired",
                ]
            ),
        )
        if preview or not plans:
            report["saved"] = not preview
            report["revision_after"] = revision
            return report
        with store.transaction(f"审核已填标签 [{start},{end}) · {mode}"):
            for plan in plans.values():
                record = plan["record"]
                for a, b in plan["runs"]:
                    store._split_write(
                        "annotations",
                        "source=? AND arm=? AND field=? AND kind=?",
                        (source, record["arm"], record["field"], record["kind"]),
                        a,
                        b,
                    )
                    updated = dict(record)
                    updated.pop("id")
                    provenance = json.loads(updated["provenance"] or "{}")
                    provenance["review_scope"] = dict(start=start, end=end, mode=mode)
                    if mode == "state":
                        provenance["history_config"] = config
                    updated.update(
                        start=a,
                        end=b,
                        status="reviewed",
                        mode=mode,
                        version=schema["version"],
                        revision=store.get("revision"),
                        future_seen=int(
                            bool(record["future_seen"])
                            or store.get("future_seen:" + source, False)
                        ),
                        provenance=json.dumps(provenance),
                    )
                    store.db.execute(
                        "INSERT INTO annotations ("
                        + ",".join(updated)
                        + ") VALUES("
                        + ",".join("?" for _ in updated)
                        + ")",
                        tuple(updated.values()),
                    )
                    report["updated_records"] += 1
        report.update(saved=True, revision_after=store.get("revision"))
        return report
