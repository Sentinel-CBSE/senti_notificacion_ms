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
