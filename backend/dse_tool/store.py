import contextlib
import copy
import json
import sqlite3
import shutil
import threading
import time
from pathlib import Path
from .schema import (
    default_schema,
    validate_schema,
    digest,
    resolve,
    field_map,
    semantic_errors,
    prepare_schema,
)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if any(
            (p / "meta/info.json").exists() for p in [self.root, *self.root.parents]
        ):
            raise ValueError("工作目录不得位于数据集内部，尚未写入任何工作文件")
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._local = threading.local()
        self.db = sqlite3.connect(
            self.root / "project.sqlite3", check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS datasets(fingerprint TEXT PRIMARY KEY,root TEXT,info TEXT,report TEXT,signatures TEXT);
        CREATE TABLE IF NOT EXISTS schemas(version INTEGER PRIMARY KEY,hash TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS annotations(id INTEGER PRIMARY KEY,source TEXT,start INTEGER,end INTEGER,arm TEXT,field TEXT,value INTEGER,kind TEXT,status TEXT,mode TEXT,label_source TEXT,future_seen INTEGER,version INTEGER,revision INTEGER);
        CREATE TABLE IF NOT EXISTS selections(id INTEGER PRIMARY KEY,dataset TEXT,episode INTEGER,start INTEGER,end INTEGER);
        CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY,source TEXT,start INTEGER,end INTEGER,status TEXT,reason TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,source TEXT,frame INTEGER,arm TEXT,type TEXT,status TEXT,details TEXT);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT,before_state TEXT,after_state TEXT,description TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT);
        """)
        if not self.db.execute("SELECT 1 FROM schemas").fetchone():
            s = default_schema()
            self.db.execute(
                "INSERT INTO schemas VALUES(?,?,?)", (1, digest(s), json.dumps(s))
            )
            self.set("schema_version", 1)
            self.set("history_cursor", 0)
            self.set("history_floor", 0)
            self.set("revision", 0)
        if "provenance" not in {
            r["name"] for r in self.db.execute("PRAGMA table_info(annotations)")
        }:
            self.db.execute(
                "ALTER TABLE annotations ADD COLUMN provenance TEXT DEFAULT '{}'"
            )
        self.db.commit()
        # Interrupted tasks are recoverable failures, never apparent successes.
        for row in self.db.execute("SELECT * FROM jobs").fetchall():
            p = json.loads(row["payload"])
            if p["status"] in ["running", "queued"]:
                p.update(status="failed", error="服务重启中断；未发布临时导出")
                if p.get("destination"):
                    destination = Path(p["destination"])
                    temporary = destination.parent / (
                        "." + destination.name + ".partial-" + p["id"]
                    )
                    if temporary.is_dir() and not temporary.is_symlink():
                        shutil.rmtree(temporary)
                self.save_job(p)
        self.backup()

    @property
    def db(self):
        if not hasattr(self._local, "connection"):
            connection = sqlite3.connect(
                self.root / "project.sqlite3", timeout=30, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            self._local.connection = connection
        return self._local.connection

    @db.setter
    def db(self, connection):
        self._local.connection = connection

    def backup(self):
        target = self.root / "backups"
        target.mkdir(exist_ok=True)
        p = target / (time.strftime("%Y%m%d-%H%M%S") + ".sqlite3")
        with sqlite3.connect(p) as connection:
            self.db.backup(connection)
        for old in sorted(target.glob("*.sqlite3"))[:-10]:
            old.unlink()

    def get(self, key, default=None):
        row = self.db.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute(
            "INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value))
        )

    def schema(self):
        return json.loads(
            self.db.execute(
                "SELECT payload FROM schemas WHERE version=?",
                (self.get("schema_version", 1),),
            ).fetchone()[0]
        )

    def snapshot(self):
        return {
            t: [dict(r) for r in self.db.execute("SELECT * FROM " + t)]
            for t in ["annotations", "selections", "reviews", "events"]
        }

    def restore(self, snapshot):
        for table, rows in snapshot.items():
            self.db.execute("DELETE FROM " + table)
            for row in rows:
                self.db.execute(
                    "INSERT INTO "
                    + table
                    + " ("
                    + ",".join(row)
                    + ") VALUES("
                    + ",".join("?" for _ in row)
                    + ")",
                    tuple(row.values()),
                )

    @contextlib.contextmanager
    def transaction(self, description):
        with self.lock:
            if getattr(self._local, "transaction_depth", 0):
                yield
                return
            self._local.transaction_depth = 1
            before = self.snapshot()
            try:
                self.set("revision", self.get("revision", 0) + 1)
                yield
                after = self.snapshot()
                cursor = self.get("history_cursor", 0)
                self.db.execute("DELETE FROM history WHERE id>?", (cursor,))
                r = self.db.execute(
                    "INSERT INTO history(before_state,after_state,description,created) VALUES(?,?,?,?)",
                    (json.dumps(before), json.dumps(after), description, time.time()),
                )
                self.set("history_cursor", r.lastrowid)
                self.db.commit()
                if self.get("revision", 0) % 20 == 0:
                    self.backup()
            except Exception:
                self.db.rollback()
                raise
            finally:
                self._local.transaction_depth = 0

    def undo(self, redo=False):
        with self.lock:
            cursor = self.get("history_cursor", 0)
            if not redo and cursor <= self.get("history_floor", 0):
                return False
            row = (
                self.db.execute(
                    "SELECT * FROM history WHERE id>? ORDER BY id LIMIT 1", (cursor,)
                ).fetchone()
                if redo
                else self.db.execute(
                    "SELECT * FROM history WHERE id=?", (cursor,)
                ).fetchone()
            )
            if not row:
                return False
            self.restore(json.loads(row["after_state" if redo else "before_state"]))
            if redo:
                self.set("history_cursor", row["id"])
            else:
                prev = self.db.execute(
                    "SELECT MAX(id) FROM history WHERE id<?", (cursor,)
                ).fetchone()[0]
                self.set("history_cursor", prev or 0)
            self.set("revision", self.get("revision", 0) + 1)
            self.db.commit()
            return True

    def history_barrier(self):
        """Preserve audit history, but don't undo across a new import or schema migration."""
        cursor = self.db.execute("SELECT MAX(id) FROM history").fetchone()[0] or 0
        self.set("history_floor", cursor)
        self.set("history_cursor", cursor)

    def raw(self, source, frame):
        result = {"left": {}, "right": {}, "global": {}}
        # A point edit always wins over ranges. Ranges are disjoint after each mutation.
        rows = self.db.execute(
            "SELECT * FROM annotations WHERE source=? AND start<=? AND end>? ORDER BY CASE kind WHEN 'point' THEN 1 ELSE 0 END,id",
            (source, frame, frame),
        ).fetchall()
        for r in rows:
            result[r["arm"]][r["field"]] = {
                "value_id": r["value"],
                "review_status": r["status"],
                "evidence_mode": r["mode"],
                "label_source": r["label_source"],
                "future_seen": bool(r["future_seen"]),
                "schema_version": r["version"],
                "revision": r["revision"],
                "provenance": json.loads(r["provenance"] or "{}"),
            }
        return result

    def labels(self, source, frame, center=True):
        return resolve(self.schema(), self.raw(source, frame), center)

    def _split_write(self, table, where, args, start, end):
        rows = self.db.execute(
            "SELECT * FROM " + table + " WHERE " + where + " AND start<? AND end>?",
            (*args, end, start),
        ).fetchall()
        for row in rows:
            r = dict(row)
            self.db.execute("DELETE FROM " + table + " WHERE id=?", (r.pop("id"),))
            for a, b in [
                (r["start"], min(start, r["end"])),
                (max(end, r["start"]), r["end"]),
            ]:
                if a >= b:
                    continue
                part = {**r, "start": a, "end": b}
                self.db.execute(
                    "INSERT INTO "
                    + table
                    + " ("
                    + ",".join(part)
                    + ") VALUES("
                    + ",".join("?" for _ in part)
                    + ")",
                    tuple(part.values()),
                )
        return len(rows)

    def annotate(
        self,
        source,
        start,
        end,
        arm,
        field,
        value,
        mode,
        reviewed=False,
        confirmed=False,
        point=False,
        preview=False,
        label_source="human",
        provenance=None,
        point_policy="preserve",
        _defer_review_validation=False,
    ):
        if point_policy not in {"preserve", "overwrite"}:
            raise ValueError("未知单帧覆盖策略")
        schema = self.schema()
        fm = field_map(schema)
        if field not in fm:
            raise ValueError("未知字段")
        f = fm[field]
        if (arm == "global") != (f["scope"] == "global"):
            raise ValueError("字段作用域不匹配")
        if value is not None and value not in [
            c["id"] for c in f["classes"] if not c.get("deprecated")
        ]:
            raise ValueError("无效/废弃类别 ID")
        if mode != f.get("evidence", "state"):
            raise ValueError(
                "该字段必须切换到 " + f.get("evidence", "state") + " 模式编辑"
            )
        if reviewed and mode == "state" and not confirmed:
            raise ValueError("需要确认允许观测支持当前标签")
        eligible = []
        skipped = 0
        undetermined = 0
        for frame in range(start, end):
            labels = self.labels(source, frame)
            app = labels[arm][field]["applicability"]
            if app == "not_applicable":
                skipped += 1
                continue
            if app == "undetermined":
                undetermined += 1
            eligible.append(frame)
        overlaps = self.db.execute(
            "SELECT COUNT(*) FROM annotations WHERE source=? AND arm=? AND field=? AND start<? AND end>?",
            (source, arm, field, end, start),
        ).fetchone()[0]
        point_frames = set()
        if not point:
            for row in self.db.execute(
                "SELECT start,end FROM annotations WHERE source=? AND arm=? AND field=? AND kind='point' AND start<? AND end>?",
                (source, arm, field, end, start),
            ):
                point_frames.update(
                    range(max(start, row["start"]), min(end, row["end"]))
                )
        overwritten_points = (
            len(point_frames.intersection(eligible))
            if point_policy == "overwrite"
            else 0
        )
        report = {
            "affected": len(eligible),
            "skipped": skipped,
            "needs_review": undetermined,
            "overlaps": overlaps,
            "point_overrides": len(point_frames) - overwritten_points,
            "overwritten_points": overwritten_points,
        }
        if preview:
            return report
        changed_frames = [
            frame
            for frame in eligible
            if self.raw(source, frame).get(arm, {}).get(field, {}).get("value_id")
            != value
        ]
        with self.transaction(f"{arm}.{field} [{start},{end})"):
            runs = []
            for frame in eligible:
                if runs and runs[-1][1] == frame:
                    runs[-1][1] = frame + 1
                else:
                    runs.append([frame, frame + 1])
            for a, b in runs:
                kind = "point" if point else "range"
                self._split_write(
                    "annotations",
                    "source=? AND arm=? AND field=? AND kind=?",
                    (source, arm, field, kind),
                    a,
                    b,
                )
                if not point and point_policy == "overwrite":
                    self._split_write(
                        "annotations",
                        "source=? AND arm=? AND field=? AND kind='point'",
                        (source, arm, field),
                        a,
                        b,
                    )
                self.db.execute(
                    "INSERT INTO annotations(source,start,end,arm,field,value,kind,status,mode,label_source,future_seen,version,revision,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        source,
                        a,
                        b,
                        arm,
                        field,
                        value,
                        kind,
                        "reviewed" if reviewed else "draft",
                        mode,
                        label_source,
                        int(self.get("future_seen:" + source, False)),
                        schema["version"],
                        self.get("revision"),
                        json.dumps(provenance or {}),
                    ),
                )
            if changed_frames:
                from .schema import references

                dependents = set()
                pending = {(arm, field)}
                while pending:
                    parent_arm, parent_field = pending.pop()
                    for other in schema["fields"]:
                        refs = [
                            r
                            for rule in [
                                other["applicable_when"],
                                other["required_when"],
                            ]
                            for r in references(rule)
                        ]
                        for child_arm in (
                            ["global"]
                            if other["scope"] == "global"
                            else ["left", "right"]
                        ):
                            node = (child_arm, other["field_id"])
                            if node in dependents:
                                continue
                            if any(
                                (
                                    child_arm
                                    if ref.split(".", 1)[0] == "self"
                                    else ref.split(".", 1)[0],
                                    ref.split(".", 1)[1],
                                )
                                == (parent_arm, parent_field)
                                for ref in refs
                            ):
                                dependents.add(node)
                                pending.add(node)
                for dependent_arm, dependent in dependents:
                    for frame in changed_frames:
                        deps = self.db.execute(
                            "SELECT * FROM annotations WHERE source=? AND arm=? AND field=? AND start<=? AND end>?",
                            (source, dependent_arm, dependent, frame, frame),
                        ).fetchall()
                        for dep in deps:
                            r = dict(dep)
                            r.pop("id")
                            kind = r["kind"]
                            dep_arm = r["arm"]
                            self._split_write(
                                "annotations",
                                "source=? AND arm=? AND field=? AND kind=?",
                                (source, dep_arm, dependent, kind),
                                frame,
                                frame + 1,
                            )
                            r.update(
                                start=frame,
                                end=frame + 1,
                                status="needs_review",
                                revision=self.get("revision"),
                            )
                            self.db.execute(
                                "INSERT INTO annotations ("
                                + ",".join(r)
                                + ") VALUES("
                                + ",".join("?" for _ in r)
                                + ")",
                                tuple(r.values()),
                            )
            if reviewed and not _defer_review_validation:
                for frame in eligible:
                    errors = [
                        e
                        for e in semantic_errors(self.labels(source, frame))
                        if "缺少必填" not in e and e.startswith(arm + ":")
                    ]
                    if errors:
                        raise ValueError("；".join(errors))
        return report

    def annotate_batch(
        self, changes, *, preview=False, expected_version=None, focus=None
    ):
        """Evaluate dependencies together; one transaction and one undo entry per batch."""
        from .schema import references

        with self.lock:
            schema = self.schema()
            if expected_version is not None and expected_version != schema["version"]:
                raise ValueError("schema 已变更，请取消旧草稿并按当前版本重新填写")
            fields = field_map(schema)
            pending = list(enumerate(changes))
            ordered = []
            # Order phase/gates before their children, regardless of UI editing order.
            while pending:
                remaining_nodes = {(r["arm"], r["field"]) for _, r in pending}
                ready = []
                for index, change in pending:
                    field = fields.get(change["field"])
                    if field is None:
                        raise ValueError("未知字段: " + change["field"])
                    refs = [
                        ref
                        for key in ["applicable_when", "required_when"]
                        for ref in references(field[key])
                    ]
                    deps = {
                        (
                            change["arm"]
                            if ref.split(".", 1)[0] == "self"
                            else ref.split(".", 1)[0],
                            ref.split(".", 1)[1],
                        )
                        for ref in refs
                    }
                    if not deps & remaining_nodes:
                        ready.append((index, change))
                if not ready:
                    raise ValueError("批量字段依赖无法排序")
                ordered.extend(ready)
                ready_indices = {i for i, _ in ready}
                pending = [(i, r) for i, r in pending if i not in ready_indices]
            original_focus = (
                self.raw(focus["source"], focus["frame"]) if focus else None
            )

            def apply():
                reports = []
                for index, change in ordered:
                    report = self.annotate(**change, _defer_review_validation=True)
                    reports.append({"index": index, **report})
                for change in changes:
                    if change.get("reviewed"):
                        for frame in range(change["start"], change["end"]):
                            errors = [
                                e
                                for e in semantic_errors(
                                    self.labels(change["source"], frame)
                                )
                                if "缺少必填" not in e
                                and e.startswith(change["arm"] + ":")
                            ]
                            if errors:
                                raise ValueError("；".join(errors))
                result = {
                    "reports": sorted(reports, key=lambda r: r["index"]),
                    "saved": not preview,
                }
                if focus:
                    result["labels"] = self.labels(
                        focus["source"], focus["frame"], focus.get("center", True)
                    )
                    # The form shows assignment targets; preserved point overrides are
                    # reported separately rather than disguising them as range values.
                    planned = copy.deepcopy(original_focus)
                    for _, change in ordered:
                        if (
                            change["source"] == focus["source"]
                            and change["start"] <= focus["frame"] < change["end"]
                        ):
                            planned[change["arm"]][change["field"]] = {
                                "value_id": change["value"],
                                "review_status": "draft",
                                "schema_version": schema["version"],
                            }
                    result["form_labels"] = resolve(schema, planned, False)
                return result

            if preview:
                self.db.execute("SAVEPOINT batch_preview")
                previous_depth = getattr(self._local, "transaction_depth", 0)
                self._local.transaction_depth = 1
                try:
                    return apply()
                finally:
                    self.db.execute("ROLLBACK TO batch_preview")
                    self.db.execute("RELEASE batch_preview")
                    self._local.transaction_depth = previous_depth
            with self.transaction(f"整组标注 · {len(changes)} 项"):
                return apply()

    def select(self, dataset, episode, start, end, remove=False):
        with self.transaction("训练中心选区"):
            self._split_write(
                "selections", "dataset=? AND episode=?", (dataset, episode), start, end
            )
            if not remove:
                self.db.execute(
                    "INSERT INTO selections(dataset,episode,start,end) VALUES(?,?,?,?)",
                    (dataset, episode, start, end),
                )
            rows = [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM selections WHERE dataset=? AND episode=? ORDER BY start",
                    (dataset, episode),
                )
            ]
            merged = []
            for r in rows:
                if merged and r["start"] <= merged[-1]["end"]:
                    merged[-1]["end"] = max(merged[-1]["end"], r["end"])
                else:
                    merged.append(r)
            self.db.execute(
                "DELETE FROM selections WHERE dataset=? AND episode=?",
                (dataset, episode),
            )
            for r in merged:
                self.db.execute(
                    "INSERT INTO selections(dataset,episode,start,end) VALUES(?,?,?,?)",
                    (dataset, episode, r["start"], r["end"]),
                )

    def publish(self, schema, preview=False):
        old = self.schema()
        schema = prepare_schema(schema, old)
        validate_schema(schema, old)
        changed = [
            key
            for key in field_map(schema).keys() | field_map(old).keys()
            if field_map(old).get(key) != field_map(schema).get(key)
        ]
        report = {
            "changed_fields": changed,
            "invalidated_labels": self.db.execute(
                "SELECT COUNT(*) FROM annotations"
            ).fetchone()[0],
        }
        if preview:
            return report
        with self.lock:
            schema = copy.deepcopy(schema)
            schema["version"] = (
                self.db.execute("SELECT MAX(version) FROM schemas").fetchone()[0] + 1
            )
            self.db.execute(
                "INSERT INTO schemas VALUES(?,?,?)",
                (schema["version"], digest(schema), json.dumps(schema)),
            )
            self.set("schema_version", schema["version"])
            self.db.execute("UPDATE annotations SET status='needs_review'")
            self.history_barrier()
            self.db.commit()
            (self.root / "schemas").mkdir(exist_ok=True)
            (self.root / "schemas" / f"v{schema['version']}.json").write_text(
                json.dumps(schema, ensure_ascii=False, indent=2)
            )
        return report

    def save_job(self, payload):
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO jobs VALUES(?,?)",
                (payload["id"], json.dumps(payload)),
            )
            self.db.commit()
