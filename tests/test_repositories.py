"""Adaptador SQL Server: sin base real, se verifica el SQL y los parámetros enviados.

La prueba contra un SQL Server real se hace con docker compose (ver README).
"""

from typing import Any

import pytest

from app.adapters.sqlserver_repo import SqlServerDeviceRepository

Log = list[tuple[str, tuple[Any, ...]]]


class FakeConnection:
    def __init__(self, log: Log, rows: list[tuple[str, ...]], fail: Exception | None = None) -> None:
        self._log, self._rows, self._fail = log, rows, fail
        self.closed = self.committed = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> "FakeConnection":
        if self._fail:
            raise self._fail
        self._log.append((sql, params))
        return self

    def fetchone(self) -> tuple[str, ...] | None:
        return self._rows[0] if self._rows else None

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def make_repo(rows: list[tuple[str, ...]] | None = None, fail: Exception | None = None) -> tuple[SqlServerDeviceRepository, Log, list[FakeConnection]]:
    log: Log = []
    conns: list[FakeConnection] = []

    def connect() -> FakeConnection:
        conns.append(FakeConnection(log, rows or [], fail))
        return conns[-1]

    return SqlServerDeviceRepository("unused", connect=connect), log, conns


async def test_upsert_uses_merge_with_holdlock_and_commits() -> None:
    repo, log, conns = make_repo()
    await repo.upsert_fid("u1", "fid-A")

    sql, params = log[0]
    assert "MERGE" in sql and "HOLDLOCK" in sql and "ON CONFLICT" not in sql
    assert params == ("u1", "fid-A")
    assert conns[0].committed and conns[0].closed


async def test_get_fid_returns_fid_and_closes_connection() -> None:
    repo, log, conns = make_repo(rows=[("f1",)])
    assert await repo.get_fid("u1") == "f1"
    assert log[0][1] == ("u1",)
    assert conns[0].closed


async def test_get_fid_returns_none_for_unknown_user() -> None:
    repo, _, _ = make_repo(rows=[])
    assert await repo.get_fid("ghost") is None


async def test_verify_schema_queries_the_table() -> None:
    repo, log, _ = make_repo()
    await repo.verify_schema()
    assert "dbo.device_tokens" in log[0][0]


async def test_verify_schema_propagates_errors_and_still_closes() -> None:
    repo, _, conns = make_repo(fail=RuntimeError("Invalid object name 'dbo.device_tokens'"))
    with pytest.raises(RuntimeError, match="device_tokens"):
        await repo.verify_schema()
    assert conns[0].closed


# --- Arranque resiliente ante una base serverless que se está reanudando ---


class OperationalError(Exception):
    """Mismo nombre que la clase DB-API de pyodbc para timeouts de login."""


class ProgrammingError(Exception):
    """Mismo nombre que la clase DB-API de pyodbc para errores de SQL/permisos."""


def flaky_repo(errors: list[Exception]) -> tuple[SqlServerDeviceRepository, list[int]]:
    """Cada conexión consume un error de la lista; cuando se acaba, conecta bien."""
    calls: list[int] = []
    log: Log = []
    pending = list(errors)

    def connect() -> FakeConnection:
        calls.append(1)
        if pending:
            raise pending.pop(0)
        return FakeConnection(log, [])

    repo = SqlServerDeviceRepository("unused", connect=connect, startup_wait_seconds=2, retry_delay_seconds=0.01)
    return repo, calls


async def test_verify_schema_retries_login_timeouts_until_the_database_is_up() -> None:
    timeout = OperationalError("('HYT00', '[HYT00] Login timeout expired (0) (SQLDriverConnect)')")
    repo, calls = flaky_repo([timeout, timeout])
    await repo.verify_schema()
    assert len(calls) == 3


async def test_verify_schema_retries_database_unavailable_error_40613() -> None:
    """40613 llega como ProgrammingError (no OperationalError) pero también es transitorio."""
    unavailable = ProgrammingError("Database 'x' on server 'y' is not currently available. (40613) (SQLDriverConnect)")
    repo, calls = flaky_repo([unavailable])
    await repo.verify_schema()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "permanent",
    [
        ProgrammingError("Invalid object name 'dbo.device_tokens'. (208) (SQLExecDirectW)"),
        ProgrammingError("Cannot open database \"x\" requested by the login. The login failed. (4060) (SQLDriverConnect)"),
        ProgrammingError("Login failed for user 'u'. (18456) (SQLDriverConnect)"),
    ],
)
async def test_verify_schema_does_not_retry_permanent_errors(permanent: Exception) -> None:
    repo, calls = flaky_repo([permanent])
    with pytest.raises(ProgrammingError):
        await repo.verify_schema()
    assert len(calls) == 1


async def test_verify_schema_gives_up_after_the_wait_window() -> None:
    timeout = OperationalError("Login timeout expired")
    log: Log = []
    calls: list[int] = []

    def always_failing() -> FakeConnection:
        calls.append(1)
        raise timeout

    repo = SqlServerDeviceRepository("unused", connect=always_failing, startup_wait_seconds=0.1, retry_delay_seconds=0.02)
    with pytest.raises(OperationalError):
        await repo.verify_schema()
    assert 2 <= len(calls) <= 10
    assert not log


def test_login_timeout_is_long_enough_for_a_resuming_serverless_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Medido en Azure: reanudar tarda ~26 s. El login de 10 s se rendía antes."""
    import sys
    import types

    seen: dict[str, Any] = {}

    class FakePyodbcConnection:
        timeout = 0

    def fake_connect(connection_string: str, timeout: int) -> FakePyodbcConnection:
        seen["login_timeout"] = timeout
        return FakePyodbcConnection()

    fake_module = types.ModuleType("pyodbc")
    fake_module.connect = fake_connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodbc", fake_module)

    from app.adapters.sqlserver_repo import _default_connect

    conn = _default_connect("cs")()
    assert seen["login_timeout"] >= 30
    assert conn.timeout > 0  # sigue habiendo límite por query
