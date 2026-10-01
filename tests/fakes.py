"""Dobles de prueba en memoria que cumplen los puertos (sin BD ni Firebase reales)."""

from typing import Any


class FakeRepository:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.rows: dict[str, str] = dict(initial or {})
        self.upserts: list[tuple[str, str]] = []

    async def verify_schema(self) -> None: ...

    async def upsert_fid(self, user_id: str, fid: str) -> None:
        self.upserts.append((user_id, fid))
        self.rows[user_id] = fid

    async def get_fid(self, user_id: str) -> str | None:
        return self.rows.get(user_id)

    async def close(self) -> None: ...


class FakeSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.error = error

    async def send(self, fid: str, title: str, body: str) -> None:
        if self.error:
            raise self.error
        self.calls.append((fid, title, body))


class FakeReceiver:
    """Registra cómo se liquidó cada mensaje en Service Bus."""

    def __init__(self) -> None:
        self.settled: list[str] = []

    async def complete_message(self, message: Any) -> None:
        self.settled.append("complete")

    async def abandon_message(self, message: Any) -> None:
        self.settled.append("abandon")

    async def dead_letter_message(self, message: Any, reason: str = "", error_description: str = "") -> None:
        self.settled.append("dead_letter")


class FakeMessage:
    def __init__(self, body: str) -> None:
        self._body = body

    def __str__(self) -> str:
        return self._body
