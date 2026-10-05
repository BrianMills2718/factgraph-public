from pathlib import Path

from factgraph.live import typedb as live_typedb
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model


ROOT = Path(__file__).resolve().parents[1]


def _model(name: str):
    return normalize_model(parse_model((ROOT / "examples" / name).read_text(encoding="utf-8")))


class _Promise:
    def resolve(self):
        return self


class _Tx:
    def __init__(self, kind):
        self.kind = kind
        self.query_text = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def query(self, query):
        self.query_text = query
        return _Promise()

    def commit(self):
        if self.kind == "WRITE":
            # Warehouse runtime probes expect duplicate relation insertion to be
            # accepted but key/requiredness violations to be rejected.
            if "$r2 isa" in self.query_text:
                return
            raise RuntimeError("simulated TypeDB constraint rejection")


class _Database:
    def __init__(self, manager, name):
        self.manager = manager
        self.name = name

    def delete(self):
        self.manager.names.discard(self.name)


class _Databases:
    def __init__(self):
        self.names = set()

    def contains(self, name):
        return name in self.names

    def create(self, name):
        self.names.add(name)

    def get(self, name):
        return _Database(self, name)


class _Driver:
    def __init__(self):
        self.databases = _Databases()

    def transaction(self, db_name, kind):
        assert db_name in self.databases.names
        return _Tx(kind)


class _TransactionType:
    SCHEMA = "SCHEMA"
    WRITE = "WRITE"


def test_typedb_connected_runner_preserves_accept_vs_reject_semantics():
    model = _model("warehouse.fg")
    result = live_typedb._run_connected(model, _Driver(), _TransactionType)
    assert result["status"] == "completed"
    assert result["passed"] is True
    runtime = [r for r in result["results"] if r["mode"] == "runtime"]
    assert runtime
    assert any(r["case_id"].startswith("typedb-gap-fact-set-") for r in runtime)
    assert any(r["case_id"].startswith("typedb-live-identifier-") for r in runtime)
    assert all(r["passed"] for r in runtime)


def test_typedb_runtime_cases_contain_executable_typeql_not_abstract_actions():
    from factgraph.conformance import typedb_cases

    runtime = [c for c in typedb_cases(_model("warehouse.fg")) if c.mode == "runtime"]
    assert runtime
    for case in runtime:
        assert case.steps
        assert all(step.get("typeql", "").startswith("insert") for step in case.steps)
        assert all(step.get("expect") in {"accept", "reject"} for step in case.steps)
