import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dse_tool.adapter import (
    DSEDataset,
    ee_pose_features,
    proprioception,
    proprioception36,
)
from dse_tool.dataset import Dataset, content_fingerprint
from dse_tool.exporter import Exporter
from dse_tool.store import Store


@pytest.mark.parametrize("dimension", [16, 48])
def test_arbitrary_state_and_action_dimensions_survive_export(
    source, tmp_path, dimension
):
    info_path = source / "meta/info.json"
    info = json.loads(info_path.read_text())
    # 动作向量维数也可与 state 不同，不通过补零或裁剪强行对齐。
    dimensions = {"observation.state": dimension, "action": dimension + 2}
    for key, size in dimensions.items():
        info["features"][key].update(
            shape=[size], names=[f"channel_{i}" for i in range(size)]
        )
    info_path.write_text(json.dumps(info))
    data_path = source / "data/chunk-000/episode_000000.parquet"
    rows = pq.read_table(data_path).to_pylist()
    for row in rows:
        for key, size in dimensions.items():
            row[key] = [float(row["frame_index"] + i) for i in range(size)]
    pq.write_table(pa.Table.from_pylist(rows), data_path)
    before = content_fingerprint(source)[0]
    dataset = Dataset(source, tmp_path / "cache")
    dataset.validate(decode=False)
    store = Store(tmp_path / "work")
    store.select(before, 0, 20, 23)
    source_id, _, _ = dataset.source_key(0, 20, before)
    store.annotate(source_id, 20, 23, "left", "phase", 8, "state", True, True)
    exporter = Exporter(store)
    destination = tmp_path / "export"
    job = exporter.start(dataset, before, destination)
    exporter.threads[job["id"]].join(30)
    assert job["status"] == "complete", job
    exported = Dataset(destination, tmp_path / "out-cache")
    exported.validate(decode=False)
    for key, size in dimensions.items():
        assert exported.info["features"][key]["shape"] == [size]
        assert exported.info["features"][key]["names"] == info["features"][key]["names"]
    for (episode, frame), mapping in exported.source_map.items():
        original = rows[mapping["source_frame_index"]]
        actual = exported.rows(episode)[frame]
        for key in dimensions:
            assert actual[key] == original[key]
    batch = DSEDataset(destination, tmp_path / "reader-cache").batch([0, 1])
    assert batch["state"].shape == (2, 4, dimension)
    config = {
        "verified": True,
        "unit": "m",
        "frame": "robot_base",
        "transform": np.eye(4).tolist(),
    }
    converted = proprioception(batch["state"], batch["ee_pose_feedback"], config)
    assert converted.shape == (2, 4, dimension + 18)
    np.testing.assert_array_equal(converted[..., :dimension], batch["state"])
    if dimension != 18:
        with pytest.raises(ValueError, match="proprioception"):
            proprioception36(batch["state"], batch["ee_pose_feedback"], config)
    assert content_fingerprint(source)[0] == before
    store.db.close()


def test_state_shape_checked_even_without_names(source, tmp_path):
    info_path = source / "meta/info.json"
    info = json.loads(info_path.read_text())
    info["features"]["observation.state"].update(shape=[16], names=None)
    info_path.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="数值特征维度错误"):
        Dataset(source, tmp_path / "cache").validate(decode=False)


def test_proprioception_checks_leading_dimensions():
    config = {
        "verified": True,
        "unit": "m",
        "frame": "robot_base",
        "transform": np.eye(4).tolist(),
    }
    with pytest.raises(ValueError, match="前导维度"):
        proprioception(np.zeros((2, 16)), np.zeros((1, 2, 7)), config)


def test_shared_pose_features_with_rotation_and_translation():
    config = {
        "verified": True,
        "unit": "m",
        "frame": "robot_base",
        "transform": [[0, -1, 0, 1], [1, 0, 0, 2], [0, 0, 1, 3], [0, 0, 0, 1]],
    }
    # 90°源旋转叠加90°基座旋转，得到180°旋转；不依赖任何关节状态布局。
    pose = np.array([1, 0, 0, 0, 0, np.sqrt(0.5), np.sqrt(0.5)])
    expected = np.array([1, 3, 3, -1, 0, 0, 0, -1, 0])
    np.testing.assert_allclose(ee_pose_features(pose, config), expected, atol=1e-12)
    poses = np.broadcast_to(pose, (2, 3, 2, 7))
    actual = ee_pose_features(poses, config)
    assert actual.shape == (2, 3, 2, 9)
    np.testing.assert_allclose(
        actual, np.broadcast_to(expected, actual.shape), atol=1e-12
    )
    for dimension in (16, 18, 31, 48):
        state = np.arange(2 * 3 * dimension).reshape(2, 3, dimension)
        result = proprioception(state, poses, config)
        np.testing.assert_array_equal(result[..., :dimension], state)
        np.testing.assert_allclose(result[..., dimension:], actual.reshape(2, 3, 18))
    legacy = proprioception36(np.zeros((2, 3, 18)), poses, config)
    np.testing.assert_allclose(legacy[..., 18:], actual.reshape(2, 3, 18))


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_shared_pose_features_reject_nonfinite(bad):
    config = {
        "verified": True,
        "unit": "m",
        "frame": "robot_base",
        "transform": np.eye(4).tolist(),
    }
    pose = np.array([bad, 0, 0, 0, 0, 0, 1])
    with pytest.raises(ValueError, match="非有限"):
        ee_pose_features(pose, config)
