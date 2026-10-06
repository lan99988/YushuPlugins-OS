"""Private per-plugin entities and commit proofs in one SQLite transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class DomainError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path, plugin):
        self.path, self.plugin = Path(path), plugin

    @contextmanager
    def read(self):
        if not self.path.exists():
            yield None
            return
        if self.path.is_symlink() or not self.path.is_file():
            raise DomainError("storage.invalid_path")
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute("SELECT plugin FROM meta").fetchone()
            if row is None or row["plugin"] != self.plugin:
                raise DomainError("storage.provider_mismatch")
            yield db
        finally:
            db.close()

    @contextmanager
    def write(self):
        if self.path.is_symlink():
            raise DomainError("storage.invalid_path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (plugin TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS entities (
                    kind TEXT NOT NULL, store_id TEXT NOT NULL, id TEXT NOT NULL,
                    version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0, fields TEXT NOT NULL,
                    PRIMARY KEY (store_id, kind, id));
                CREATE TABLE IF NOT EXISTS commits (
                    store_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    identity TEXT NOT NULL, proof TEXT NOT NULL,
                    PRIMARY KEY (store_id, request_id));
            """)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT plugin FROM meta").fetchone()
            if row and row["plugin"] != self.plugin:
                raise DomainError("storage.provider_mismatch")
            if not row:
                db.execute("INSERT INTO meta VALUES (?)", (self.plugin,))
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def lookup(self, store_id, request_id):
        with self.read() as db:
            if db is None:
                return None
            row = db.execute("SELECT proof FROM commits WHERE store_id=? AND request_id=?",
                             (store_id, request_id)).fetchone()
            return json.loads(row[0]) if row else None

    def commit(self, store_id, identity, mutate):
        with self.write() as db:
            row = db.execute("SELECT identity,proof FROM commits WHERE store_id=? AND request_id=?",
                             (store_id, identity["request_id"])).fetchone()
            if row:
                if row["identity"] != encoded(identity):
                    raise DomainError("operation.request_conflict")
                return json.loads(row["proof"])
            data, events = mutate(db)
            proof = {"identity": identity, "data": data, "events": events,
                     "committed_at": now(), "operation_status": "committed"}
            db.execute("INSERT INTO commits VALUES (?,?,?,?)",
                       (store_id, identity["request_id"], encoded(identity), encoded(proof)))
            return proof

    def _entity(self, row):
        return {"id": row["id"], "kind": row["kind"], "version": row["version"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "archived": bool(row["archived"]), "fields": json.loads(row["fields"])}

    def get(self, kind, store_id, entity_id, db=None):
        if db is None:
            with self.read() as connection:
                return None if connection is None else self.get(kind, store_id, entity_id, connection)
        row = db.execute("SELECT * FROM entities WHERE kind=? AND store_id=? AND id=?",
                         (kind, store_id, entity_id)).fetchone()
        return self._entity(row) if row else None

    def list(self, kind, store_id, db=None, include_archived=False, filters=None):
        if db is None:
            with self.read() as connection:
                return [] if connection is None else self.list(kind, store_id, connection,
                                                              include_archived, filters)
        rows = db.execute("SELECT * FROM entities WHERE kind=? AND store_id=? ORDER BY created_at,id",
                          (kind, store_id)).fetchall()
        values = [self._entity(row) for row in rows if include_archived or not row["archived"]]
        if filters:
            values = [e for e in values if all(e["fields"].get(k) == v for k, v in filters.items())]
        return values

    def create(self, db, kind, store_id, fields, entity_id=None):
        timestamp, entity_id = now(), entity_id or kind.replace(".", "_") + "_" + uuid4().hex
        db.execute("INSERT INTO entities VALUES (?,?,?,?,?,?,?,?)",
                   (kind, store_id, entity_id, 1, timestamp, timestamp, 0, encoded(fields)))
        return self.get(kind, store_id, entity_id, db)

    def update(self, db, kind, store_id, entity_id, expected_version, changes, *, archived=None):
        entity = self.get(kind, store_id, entity_id, db)
        if entity is None:
            raise DomainError("entity.not_found")
        if type(expected_version) is not int or expected_version != entity["version"]:
            raise DomainError("entity.version_conflict")
        fields = {**entity["fields"], **changes}
        flag = entity["archived"] if archived is None else archived
        if fields == entity["fields"] and flag == entity["archived"]:
            return entity, False
        db.execute("UPDATE entities SET fields=?,archived=?,version=version+1,updated_at=? "
                   "WHERE kind=? AND store_id=? AND id=?",
                   (encoded(fields), int(flag), now(), kind, store_id, entity_id))
        return self.get(kind, store_id, entity_id, db), True
