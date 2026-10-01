import pytest
from pydantic import ValidationError

from app.config import Settings


def make(**kwargs: object) -> Settings:
    kwargs.setdefault("servicebus_enabled", False)
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_db_settings_come_from_env_vars() -> None:
    s = make()
    assert (s.db_host, s.db_port, s.db_username, s.db_database) == ("db.test", 1433, "tester", "testdb")
    assert s.db_password.get_secret_value() == "pw"
    assert s.servicebus_queue_name == "senti-notificaciones-mq"


@pytest.mark.parametrize("missing", ["DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_DATABASE"])
def test_db_variables_are_required(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError, match=missing.lower()):
        make()


def test_no_engine_selection_or_full_connection_string_setting() -> None:
    fields = set(Settings.model_fields)
    assert not {"db_engine", "sqlite_path", "azure_sql_connection_string", "sql_connection_string"} & fields


def test_connection_string_is_built_from_individual_variables() -> None:
    cs = make(db_port=1444, db_encrypt=False, db_trust_server_certificate=True).odbc_connection_string()
    assert cs.startswith("DRIVER={ODBC Driver 18 for SQL Server};SERVER=db.test,1444;")
    assert "DATABASE={testdb}" in cs and "UID={tester}" in cs and "PWD={pw}" in cs
    assert "Encrypt=no" in cs and "TrustServerCertificate=yes" in cs


def test_connection_string_secure_defaults() -> None:
    cs = make().odbc_connection_string()
    assert "Encrypt=yes" in cs and "TrustServerCertificate=no" in cs


def test_password_with_special_characters_is_escaped() -> None:
    cs = make(db_password="pa;ss}w{ord").odbc_connection_string()
    assert "PWD={pa;ss}}w{ord}" in cs  # `}` se duplica y todo va entre llaves


def test_env_defaults_to_production() -> None:
    """Seguro por defecto: si se olvida definir ENV, /docs queda cerrado."""
    assert make().env == "production"


def test_env_reads_development_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    assert make().env == "development"


def test_env_rejects_unknown_values() -> None:
    """Un typo como ENV=prod no debe dejar las docs abiertas por accidente: falla al arrancar."""
    with pytest.raises(ValidationError, match="env"):
        make(env="prod")


def test_password_is_not_exposed_by_repr() -> None:
    assert "pw" not in repr(make().db_password)


def test_worker_requires_servicebus_and_firebase_credentials() -> None:
    with pytest.raises(ValidationError):
        make(servicebus_enabled=True)
    with pytest.raises(ValidationError):
        make(servicebus_enabled=True, servicebus_connection_string="Endpoint=sb://x")
    assert make(
        servicebus_enabled=True,
        servicebus_connection_string="Endpoint=sb://x",
        firebase_credentials_json="{}",
    )
    assert make(
        servicebus_enabled=True,
        servicebus_connection_string="Endpoint=sb://x",
        firebase_credentials_path="./sa.json",
    )


def test_env_vars_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICEBUS_ENABLED", "false")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DB_ENCRYPT", "false")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.log_level == "DEBUG" and s.servicebus_enabled is False and s.db_encrypt is False
