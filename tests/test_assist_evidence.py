import math

import numpy as np

from dse_tool.assist import adviser_candidate


def test_reversing_translation_is_unknown_per_axis():
    # A significant out-and-back movement must not become a confident net direction.
    rows = [
        {
            "timestamp": i * 0.05,
            "pose": [x, i * 0.01, 0, 0, 0, 0, 1],
        }
        for i, x in enumerate([0, 0.02, 0.01, 0.008])
    ]
    result = adviser_candidate(
        rows, 0, lambda i: True, lambda i: 2, set(), calibration()
    )
    assert result["status"] == "suggestion"
    assert result["needs_review"] is True
    assert result["values"] == {"x": 3, "y": 1, "z": 0, "orientation": 0}


def test_rotation_out_and_back_is_unknown_instead_of_keep():
    rows = []
    for i, degrees in enumerate([0, 20, 10, 0]):
        half_angle = math.radians(degrees) / 2
        rows.append(
            {
                "timestamp": i * 0.05,
                "pose": [0, 0, 0, 0, 0, math.sin(half_angle), math.cos(half_angle)],
            }
        )
    result = adviser_candidate(
        rows, 0, lambda i: True, lambda i: 5, set(), calibration()
    )
    assert result["needs_review"] is True
    assert result["values"] == {"x": 0, "y": 0, "z": 0, "orientation": 2}


def calibration():
    return {
        "verified": True,
        "unit": "m",
        "frame": "robot_base",
        "transform": np.eye(4).tolist(),
        "feature": "pose",
    }
