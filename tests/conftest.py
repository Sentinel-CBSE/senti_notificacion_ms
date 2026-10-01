import pytest


@pytest.fixture(autouse=True)
def _db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Variables DB_* obligatorias para poder construir Settings en los tests (no se conecta a nada)."""
    monkeypatch.setenv("DB_HOST", "db.test")
    monkeypatch.setenv("DB_USERNAME", "tester")
    monkeypatch.setenv("DB_PASSWORD", "pw")
    monkeypatch.setenv("DB_DATABASE", "testdb")
    monkeypatch.delenv("ENV", raising=False)  # que un ENV de tu shell no altere los tests
