import json

import pytest

from app.handlers import handle_event_grid_payload, process_incident_message
from app.models import ContractError
from tests.fakes import FakeRepository, FakeSender

FID_EVENT = {
    "id": "e1",
    "eventType": "Sentinel.Notification.FidRegisteredOrUpdated",
    "data": {"user_id": "u1", "fid": "fid-A"},
    "dataVersion": "1.0",
}


# --- Event Grid ---


async def test_handshake_returns_validation_response_and_does_not_touch_db() -> None:
    repo = FakeRepository()
    payload = [
        {
            "eventType": "Microsoft.EventGrid.SubscriptionValidationEvent",
            "data": {"validationCode": "abc-123"},
        }
    ]
    assert await handle_event_grid_payload(payload, repo) == {"validationResponse": "abc-123"}
    assert repo.upserts == []


async def test_handshake_without_code_is_contract_error() -> None:
    payload = [{"eventType": "Microsoft.EventGrid.SubscriptionValidationEvent", "data": {}}]
    with pytest.raises(ContractError):
        await handle_event_grid_payload(payload, FakeRepository())


async def test_first_registration_saves_fid() -> None:
    repo = FakeRepository()
    result = await handle_event_grid_payload([FID_EVENT], repo)
    assert result == {"processed": 1}
    assert repo.rows == {"u1": "fid-A"}


async def test_update_overwrites_fid_same_code_path() -> None:
    """Primer registro y rotación de FID son el mismo evento: el segundo pisa al primero."""
    repo = FakeRepository()
    await handle_event_grid_payload([FID_EVENT], repo)
    rotated = {**FID_EVENT, "id": "e2", "data": {"user_id": "u1", "fid": "fid-B"}}
    await handle_event_grid_payload([rotated], repo)
    assert repo.rows == {"u1": "fid-B"}


async def test_batch_with_several_events() -> None:
    repo = FakeRepository()
    other = {**FID_EVENT, "data": {"user_id": "u2", "fid": "fid-C"}}
    assert await handle_event_grid_payload([FID_EVENT, other], repo) == {"processed": 2}
    assert repo.rows == {"u1": "fid-A", "u2": "fid-C"}


async def test_unknown_event_type_is_ignored() -> None:
    repo = FakeRepository()
    result = await handle_event_grid_payload([{"eventType": "Other.Thing", "data": {}}], repo)
    assert result == {"processed": 0}
    assert repo.upserts == []


async def test_non_list_payload_is_contract_error() -> None:
    with pytest.raises(ContractError):
        await handle_event_grid_payload(FID_EVENT, FakeRepository())


# --- Dispatch ---


def message(user_id: str = "u1", incidente: str = "armed_robbery", distance: float = 120.4) -> str:
    return json.dumps(
        {
            "event_id": "ev-1",
            "user_id": user_id,
            "incidente": incidente,
            "latitude": 4.6,
            "longitude": -74.08,
            "incident_timestamp": "2026-09-30T20:05:00Z",
            "distance_meters": distance,
        }
    )


async def test_dispatch_sends_one_push_to_the_users_fid_with_built_text() -> None:
    repo, sender = FakeRepository({"u1": "fid-1"}), FakeSender()
    result = await process_incident_message(message(), repo, sender)
    assert sender.calls == [
        ("fid-1", "Robo armado", "Reportado a aproximadamente 120 metros de tu ubicación, a las 3:05 p. m.")
    ]
    assert result.sent is True


async def test_dispatch_targets_only_the_named_user() -> None:
    repo, sender = FakeRepository({"u1": "fid-1", "u2": "fid-2"}), FakeSender()
    await process_incident_message(message(user_id="u2"), repo, sender)
    assert [call[0] for call in sender.calls] == ["fid-2"]


async def test_dispatch_skips_user_without_fid() -> None:
    sender = FakeSender()
    result = await process_incident_message(message(user_id="ghost"), FakeRepository(), sender)
    assert sender.calls == []
    assert result.sent is False


async def test_unknown_incident_still_sends_with_fallback_title() -> None:
    repo, sender = FakeRepository({"u1": "fid-1"}), FakeSender()
    await process_incident_message(message(incidente="algo_nuevo"), repo, sender)
    assert sender.calls[0][1] == "Incidente reportado"


@pytest.mark.parametrize("raw", ["{}", "basura", '{"recipients": ["u1"], "data": {"title": "t", "body": "b"}}'])
async def test_invalid_message_raises_contract_error(raw: str) -> None:
    with pytest.raises(ContractError):
        await process_incident_message(raw, FakeRepository(), FakeSender())


# --- Evento tal como lo arma la política del API Gateway ---

GATEWAY_EVENT = {
    "id": "1b9c1e0e-0000-4000-8000-000000000001",
    "eventType": "Sentinel.InstallationIdActualizado",
    "subject": "usuarios/gw-user-1",
    "eventTime": "2026-10-04T15:00:00.0000000Z",
    "dataVersion": "1.0",
    "data": {"installationId": "gw-fid-1", "userId": "gw-user-1"},
}


async def test_gateway_event_saves_fid_using_installation_id_and_user_id() -> None:
    repo = FakeRepository()
    assert await handle_event_grid_payload([GATEWAY_EVENT], repo) == {"processed": 1}
    assert repo.rows == {"gw-user-1": "gw-fid-1"}


async def test_gateway_event_rotation_overwrites_previous_fid() -> None:
    repo = FakeRepository({"gw-user-1": "viejo"})
    await handle_event_grid_payload([GATEWAY_EVENT], repo)
    assert repo.rows == {"gw-user-1": "gw-fid-1"}


async def test_previous_event_type_name_is_still_accepted() -> None:
    repo = FakeRepository()
    legacy = {**GATEWAY_EVENT, "eventType": "Sentinel.Notification.FidRegisteredOrUpdated"}
    assert await handle_event_grid_payload([legacy], repo) == {"processed": 1}


async def test_other_event_types_of_the_shared_topic_are_ignored() -> None:
    repo = FakeRepository()
    foreign = {**GATEWAY_EVENT, "eventType": "Sentinel.IncidenteReportado"}
    assert await handle_event_grid_payload([foreign], repo) == {"processed": 0}
    assert repo.rows == {}


async def test_gateway_event_without_installation_id_is_a_contract_error() -> None:
    bad = {**GATEWAY_EVENT, "data": {"userId": "gw-user-1"}}
    with pytest.raises(ContractError):
        await handle_event_grid_payload([bad], FakeRepository())
