import json

from app.servicebus_listener import settle_message
from tests.fakes import FakeMessage, FakeReceiver, FakeRepository, FakeSender

VALID = json.dumps(
    {
        "event_id": "ev-1",
        "user_id": "u1",
        "incidente": "theft",
        "latitude": 4.6,
        "longitude": -74.08,
        "incident_timestamp": "2026-09-30T20:05:00Z",
        "distance_meters": 80,
    }
)


async def test_success_completes_message() -> None:
    receiver, sender = FakeReceiver(), FakeSender()
    await settle_message(receiver, FakeMessage(VALID), FakeRepository({"u1": "f1"}), sender)
    assert receiver.settled == ["complete"]
    assert len(sender.calls) == 1


async def test_poison_message_goes_to_dead_letter() -> None:
    receiver = FakeReceiver()
    await settle_message(receiver, FakeMessage("basura"), FakeRepository(), FakeSender())
    assert receiver.settled == ["dead_letter"]


async def test_old_contract_message_goes_to_dead_letter() -> None:
    old = json.dumps({"messageType": "PUSH_ROBBERY_ALERT", "correlationId": "c", "recipients": ["u1"], "data": {"title": "t", "body": "b"}})
    receiver = FakeReceiver()
    await settle_message(receiver, FakeMessage(old), FakeRepository({"u1": "f1"}), FakeSender())
    assert receiver.settled == ["dead_letter"]


async def test_transient_failure_abandons_for_retry() -> None:
    receiver = FakeReceiver()
    sender = FakeSender(error=RuntimeError("FCM caído"))
    await settle_message(receiver, FakeMessage(VALID), FakeRepository({"u1": "f1"}), sender)
    assert receiver.settled == ["abandon"]


async def test_user_without_fid_still_completes() -> None:
    """Sin FID no hay nada que reintentar: el mensaje se consume."""
    receiver = FakeReceiver()
    await settle_message(receiver, FakeMessage(VALID), FakeRepository(), FakeSender())
    assert receiver.settled == ["complete"]
