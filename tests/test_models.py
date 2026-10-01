import json

import pytest

from app.models import (
    ContractError,
    parse_event_grid_payload,
    parse_fid_registration,
    parse_incident_message,
)


def test_parse_event_grid_list_with_nested_data() -> None:
    events = parse_event_grid_payload(
        [
            {
                "id": "1",
                "eventType": "Sentinel.Notification.FidRegisteredOrUpdated",
                "data": {"user_id": "u1", "fid": "f1"},
                "dataVersion": "1.0",
            }
        ]
    )
    assert events[0].event_type == "Sentinel.Notification.FidRegisteredOrUpdated"
    fid_data = parse_fid_registration(events[0])
    assert (fid_data.user_id, fid_data.fid) == ("u1", "f1")


def test_event_grid_must_be_a_list() -> None:
    with pytest.raises(ContractError):
        parse_event_grid_payload({"eventType": "x", "data": {}})


@pytest.mark.parametrize("data", [{}, {"user_id": "u1"}, {"fid": "f1"}, {"user_id": "", "fid": "f"}])
def test_fid_registration_requires_both_fields(data: dict[str, str]) -> None:
    event = parse_event_grid_payload([{"eventType": "t", "data": data}])[0]
    with pytest.raises(ContractError):
        parse_fid_registration(event)


def test_fid_registration_accepts_camel_case_user_id() -> None:
    # El API Gateway podría armar el evento con `userId` en vez de `user_id`.
    event = parse_event_grid_payload([{"eventType": "t", "data": {"userId": "u1", "fid": "f1"}}])[0]
    fid_data = parse_fid_registration(event)
    assert (fid_data.user_id, fid_data.fid) == ("u1", "f1")


VALID_MESSAGE = {
    "event_id": "ev-1",
    "user_id": "u1",
    "incidente": "armed_robbery",
    "latitude": 4.6097,
    "longitude": -74.0817,
    "incident_timestamp": "2026-09-30T20:05:00Z",
    "distance_meters": 120.4,
}


def test_parse_incident_message_ok() -> None:
    msg = parse_incident_message(json.dumps(VALID_MESSAGE))
    assert (msg.event_id, msg.user_id, msg.incidente) == ("ev-1", "u1", "armed_robbery")
    assert (msg.latitude, msg.longitude, msg.distance_meters) == (4.6097, -74.0817, 120.4)
    assert msg.incident_timestamp.isoformat() == "2026-09-30T20:05:00+00:00"


def test_parse_accepts_bytes_and_integer_distance() -> None:
    raw = json.dumps({**VALID_MESSAGE, "distance_meters": 120}).encode()
    assert parse_incident_message(raw).distance_meters == 120.0


def test_extra_fields_are_ignored() -> None:
    assert parse_incident_message(json.dumps({**VALID_MESSAGE, "campo_futuro": 1})).user_id == "u1"


def test_no_message_type_discriminator_is_required() -> None:
    assert "messageType" not in VALID_MESSAGE
    assert parse_incident_message(json.dumps(VALID_MESSAGE))


def test_parse_accepts_real_payload_camel_case_with_epoch_millis() -> None:
    # Payload tal como lo publica geolocalización: camelCase e `incidentTimestamp` en epoch ms.
    raw_message = {
        "eventId": "b1a2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
        "userId": "firebase-uid-xyz789",
        "incidentType": "theft",
        "latitude": 4.6097,
        "longitude": -74.0817,
        "incidentTimestamp": 1759245780000,
        "distanceMeters": 340.5,
    }
    msg = parse_incident_message(json.dumps(raw_message))
    assert (msg.event_id, msg.user_id, msg.incidente) == (
        "b1a2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
        "firebase-uid-xyz789",
        "theft",
    )
    assert (msg.latitude, msg.longitude, msg.distance_meters) == (4.6097, -74.0817, 340.5)
    assert msg.incident_timestamp.isoformat() == "2025-09-30T15:23:00+00:00"


@pytest.mark.parametrize("missing", list(VALID_MESSAGE))
def test_every_contract_field_is_required(missing: str) -> None:
    raw = json.dumps({k: v for k, v in VALID_MESSAGE.items() if k != missing})
    with pytest.raises(ContractError):
        parse_incident_message(raw)


@pytest.mark.parametrize(
    "override",
    [
        {"latitude": 91},
        {"longitude": -181},
        {"distance_meters": -1},
        {"distance_meters": "lejos"},
        {"incident_timestamp": "ayer"},
        {"user_id": ""},
        {"event_id": ""},
    ],
)
def test_invalid_values_are_rejected(override: dict[str, object]) -> None:
    with pytest.raises(ContractError):
        parse_incident_message(json.dumps({**VALID_MESSAGE, **override}))


@pytest.mark.parametrize("raw", ["no es json", "[]", "{}", '{"recipients": ["a"], "data": {"title": "t", "body": "b"}}'])
def test_old_or_malformed_contract_is_rejected(raw: str) -> None:
    with pytest.raises(ContractError):
        parse_incident_message(raw)
