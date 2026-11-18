import json
import shutil
import sqlite3
import threading
import zlib
from contextlib import closing

import pytest

from dse_tool.maintenance import workspace_databases
from dse_tool.storage import backup_paths, compact_database, workspace_lock
from dse_tool.store import Store
from dse_tool.undo_log import COMPRESSED_ROWS, COMPRESSED_SNAPSHOT


def test_history_records_row_reuse_and_savepoint_rollback(tmp_path, monkeypatch):
    store = Store(tmp_path, startup_backup=False)
    with store.transaction("old event"):
        store.db.execute(
            "INSERT INTO events VALUES (1,'source',0,'left','open','confirmed','{}')"
        )
    before = store.snapshot()

    def no_full_snapshot():
        pytest.fail("An ordinary write or undo read all annotations")

    with monkeypatch.context() as patch:
        patch.setattr(store, "snapshot", no_full_snapshot)
        with store.transaction("revise event and selection"):
            store.db.execute("DELETE FROM events WHERE id=1")
            store.db.execute(
                "INSERT INTO events VALUES (1,'source',1,'right','closed','confirmed','{}')"
            )
            store.db.execute("SAVEPOINT trial")
            store.db.execute("UPDATE events SET id=2,frame=99")
            store.db.execute("ROLLBACK TO trial")
            store.db.execute("RELEASE trial")
            store.select("dataset", 0, 10, 15)

    row = store.db.execute("SELECT * FROM history ORDER BY id DESC LIMIT 1").fetchone()
    assert row["encoding"] == COMPRESSED_ROWS
    before_rows = json.loads(zlib.decompress(row["before_state"]))
    assert before_rows["events"] == [[1, before["events"][0]]]
    assert before_rows["selections"] == [[1, None]]
    after = store.snapshot()
    assert store.undo() and store.snapshot() == before
    assert store.undo(redo=True) and store.snapshot() == after
    assert store.undo()
    store.select("dataset", 0, 15, 20)
    assert not store.undo(redo=True)
    assert store.snapshot()["events"] == before["events"]
    store.close()


def test_legacy_history_compaction_and_mixed_undo(tmp_path):
    store = Store(tmp_path, startup_backup=False)
    old_db = store.root / "project.sqlite3"
    schema = store.schema()
    store.db.execute("ALTER TABLE history DROP COLUMN encoding")
    before = store.snapshot()
    reason = "已审查有效动作" * 12_000
    store.db.execute(
        "INSERT INTO reviews VALUES(1,'source',0,10,'approved',?)", (reason,)
    )
    after = store.snapshot()
    original_before, original_after = (
        json.dumps(before),
        json.dumps(after, ensure_ascii=False),
    )
    store.db.execute(
        "INSERT INTO history VALUES(7,?,?,'legacy review',142.0)",
        (original_before, original_after),
    )
    store.db.execute("UPDATE sqlite_sequence SET seq=20 WHERE name='history'")
    store.db.execute("PRAGMA user_version=42")
    store.set("history_cursor", 7)
    store.set("revision", 7)
    store.db.commit()
    store.close()

    legacy = Store(tmp_path, startup_backup=False)
    assert legacy.undo() and legacy.snapshot() == before
    assert legacy.undo(redo=True) and legacy.snapshot() == after
    legacy.close()

    before_size, after_size = compact_database(old_db)
    assert before_size - after_size > len(original_after.encode()) / 2
    upgraded = Store(tmp_path, startup_backup=False)
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 42
    entry = upgraded.db.execute("SELECT * FROM history").fetchone()
    assert (entry["id"], entry["description"], entry["created"]) == (
        7,
        "legacy review",
        142.0,
    )
    assert entry["encoding"] == COMPRESSED_SNAPSHOT
    assert zlib.decompress(entry["before_state"]) == original_before.encode()
    assert zlib.decompress(entry["after_state"]) == original_after.encode()
    assert upgraded.schema() == schema and upgraded.snapshot() == after
    upgraded.select("dataset", 0, 10, 20)
    assert upgraded.get("history_cursor") == 21
    selected = upgraded.snapshot()
    assert upgraded.undo() and upgraded.snapshot() == after
    assert upgraded.undo() and upgraded.snapshot() == before
    assert upgraded.undo(redo=True) and upgraded.snapshot() == after
    assert upgraded.undo(redo=True) and upgraded.snapshot() == selected
    upgraded.close()


def test_backup_is_async_and_restores_its_committed_revision(tmp_path, monkeypatch):
    store = Store(tmp_path / "project", startup_backup=False)
    started, release = threading.Event(), threading.Event()
    write = store.backups._write

    def hold_backup(source, name):
        started.set()
        assert release.wait(timeout=5)
        write(source, name)

    monkeypatch.setattr(store.backups, "_write", hold_backup)
    snapshots = store.root / "backups"
    store.set("revision", 19)
    store.db.commit()
    try:
        store.select("dataset", 0, 0, 3)
        assert started.wait(timeout=5)
        assert backup_paths(snapshots) == []
        store.select("dataset", 0, 5, 8)
        assert store.get("revision") == 21
    finally:
        release.set()
        store.close()

    (backup,) = backup_paths(snapshots)
    copied = tmp_path / "restored"
    copied.mkdir()
    shutil.copy2(backup, copied / "project.sqlite3")
    reopened = Store(copied, startup_backup=False)
    assert reopened.get("revision") == 20
    assert [(r["start"], r["end"]) for r in reopened.snapshot()["selections"]] == [
        (0, 3)
    ]
    assert reopened.undo() and not reopened.snapshot()["selections"]
    assert reopened.undo(redo=True)
    reopened.close()


def test_offline_compaction_scope_and_active_service_lock(tmp_path):
    root = tmp_path
    project = root / "project.sqlite3"
    store = Store(root, startup_backup=False)
    scheme_id = "aa54f450-6a12-4f4b-a4d9-fb054adc7e75"
    scheme = Store(root / "schemes" / scheme_id, startup_backup=False)
    export = scheme.root.parent / "export-1"
    export.mkdir()
    (export / "project.sqlite3").touch()
    scheme.backup()
    scheme.close()
    assert list(workspace_databases(root)) == [
        project,
        scheme.root / "project.sqlite3",
        *backup_paths(scheme.root / "backups"),
    ]
    with workspace_lock(root), pytest.raises(ValueError, match="正在使用"):
        with workspace_lock(root, maintenance=True):
            pytest.fail("Compaction obtained a lock while the server was active")
    # An older server does not acquire the workspace lock, but owns a WAL handle.
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        compact_database(project)
    store.close()
    with workspace_lock(root, maintenance=True):
        compact_database(project)
    with closing(sqlite3.connect(project)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
