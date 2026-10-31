"""Shared causal history sampling contract for UI, export and training."""

from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class HistoryConfig:
    horizon_seconds: float = 0.6
    num_frames: int = 4  # Total timestamps per camera, including current t.

    def __post_init__(self):
        if (
            isinstance(self.horizon_seconds, bool)
            or not isinstance(self.horizon_seconds, (int, float))
            or not math.isfinite(self.horizon_seconds)
            or not 0 <= self.horizon_seconds <= 3600
        ):
            raise ValueError("历史跨度必须为0–3600秒的有限数")
        if type(self.num_frames) is not int or not 1 <= self.num_frames <= 32:
            raise ValueError("历史时刻数必须为1–32的整数（含当前帧）")
        if (self.num_frames == 1) != (self.horizon_seconds == 0):
            raise ValueError("单时刻须设跨度0；多时刻须设正跨度")

    @property
    def offsets(self):
        return np.linspace(self.horizon_seconds, 0, self.num_frames).tolist()

    def to_dict(self):
        return {
            "horizon_seconds": self.horizon_seconds,
            "num_frames": self.num_frames,
            "offsets_seconds": self.offsets,
            "sampling": "at_or_before",
            "includes_current": True,
        }

    @classmethod
    def from_dict(cls, config=None):
        if config is None:
            return cls()
        if isinstance(config, cls):
            return config
        for key, expected in [("sampling", "at_or_before"), ("includes_current", True)]:
            if key in config and config[key] != expected:
                raise ValueError("不支持的历史采样约定: " + key)
        value = cls(config.get("horizon_seconds", 0.6), config.get("num_frames", 4))
        if "offsets_seconds" in config and config["offsets_seconds"] != value.offsets:
            raise ValueError("历史偏移与跨度/时刻数不一致")
        return value


def sample_history(timestamps, frame, config=None):
    """Use actual timestamps; never round to a row after the requested target."""
    config = HistoryConfig.from_dict(config)
    times = np.asarray(timestamps, dtype=np.float64)
    now = times[frame]
    result = []
    for offset in config.offsets:
        target = now - offset
        # Small epsilon only compensates timestamp arithmetic/float32 storage.
        index = int(np.searchsorted(times, target + 1e-8, side="right") - 1)
        index = min(index, frame)
        valid = index >= 0 and target >= times[0] - 1e-8
        result.append(
            {
                "offset_seconds": offset,
                "target_timestamp": float(target),
                "frame": index if valid else None,
                "timestamp": float(times[index]) if valid else None,
                "relative_timestamp": float(times[index] - now) if valid else None,
            }
        )
    return result
