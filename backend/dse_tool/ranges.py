"""Evaluate the effective annotation rows across a half-open frame range."""

from heapq import heappop, heappush
from itertools import pairwise


def effective_ranges(records, start, end):
    if start >= end:
        return
    # Records are ordered by write precedence. The last row in each field
    # wins, including when a point overrides a range.
    boundaries = {start: [], end: []}
    for priority, row in enumerate(records):
        lower, upper = max(start, row["start"]), min(end, row["end"])
        if lower < upper:
            boundaries.setdefault(lower, []).append((-priority, upper, row))
            boundaries.setdefault(upper, [])
    active = {}
    for lower, upper in pairwise(sorted(boundaries)):
        for item in boundaries[lower]:
            row = item[2]
            heappush(active.setdefault((row["arm"], row["field"]), []), item)
        effective = {}
        for key, heap in active.items():
            while heap and heap[0][1] <= lower:
                heappop(heap)
            if heap:
                effective[key] = heap[0][2]
        yield lower, upper, effective


def gate_records(effective):
    raw = {"left": {}, "right": {}, "global": {}}
    for (arm, field), row in effective.items():
        raw[arm][field] = {
            "value_id": row["value"],
            "review_status": row["status"],
            "schema_version": row["version"],
        }
    return raw
