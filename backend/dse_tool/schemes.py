"""Independent annotation databases over a shared, read-only source registry."""

import contextvars
import json
import shutil
import uuid
import time

from .exporter import Exporter
from .schema import digest, import_schema
from .storage import backup_paths
from .store import Store


class Scoped:
    def __init__(self, context, attribute):
        self.context, self.attribute = context, attribute

    def __getattr__(self, name):
        return getattr(self.context.get()[self.attribute], name)


class Schemes:
    def __init__(self, root):
        self.base = Store(root, startup_backup=False)
        if not self.base.get("scheme_info"):
            self.base.set(
                "scheme_info",
                {
                    "id": "default",
                    "name": "原有标注方案",
                    "project_id": str(uuid.uuid4()),
                },
            )
            self.base.db.commit()
        self.base.backup()
        self.instances = {"default": (self.base, Exporter(self.base))}
        self.context = contextvars.ContextVar(
            "annotation_scheme", default=self.instances["default"]
        )
        self.store = Scoped(self.context, 0)
        self.exporter = Scoped(self.context, 1)

    def list(self, status="active"):
        states = self.base.get("scheme_states", {})
        entries = [
            self.base.get("scheme_info"),
            *self.base.get("annotation_schemes", []),
        ]
        return [
            s
            for s in entries
            if states.get(s["id"], {}).get("status", "active") == status
        ]

    def catalog(self):
        with self.base.lock:
            trash = []
            for info in self.list("trashed") + self.list("deleting"):
                state = self.base.get("scheme_states", {})[info["id"]]
                trash.append({**info, **state})
            return {"schemes": self.list(), "trash": trash}

    def wait_for_backups(self):
        for store, _ in self.instances.values():
            store.backups.wait()

    def lifecycle(self, key, action, confirmation=None):
        with self.base.lock:
            states = self.base.get("scheme_states", {})
            state = states.get(key, {"status": "active"})
            entries = [
                self.base.get("scheme_info"),
                *self.base.get("annotation_schemes", []),
            ]
            info = next((s for s in entries if s["id"] == key), None)
            if info is None or state["status"] == "deleted":
                raise ValueError("标注方案已删除或不存在，请重新选择")
            if action == "trash":
                if state["status"] != "active":
                    return self.catalog()
                store, exporter = self.get(key)
                if any(t.is_alive() for t in exporter.threads.values()):
                    raise ValueError("该方案正在导出，请等待导出完成后再删除")
                states[key] = {
                    "status": "trashed",
                    "deleted_at": time.time(),
                    "annotation_count": store.db.execute(
                        "SELECT COUNT(*) FROM annotations"
                    ).fetchone()[0],
                }
            elif action == "restore":
                if state["status"] != "trashed":
                    raise ValueError("仅可恢复回收站中的方案")
                states.pop(key)
            elif action == "purge":
                if state["status"] not in ("trashed", "deleting"):
                    raise ValueError("请先将方案移入回收站")
                if confirmation != info["name"]:
                    raise ValueError("请输入完整方案名称以确认永久删除")
                # Persist the non-restorable state first so a failed cleanup can be retried.
                states[key] = {**state, "status": "deleting"}
                self.base.set("scheme_states", states)
                self.base.db.commit()
                if key == "default":
                    self.base.backups.wait()
                    with self.base.db:
                        for table in (
                            "annotations",
                            "selections",
                            "reviews",
                            "events",
                            "history",
                            "schemas",
                            "jobs",
                        ):
                            self.base.db.execute("DELETE FROM " + table)
                        self.base.db.execute(
                            "DELETE FROM settings WHERE key NOT IN ('scheme_info', 'annotation_schemes', 'scheme_states')"
                        )
                    root = self.base.root
                else:
                    root = self.base.root / "schemes" / key
                    instance = self.instances.pop(key, None)
                    if instance:
                        instance[0].close()
                    for filename in (
                        "project.sqlite3",
                        "project.sqlite3-wal",
                        "project.sqlite3-shm",
                    ):
                        (root / filename).unlink(missing_ok=True)
                # Cached indices may still be used by shared Dataset handles.
                # Delete only generated database backups, preserving user exports.
                backups = root / "backups"
                if backups.is_dir() and not backups.is_symlink():
                    for backup in backup_paths(backups):
                        if backup.is_file() or backup.is_symlink():
                            backup.unlink()
                states[key] = {**state, "status": "deleted"}
            else:
                raise ValueError("未知方案操作")
            self.base.set("scheme_states", states)
            self.base.db.commit()
            return self.catalog()

    def get(self, key):
        with self.base.lock:
            info = next((s for s in self.list() if s["id"] == key), None)
            if info is None:
                raise ValueError("标注方案已删除或不存在，请重新选择")
            if key not in self.instances:
                store = Store(self.base.root / "schemes" / key)
                self.instances[key] = (store, Exporter(store))
            return self.instances[key]

    def create(self, name, candidate):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("方案名称须为1–100个字符")
        schema = import_schema(candidate, None)
        with self.base.lock:
            if any(
                s["name"] == name.strip()
                for s in self.list() + self.list("trashed") + self.list("deleting")
            ):
                raise ValueError("方案名称已存在")
            info = {
                "id": str(uuid.uuid4()),
                "name": name.strip(),
                "project_id": self.base.get("scheme_info")["project_id"],
            }
            root = self.base.root / "schemes" / info["id"]
            store = None
            try:
                store = Store(root, startup_backup=False)
                with store.db:
                    store.db.execute("DELETE FROM schemas")
                    store.db.execute(
                        "INSERT INTO schemas VALUES(?,?,?)",
                        (1, digest(schema), json.dumps(schema)),
                    )
                    store.set("schema_version", 1)
                    store.set("scheme_info", info)
                self.base.set(
                    "annotation_schemes",
                    [*self.base.get("annotation_schemes", []), info],
                )
                self.base.db.commit()
            except Exception:
                self.base.db.rollback()
                if store is not None:
                    store.close()
                shutil.rmtree(root, ignore_errors=True)
                raise
            self.instances[info["id"]] = (store, Exporter(store))
            store.backup()
            return info
