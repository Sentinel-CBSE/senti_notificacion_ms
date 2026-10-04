from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeRepository, FakeSender

FID_EVENT = [
    {
        "id": "e1",
        "eventType": "Sentinel.Notification.FidRegisteredOrUpdated",
        "data": {"user_id": "u1", "fid": "fid-A"},
        "dataVersion": "1.0",
    }
]


def make_client(repo: FakeRepository, **overrides: object) -> TestClient:
    settings = Settings(_env_file=None, servicebus_enabled=False, **overrides)  # type: ignore[arg-type]
    return TestClient(create_app(settings, repo=repo, sender=FakeSender()))


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def client(repo: FakeRepository) -> Iterator[TestClient]:
    with make_client(repo) as c:
        yield c


def test_health_ok_without_worker(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_handshake_over_http(client: TestClient) -> None:
    body = [{"eventType": "Microsoft.EventGrid.SubscriptionValidationEvent", "data": {"validationCode": "xyz"}}]
    response = client.post("/notifications/fid", json=body)
    assert response.status_code == 200
    assert response.json() == {"validationResponse": "xyz"}


def test_fid_event_over_http_upserts(client: TestClient, repo: FakeRepository) -> None:
    assert client.post("/notifications/fid", json=FID_EVENT).status_code == 200
    assert repo.rows == {"u1": "fid-A"}


def test_invalid_payload_returns_400(client: TestClient) -> None:
    assert client.post("/notifications/fid", json={"not": "a list"}).status_code == 400
    assert client.post("/notifications/fid", content="no json").status_code == 400


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_and_spec_are_open_in_development(repo: FakeRepository, path: str) -> None:
    with make_client(repo, env="development") as c:
        assert c.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_swagger_ui_is_disabled_in_production(repo: FakeRepository, path: str) -> None:
    with make_client(repo, env="production") as c:
        assert c.get(path).status_code == 404


def test_openapi_spec_stays_available_in_production_for_the_api_gateway(repo: FakeRepository) -> None:
    with make_client(repo, env="production") as c:
        response = c.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["openapi"].startswith("3.0.")


def test_endpoints_keep_working_in_production(repo: FakeRepository) -> None:
    with make_client(repo, env="production") as c:
        assert c.get("/health").status_code == 200
        assert c.post("/notifications/fid", json=FID_EVENT).status_code == 200
        assert repo.rows == {"u1": "fid-A"}


def test_gateway_shaped_event_over_http_upserts(client: TestClient, repo: FakeRepository) -> None:
    """Evento exactamente como lo publica el API Gateway, con los campos extra de Event Grid."""
    event = {
        "id": "e-gw",
        "topic": "/subscriptions/x/resourceGroups/rg/providers/Microsoft.EventGrid/topics/senti-eventos-mq",
        "eventType": "Sentinel.InstallationIdActualizado",
        "subject": "usuarios/gw-user",
        "eventTime": "2026-10-04T15:00:00Z",
        "dataVersion": "1.0",
        "metadataVersion": "1",
        "data": {"installationId": "gw-fid", "userId": "gw-user"},
    }
    response = client.post("/notifications/fid", json=[event])
    assert response.status_code == 200
    assert response.json() == {"processed": 1}
    assert repo.rows == {"gw-user": "gw-fid"}
