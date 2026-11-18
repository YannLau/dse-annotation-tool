"""SQLite backups and compact copies for annotation workspaces."""

import logging
import os
import re
import sqlite3
import threading
import zlib
from collections import deque
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory

from .undo_log import COMPRESSED_SNAPSHOT, JSON_SNAPSHOT, ensure_encoding_column


def workspace_path(root):
    root = Path(root).expanduser().resolve()
    if any((path / "meta/info.json").exists() for path in (root, *root.parents)):
        raise ValueError("工作目录不得位于数据集内部，尚未写入任何工作文件")
    return root


@contextmanager
def workspace_lock(root, *, maintenance=False):
    """Coordinate the server and offline compaction using SQLite file locks."""
    root = workspace_path(root)
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / ".workspace.lock", timeout=0)) as db:
        try:
            db.execute("BEGIN EXCLUSIVE" if maintenance else "BEGIN")
            db.execute("PRAGMA schema_version").fetchone()
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode & 0xFF not in (
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            ):
                raise
            raise ValueError("工作区正在使用，请先停止服务或等待压缩完成") from exc
        yield root


def backup_paths(directory):
    pattern = re.compile(r"[0-9]{8}-[0-9]{6}(?:-[0-9]{6})?\.sqlite3")
    return sorted(
        (path for path in directory.glob("*.sqlite3") if pattern.fullmatch(path.name)),
        key=lambda path: path.stem,
    )


class Backups:
    def __init__(self, root):
        self.root = root
        self._lock = threading.Lock()
        self._pending = deque()
        self._worker = None

    def schedule(self):
        # Pin the just-committed state with a dedicated WAL reader. Copying it
        # can continue after the response while later revisions keep writing.
        path = self.root / "project.sqlite3"
        source = None
        try:
            source = sqlite3.connect(
                path.as_uri() + "?mode=ro", uri=True, check_same_thread=False
            )
            source.execute("BEGIN")
            source.execute("PRAGMA schema_version").fetchone()
            name = datetime.now().strftime("%Y%m%d-%H%M%S-%f.sqlite3")
            with self._lock:
                if self._worker is None:
                    worker = threading.Thread(target=self._drain, daemon=True)
                    worker.start()
                    self._worker = worker
                self._pending.append((source, name))
        except (sqlite3.Error, OSError, RuntimeError):
            if source is not None:
                source.close()
            logging.getLogger(__name__).exception(
                "Could not schedule backup of %s", path
            )

    def _drain(self):
        while True:
            with self._lock:
                if not self._pending:
                    self._worker = None
                    return
                source, name = self._pending.popleft()
            try:
                with closing(source):
                    self._write(source, name)
            except Exception:
                logging.getLogger(__name__).exception("Backup failed in %s", self.root)

    def _write(self, source, name):
        directory = self.root / "backups"
        directory.mkdir(exist_ok=True)
        with TemporaryDirectory(prefix=".backup-", dir=directory) as staging:
            path = Path(staging) / name
            with closing(sqlite3.connect(path)) as target:
                if source.execute(
                    "SELECT 1 FROM history WHERE encoding=? LIMIT 1", (JSON_SNAPSHOT,)
                ).fetchone():
                    copy_compact(source, target)
                else:
                    source.backup(target, pages=256)
                # A backup can be restored by copying only this file.
                target.execute("PRAGMA journal_mode=DELETE").fetchone()
            os.replace(path, directory / name)
        for old in backup_paths(directory)[:-10]:
            old.unlink(missing_ok=True)

    def wait(self):
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join()


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def copy_compact(source, target):
    """Rebuild from a stable SQLite snapshot, processing one history row at a time."""
    tables = source.execute(
        "SELECT name, sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if "history" not in {name for name, _ in tables}:
        raise ValueError("SQLite 文件缺少标注工作库的 history 表")

    def history_rows():
        columns = {row[1] for row in source.execute("PRAGMA table_info(history)")}
        encoding_column = "encoding" if "encoding" in columns else "'json'"
        for row_id, before, after, desc, created, encoding in source.execute(
            "SELECT id, CAST(before_state AS BLOB), CAST(after_state AS BLOB), "
            f"description, created, {encoding_column} FROM history ORDER BY id"
        ):
            if encoding == JSON_SNAPSHOT:
                before = zlib.compress(before)
                after = zlib.compress(after)
                encoding = COMPRESSED_SNAPSHOT
            yield row_id, before, after, desc, created, encoding

    with target:
        target.execute("BEGIN")
        for name, sql in tables:
            target.execute(sql)
            if name == "history":
                ensure_encoding_column(target)
                target.executemany(
                    "INSERT INTO history(id,before_state,after_state,description,created,encoding) "
                    "VALUES(?,?,?,?,?,?)",
                    history_rows(),
                )
            else:
                table = _quote(name)
                rows = source.execute(f"SELECT * FROM {table}")
                placeholders = ",".join("?" for _ in rows.description)
                target.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        target.execute("DELETE FROM sqlite_sequence")
        target.executemany(
            "INSERT INTO sqlite_sequence VALUES (?,?)",
            source.execute("SELECT * FROM sqlite_sequence"),
        )
        for (sql,) in source.execute(
            "SELECT sql FROM sqlite_schema WHERE type IN ('index','view','trigger') AND sql IS NOT NULL "
            "ORDER BY CASE type WHEN 'index' THEN 1 WHEN 'view' THEN 2 ELSE 3 END"
        ):
            target.execute(sql)
        for pragma in ("user_version", "application_id"):
            value = source.execute("PRAGMA " + pragma).fetchone()[0]
            target.execute(f"PRAGMA {pragma}={value}")


def compact_database(path):
    """Validate and replace an offline database using a new file on the same filesystem."""
    with TemporaryDirectory(prefix=".compact-", dir=path.parent) as staging:
        staged = Path(staging) / path.name
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)
        ) as source:
            # Checkpoint WAL and make other SQLite connections release the file
            # before copying. The workspace lock protects against service startup.
            if source.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise ValueError(f"数据库正在使用，无法压缩 {path}")
            source.execute("BEGIN EXCLUSIVE")
            original_size = path.stat().st_size
            with closing(sqlite3.connect(staged)) as target:
                copy_compact(source, target)
                if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError(f"压缩副本未通过 SQLite 完整性检查 {path}")
                counts = "SELECT COUNT(*) FROM "
                for (name,) in source.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                ):
                    query = counts + _quote(name)
                    if (
                        source.execute(query).fetchone()
                        != target.execute(query).fetchone()
                    ):
                        raise ValueError(f"压缩副本的 {name} 记录数量不一致 {path}")
        compact_size = staged.stat().st_size
        if compact_size < original_size:
            staged.chmod(S_IMODE(path.stat().st_mode))
            os.replace(staged, path)
            return original_size, compact_size
        return original_size, original_size
