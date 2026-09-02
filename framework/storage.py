"""Pluggable persistence for scenario, locator, and run documents.

JSON is used in DEV and MOCK. PROD uses SQLite configured with
AIVAR_DATABASE_URL=sqlite:///data/aivar.db without changing callers. The interface leaves room for a
future server database adapter without coupling the framework to one driver.
"""
import json
import os
import sqlite3
import tempfile
import threading
from urllib.parse import urlparse

from framework.constants import DATABASE_URL_ENV, RUNS_PATH, SCENARIOS_PATH, TESTS_PATH
from framework.environment import storage_backend


class StorageError(RuntimeError):
    """Raised when the configured persistence backend is invalid."""


class DocumentStore:
    def read_scenarios(self):
        raise NotImplementedError

    def write_scenarios(self, data):
        raise NotImplementedError

    def read_test_memory(self):
        raise NotImplementedError

    def write_test_memory(self, data):
        raise NotImplementedError

    def write_run(self, run_id, record):
        raise NotImplementedError

    def reset(self):
        raise NotImplementedError


class JsonDocumentStore(DocumentStore):
    def __init__(self):
        self._lock = threading.RLock()

    def _read(self, path, default):
        if not os.path.exists(path):
            return default
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, path, data):
        directory = os.path.dirname(path)
        fd, temporary_path = tempfile.mkstemp(prefix="aivar-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
            os.replace(temporary_path, path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def read_scenarios(self):
        with self._lock:
            return self._read(SCENARIOS_PATH, {"schema_version": 1, "scenarios": {}})

    def write_scenarios(self, data):
        with self._lock:
            self._write(SCENARIOS_PATH, data)

    def read_test_memory(self):
        with self._lock:
            return self._read(TESTS_PATH, None)

    def write_test_memory(self, data):
        with self._lock:
            self._write(TESTS_PATH, data)

    def write_run(self, run_id, record):
        with self._lock:
            runs = self._read(RUNS_PATH, {})
            runs[run_id] = record
            self._write(RUNS_PATH, runs)

    def reset(self):
        with self._lock:
            for path in (SCENARIOS_PATH, TESTS_PATH, RUNS_PATH):
                if os.path.exists(path):
                    os.unlink(path)


class SqliteDocumentStore(DocumentStore):
    def __init__(self, database_url):
        self.database_path = self._path_from_url(database_url)
        directory = os.path.dirname(self.database_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.execute("CREATE TABLE IF NOT EXISTS aivar_documents (name TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self._connection.commit()

    @staticmethod
    def _path_from_url(database_url):
        if not database_url:
            raise StorageError("AIVAR_DATABASE_URL is required for sqlite storage")
        if database_url == ":memory:":
            return database_url
        parsed = urlparse(database_url)
        if parsed.scheme not in ("sqlite", ""):
            raise StorageError("only sqlite URLs are supported by the built-in database adapter")
        if parsed.scheme == "sqlite":
            path = parsed.path
            if parsed.netloc and not path:
                path = parsed.netloc
            if path.startswith("/") and len(path) > 2 and path[2] == ":":
                path = path[1:]
            return path or ":memory:"
        return database_url

    def _read(self, name, default):
        row = self._connection.execute("SELECT payload FROM aivar_documents WHERE name = ?", (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def _write(self, name, data):
        payload = json.dumps(data)
        self._connection.execute(
            "INSERT INTO aivar_documents(name, payload) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
            (name, payload),
        )
        self._connection.commit()

    def read_scenarios(self):
        with self._lock:
            return self._read("scenarios", {"schema_version": 1, "scenarios": {}})

    def write_scenarios(self, data):
        with self._lock:
            self._write("scenarios", data)

    def read_test_memory(self):
        with self._lock:
            return self._read("test_memory", None)

    def write_test_memory(self, data):
        with self._lock:
            self._write("test_memory", data)

    def write_run(self, run_id, record):
        with self._lock:
            runs = self._read("runs", {})
            runs[run_id] = record
            self._write("runs", runs)

    def reset(self):
        with self._lock:
            self._connection.execute("DELETE FROM aivar_documents")
            self._connection.commit()


def create_store():
    backend = storage_backend()
    if backend == "json":
        return JsonDocumentStore()
    if backend == "sqlite":
        return SqliteDocumentStore(os.environ.get(DATABASE_URL_ENV))
    raise StorageError(f"unsupported storage backend: {backend}")
