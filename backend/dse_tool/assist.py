"""Calibration-gated suggestions. No suggestion is a reviewed causal label."""

import math
import numpy as np


def event_candidates(rows, feature, dimension, config):
    required = [
        "verified",
        "unit",
        "closed_threshold",
        "open_threshold",
        "closed_below",
        "min_duration",
    ]
    if (
        any(k not in config for k in required)
        or not config["verified"]
        or not config["unit"]
    ):
        raise ValueError("先核对单位、开闭方向、双阈值滞回与持续时间")
    close, opened = config["closed_threshold"], config["open_threshold"]
    if (config["closed_below"] and close >= opened) or (
        not config["closed_below"] and close <= opened
    ):
        raise ValueError("滞回阈值方向错误")
    events = []
    stable = None
    pending = None
    since = 0
    for i, row in enumerate(rows):
        x = row[feature][dimension]
        candidate = (
            ("closed" if x <= close else "open" if x >= opened else None)
            if config["closed_below"]
            else ("closed" if x >= close else "open" if x <= opened else None)
        )
        if candidate is None:
            pending = None
            continue
        if candidate != pending:
            pending = candidate
            since = i
        if (
            row["timestamp"] - rows[since]["timestamp"] >= config["min_duration"]
            and candidate != stable
        ):
            if stable is not None:
                events.append(
                    {
                        "frame": since,
                        "type": candidate,
                        "status": "suggestion",
                        "details": {
                            "feature": feature,
                            "dimension": dimension,
                            "calibration": config,
                            "rule_version": "hysteresis-1",
                        },
                    }
                )
            stable = candidate
    return events


def adviser_candidate(rows, start, approved, phases, event_frames, config):
    if (
        not config.get("verified")
        or config.get("unit") != "m"
        or config.get("frame") != "robot_base"
    ):
        raise ValueError("坐标/米制单位未验证，自动方向预标注已禁用")
    transform = np.asarray(config.get("transform", []), dtype=float)
    if (
        transform.shape != (4, 4)
        or not np.allclose(transform[3], [0, 0, 0, 1])
        or not np.allclose(
            transform[:3, :3] @ transform[:3, :3].T, np.eye(3), atol=1e-5
        )
        or not np.isclose(np.linalg.det(transform[:3, :3]), 1)
    ):
        raise ValueError("需要已验证的刚体 4×4 坐标变换")
    if phases(start) not in [2, 5]:
        return {
            "status": "missing",
            "reason": "adviser 需要已明确的 grasp_align/place_align 阶段",
        }
    stop = start
    for i in range(start + 1, len(rows)):
        if (
            rows[i]["timestamp"] - rows[start]["timestamp"] > 0.25 + 1e-8
            or not approved(i - 1)
            or phases(i) != phases(start)
            or i in event_frames
        ):
            break
        stop = i
    if rows[stop]["timestamp"] - rows[start]["timestamp"] < 0.1 - 1e-8:
        return {"status": "missing", "reason": "连续 approved 窗口不足0.1秒"}
    key = config["feature"]
    positions = (
        np.asarray([r[key][:3] for r in rows[start : stop + 1]]) @ transform[:3, :3].T
    )
    delta = positions[-1] - positions[0]
    dead = max(0.005, 3 * config.get("position_noise", 0))
    steps = np.diff(positions, axis=0)
    cancellation = np.sum(abs(steps), axis=0) - abs(delta)
    values = {
        axis: (0 if abs(d) < dead else 1 if d > 0 else 2)
        for axis, d in zip(["x", "y", "z"], delta)
    }
    q0 = np.asarray(rows[start][key][3:])
    q1 = np.asarray(rows[stop][key][3:])
    if np.linalg.norm(q0) < 1e-8 or np.linalg.norm(q1) < 1e-8:
        return {"status": "missing", "reason": "无效四元数"}
    angle = 2 * math.acos(
        float(
            np.clip(abs(np.dot(q0 / np.linalg.norm(q0), q1 / np.linalg.norm(q1))), 0, 1)
        )
    )
    angular_path = 0.0
    for a, b in zip(rows[start:stop], rows[start + 1 : stop + 1]):
        qa = np.asarray(a[key][3:])
        qb = np.asarray(b[key][3:])
        if np.linalg.norm(qa) < 1e-8 or np.linalg.norm(qb) < 1e-8:
            return {"status": "missing", "reason": "窗口含无效四元数"}
        angular_path += 2 * math.acos(
            float(
                np.clip(
                    abs(np.dot(qa / np.linalg.norm(qa), qb / np.linalg.norm(qb))), 0, 1
                )
            )
        )
    values["orientation"] = int(
        angle > max(math.radians(5), 3 * config.get("angle_noise", 0))
    )
    return {
        "status": "suggestion",
        "values": values,
        "start": start,
        "end": stop + 1,
        "duration": rows[stop]["timestamp"] - rows[start]["timestamp"],
        "needs_review": bool(
            np.any(cancellation > dead)
            or angular_path - angle
            > max(math.radians(5), 3 * config.get("angle_noise", 0))
        ),
        "rule_version": "approved-displacement-1",
        "coordinate_config": config,
    }
