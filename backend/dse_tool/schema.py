"""Shared, non-executable rule AST; None is an undetermined truth value."""

import copy
import hashlib
import json

STATE_FIELDS = {"phase", "holding", "close_readiness", "release_readiness"}


def digest(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def default_schema():
    phase = [
        "idle|空闲",
        "approach|接近",
        "grasp_align|抓取对准",
        "grasp_check|抓持检查",
        "transport|搬运",
        "place_align|放置对准",
        "withdraw|撤离",
        "recover|恢复重试",
        "unknown|无法判断",
    ]
    definitions = [
        ("phase", phase, 8, True),
        (
            "holding",
            ["empty|空手", "holding|持物", "unknown|无法判断"],
            2,
            [1, 2, 3, 4, 5, 6, 7, 8],
        ),
        (
            "close_readiness",
            ["not_ready|条件不具备", "ready|条件具备", "unknown|无法判断"],
            2,
            [2],
        ),
        (
            "release_readiness",
            ["not_ready|条件不具备", "ready|条件具备", "unknown|无法判断"],
            2,
            [4, 5],
        ),
    ]
    definitions += [
        (
            "adviser." + axis,
            [
                "none|无显著位移",
                "positive|正方向",
                "negative|负方向",
                "unknown|无法判断",
            ],
            3,
            [2, 5],
        )
        for axis in ["x", "y", "z"]
    ]
    definitions += [
        (
            "adviser.orientation",
            ["keep|无显著调整", "adjust|调整姿态", "unknown|无法判断"],
            2,
            [2, 5],
        )
    ]
    fields = []
    colors = [
        "#718096",
        "#60a5fa",
        "#34d399",
        "#fbbf24",
        "#c084fc",
        "#fb923c",
        "#22d3ee",
        "#f87171",
        "#94a3b8",
    ]
    for key, classes, unknown, gate in definitions:
        condition = (
            True
            if gate is True
            else {"field": "self.phase", "op": "in", "values": gate}
        )
        fields.append(
            {
                "field_id": key,
                "label": key,
                "description": "",
                "scope": "each_arm",
                "type": "categorical",
                "classes": [
                    {
                        "id": i,
                        "key": s.split("|")[0],
                        "label_zh": s.split("|")[1],
                        "color": colors[i],
                        "shortcut": "",
                    }
                    for i, s in enumerate(classes)
                ],
                "unknown_id": unknown,
                "applicable_when": condition,
                "required_when": condition,
                "evidence": "behavior" if key.startswith("adviser.") else "state",
            }
        )
    fields[1]["independent_when_missing"] = ["self.phase"]
    descriptions = {
        "phase": "左右臂独立判断；静止不自动等于 idle；证据不足允许 unknown。",
        "holding": "针对指定目标，需要可见夹持关系或共同运动；不能只看夹爪开合。",
        "close_readiness": "保持当前 TCP 现在闭合：检查目标位置、夹指高度/朝向/开口、闭合路径和运动条件。",
        "release_readiness": "保持当前 TCP 打开：检查承接位置、支撑、运动与脱离阻碍；ready 仅限 place_align 且 holding。",
    }
    for field in fields:
        field["description"] = descriptions.get(
            field["field_id"],
            "规范基座系 +X前、+Y机器人左、+Z上；仅复核示教短窗口运动，none/keep 不代表对准。",
        )
    return {"version": 1, "fields": fields}


def field_map(schema):
    return {f["field_id"]: f for f in schema["fields"]}


def references(rule):
    if isinstance(rule, bool):
        return []
    if not isinstance(rule, dict):
        raise ValueError("规则必须是布尔值或条件树")
    if "field" in rule:
        if set(rule) - {"field", "op", "value", "values"}:
            raise ValueError("条件树不能混用多个操作节点")
        return [rule["field"]]
    if len(rule) != 1:
        raise ValueError("每个条件树节点只能有一个操作")
    if "not" in rule:
        return references(rule["not"])
    for op in ["all", "any"]:
        if op in rule:
            if not isinstance(rule[op], list):
                raise ValueError("all/any 需要条件数组")
            return sum((references(x) for x in rule[op]), [])
    raise ValueError("规则需要 all / any / not / field")


def validate_schema(schema, previous=None):
    fields = schema.get("fields", [])
    ids = [f["field_id"] for f in fields]
    if not fields or len(set(ids)) != len(ids) or "phase" not in ids:
        raise ValueError("字段 key 重复或缺少 phase")
    fm = field_map(schema)
    graph = {}
    for f in fields:
        key = f["field_id"]
        if (
            not isinstance(key, str)
            or not key.strip()
            or any(c in key for c in [" ", ":", "/"])
        ):
            raise ValueError("字段 key 包含无效字符")
        if key in STATE_FIELDS and f.get("evidence", "state") != "state":
            raise ValueError("固定状态字段必须使用状态证据")
        if key.startswith("adviser.") and f.get("evidence") != "behavior":
            raise ValueError("adviser 必须使用行为证据")
        if key in STATE_FIELDS and f.get("scope") != "each_arm":
            raise ValueError("固定状态字段必须按臂独立")
        if f.get("scope") not in ["each_arm", "global"]:
            raise ValueError("无效作用域")
        cs = f["classes"]
        nums = [c["id"] for c in cs]
        keys = [c["key"] for c in cs]
        if (
            not cs
            or any(type(i) is not int or i < 0 for i in nums)
            or len(set(nums)) != len(nums)
            or len(set(keys)) != len(keys)
        ):
            raise ValueError(key + ": 类别 ID/key 重复或无效")
        if f["unknown_id"] not in nums:
            raise ValueError(key + ": unknown ID 不存在")
        graph[key] = set()
        for rule in [f["applicable_when"], f["required_when"]]:

            def check(node):
                if isinstance(node, bool):
                    return
                refs = references(node)
                if "field" in node:
                    prefix, target = node["field"].split(".", 1)
                    if (
                        prefix not in ["self", "left", "right", "global"]
                        or target not in fm
                    ):
                        raise ValueError("不存在的字段引用: " + node["field"])
                    if (prefix == "global") != (fm[target]["scope"] == "global"):
                        raise ValueError("字段引用作用域不匹配")
                    if node.get("op") not in ["eq", "ne", "in"]:
                        raise ValueError("不支持的条件操作符")
                    values = (
                        node.get("values", [])
                        if node["op"] == "in"
                        else [node.get("value")]
                    )
                    if not values or any(
                        v not in [c["id"] for c in fm[target]["classes"]]
                        for v in values
                    ):
                        raise ValueError("条件引用无效类别")
                    if (
                        f.get("evidence", "state") == "state"
                        and fm[target].get("evidence", "state") == "behavior"
                    ):
                        raise ValueError("状态字段门控不能引用未来行为字段")
                    graph[key].add(target)
                else:
                    for op in ["all", "any"]:
                        if op in node:
                            if not node[op]:
                                raise ValueError("条件组不能为空")
                            for child in node[op]:
                                check(child)
                    if "not" in node:
                        check(node["not"])

            check(rule)

    def visit(key, path):
        if key in path:
            raise ValueError("循环依赖: " + " → ".join(path + [key]))
        for dep in graph[key]:
            visit(dep, path + [key])

    for key in graph:
        visit(key, [])
    if previous:
        for key, old in field_map(previous).items():
            if key not in fm:
                raise ValueError("请废弃字段而非删除: " + key)
            nc = {c["id"]: c for c in fm[key]["classes"]}
            for c in old["classes"]:
                if c["id"] not in nc or nc[c["id"]]["key"] != c["key"]:
                    raise ValueError("稳定类别 ID/key 不可删除或复用: " + key)
    return schema


def evaluate(rule, values, arm, schema):
    if isinstance(rule, bool):
        return rule
    if "all" in rule:
        r = [evaluate(x, values, arm, schema) for x in rule["all"]]
        return False if False in r else None if None in r else True
    if "any" in rule:
        r = [evaluate(x, values, arm, schema) for x in rule["any"]]
        return True if True in r else None if None in r else False
    if "not" in rule:
        v = evaluate(rule["not"], values, arm, schema)
        return None if v is None else not v
    prefix, key = rule["field"].split(".", 1)
    scope = arm if prefix == "self" else prefix
    value = values.get(scope, {}).get(key)
    if value is None:
        return None
    targets = rule.get("values", []) if rule["op"] == "in" else [rule["value"]]
    unknown = field_map(schema)[key]["unknown_id"]
    if value == unknown and unknown not in targets:
        return None
    return value not in targets if rule["op"] == "ne" else value in targets


def resolve(schema, raw, center=True):
    values = {a: {k: r["value_id"] for k, r in rs.items()} for a, rs in raw.items()}
    out = {}
    for arm in ["left", "right", "global"]:
        out[arm] = {}
        for f in schema["fields"]:
            if (arm == "global") != (f["scope"] == "global"):
                continue
            key = f["field_id"]
            r = copy.deepcopy(raw.get(arm, {}).get(key, {}))
            app = evaluate(f["applicable_when"], values, arm, schema)
            req = evaluate(f["required_when"], values, arm, schema)
            independent = f.get("independent_when_missing", [])
            if (
                app is None
                and independent
                and r.get("review_status") == "reviewed"
                and all(
                    values.get(
                        arm if ref.split(".", 1)[0] == "self" else ref.split(".", 1)[0],
                        {},
                    ).get(ref.split(".", 1)[1])
                    is None
                    for ref in independent
                )
            ):
                app = True
            r.update(
                field_id=key,
                value_id=r.get("value_id"),
                applicability="applicable"
                if app is True
                else "not_applicable"
                if app is False
                else "undetermined",
                required=req,
                annotation_status="labeled"
                if r.get("value_id") is not None
                else "missing",
                review_status=r.get("review_status", "draft"),
            )
            r["effective_loss_mask"] = int(
                center
                and app is True
                and r["annotation_status"] == "labeled"
                and r["review_status"] == "reviewed"
                and r.get("schema_version") == schema["version"]
            )
            r["reason"] = (
                "阶段/条件明确不适用"
                if app is False
                else "依赖缺失或 unknown"
                if app is None
                else ""
            )
            out[arm][key] = r
    return out


def semantic_errors(labels):
    errors = []
    for arm in ["left", "right"]:
        rs = labels[arm]
        val = lambda k: rs.get(k, {}).get("value_id")
        phase, holding, release = val("phase"), val("holding"), val("release_readiness")
        if (
            rs.get("release_readiness", {}).get("applicability") == "applicable"
            and release == 1
            and (phase != 5 or holding != 1)
        ):
            errors.append(arm + ": release ready 需要 place_align 且 holding")
        if phase in [4, 5] and holding == 0:
            errors.append(arm + ": 搬运/放置阶段配 empty 需要复核")
        if phase == 2 and holding == 1:
            errors.append(arm + ": grasp_align 配 holding 需要复核")
        for key, r in rs.items():
            if r["required"] is True and r["annotation_status"] == "missing":
                errors.append(arm + ": 缺少必填 " + key)
    return errors


def head_spec(schema):
    return {
        "schema_version": schema["version"],
        "schema_hash": digest(schema),
        "categorical_heads": [
            {
                "field_id": f["field_id"],
                "scope": f["scope"],
                "classes": f["classes"],
                "size": len(f["classes"]),
                "class_ids": [c["id"] for c in f["classes"]],
            }
            for f in schema["fields"]
        ],
        "note": "新增类别需要对应模型分类头；稳定 ID 不是连续回归目标",
    }
