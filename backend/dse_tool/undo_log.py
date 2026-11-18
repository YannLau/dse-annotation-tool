"""Capture the rows changed by one SQLite transaction for undo and redo."""

import json
import zlib


TABLES = ("annotations", "selections", "reviews", "events")
JSON_SNAPSHOT = "json"
COMPRESSED_SNAPSHOT = "snapshot-zlib"
COMPRESSED_ROWS = "rows-zlib"


def ensure_encoding_column(db):
    if "encoding" not in {row[1] for row in db.execute("PRAGMA table_info(history)")}:
        db.execute(
            "ALTER TABLE history ADD COLUMN encoding TEXT NOT NULL DEFAULT 'json'"
        )


def start_capture(db):
    # TEMP triggers and their journal belong to this connection. Both the capture
    # and the data changes obey the same rollback and SAVEPOINT boundaries.
    if not db.execute(
        "SELECT 1 FROM sqlite_temp_schema WHERE name='history_changes'"
    ).fetchone():
        db.execute("CREATE TEMP TABLE history_capture (active INTEGER)")
        db.execute("""CREATE TEMP TABLE history_changes (
            table_name TEXT NOT NULL, row_id INTEGER NOT NULL,
            before_state TEXT, after_state TEXT,
            PRIMARY KEY (table_name, row_id)
        ) WITHOUT ROWID""")
        for table in TABLES:
            columns = [row[1] for row in db.execute(f"PRAGMA main.table_info({table})")]

            def row_json(alias):
                return (
                    "json_object("
                    + ", ".join(
                        f"'{column}', {alias}.\"{column}\"" for column in columns
                    )
                    + ")"
                )

            remove = f"""
                INSERT INTO history_changes VALUES ('{table}', OLD.id, {row_json("OLD")}, NULL)
                ON CONFLICT (table_name, row_id) DO UPDATE SET after_state=NULL;
            """
            insert = f"""
                INSERT INTO history_changes VALUES ('{table}', NEW.id, NULL, {row_json("NEW")})
                ON CONFLICT (table_name, row_id) DO UPDATE SET after_state=excluded.after_state;
            """
            for event, body in (
                ("INSERT", insert),
                ("DELETE", remove),
                ("UPDATE", remove + insert),
            ):
                db.execute(f"""CREATE TEMP TRIGGER capture_{table}_{event.lower()}
                    AFTER {event} ON main.{table}
                    WHEN EXISTS (SELECT 1 FROM history_capture)
                    BEGIN {body} END""")
        # An implicit DELETE from INSERT OR REPLACE also has to be recorded.
        db.execute("PRAGMA recursive_triggers=ON")
    db.execute("INSERT INTO temp.history_capture VALUES (1)")


def finish_capture(db):
    before, after = {}, {}
    for table, row_id, old, new in db.execute(
        "SELECT * FROM temp.history_changes WHERE before_state IS NOT after_state"
    ):
        before.setdefault(table, []).append(
            (row_id, json.loads(old) if old is not None else None)
        )
        after.setdefault(table, []).append(
            (row_id, json.loads(new) if new is not None else None)
        )
    db.execute("DELETE FROM temp.history_capture")
    db.execute("DELETE FROM temp.history_changes")
    return pack_rows(before), pack_rows(after)


def pack_rows(state):
    return zlib.compress(json.dumps(state, separators=(",", ":")).encode(), level=1)


def unpack_state(encoded, encoding):
    if encoding == JSON_SNAPSHOT:
        return json.loads(encoded)
    if encoding in (COMPRESSED_SNAPSHOT, COMPRESSED_ROWS):
        return json.loads(zlib.decompress(encoded))
    raise ValueError(f"无法读取撤销历史的编码 {encoding!r}")
