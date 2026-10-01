import logging
from datetime import UTC, datetime

import pytest

from app.models import IncidentAlertMessage
from app.notification_text import FALLBACK_TITLE, INCIDENT_LABELS, build_location_phrase, build_notification


def incident(**overrides: object) -> IncidentAlertMessage:
    data: dict[str, object] = {
        "event_id": "e1",
        "user_id": "u1",
        "incidente": "armed_robbery",
        "latitude": 4.6,
        "longitude": -74.08,
        "incident_timestamp": "2026-09-30T20:05:00Z",
        "distance_meters": 120.4,
    }
    return IncidentAlertMessage.model_validate({**data, **overrides})


@pytest.mark.parametrize(
    ("incidente", "label"),
    [("armed_robbery", "Robo armado"), ("theft", "Hurto"), ("burglary", "Robo a vivienda")],
)
def test_known_incidents_use_their_readable_label(incidente: str, label: str) -> None:
    title, _ = build_notification(incident(incidente=incidente))
    assert title == label


def test_labels_table_has_exactly_the_three_known_types() -> None:
    assert set(INCIDENT_LABELS) == {"armed_robbery", "theft", "burglary"}


def test_unknown_incident_does_not_crash_and_uses_fallback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        title, body = build_notification(incident(incidente="alien_invasion"))
    assert title == FALLBACK_TITLE == "Incidente reportado"
    assert body  # el cuerpo se arma igual
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert warning.incidente == "alien_invasion"  # type: ignore[attr-defined]


def test_body_uses_colombia_time_not_raw_utc() -> None:
    # 20:05 UTC = 15:05 en Colombia (UTC-5)
    _, body = build_notification(incident())
    assert body == "Reportado a aproximadamente 120 metros de tu ubicación, a las 3:05 p. m."


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        ("2026-09-30T05:00:00Z", "12:00 a. m."),  # medianoche local
        ("2026-09-30T17:00:00Z", "12:00 p. m."),  # mediodía local
        ("2026-09-30T14:30:00+00:00", "9:30 a. m."),
        ("2026-09-30T15:05:00-05:00", "3:05 p. m."),  # ya viene en hora local
    ],
)
def test_time_formatting(utc: str, expected: str) -> None:
    _, body = build_notification(incident(incident_timestamp=utc))
    assert body.endswith(f"a las {expected}")


def test_naive_timestamp_is_assumed_utc() -> None:
    naive = incident(incident_timestamp=datetime(2026, 9, 30, 20, 5).isoformat())
    assert naive.incident_timestamp.tzinfo is None
    assert build_notification(naive)[1].endswith("a las 3:05 p. m.")
    assert datetime(2026, 9, 30, 20, 5, tzinfo=UTC)  # referencia de la equivalencia


@pytest.mark.parametrize(
    ("distance", "phrase"),
    [
        (120.4, "a aproximadamente 120 metros de tu ubicación"),
        (120.5, "a aproximadamente 121 metros de tu ubicación"),  # mitad hacia arriba
        (2.5, "a aproximadamente 3 metros de tu ubicación"),  # round() de Python daría 2
        (0.2, "a aproximadamente 0 metros de tu ubicación"),
        (1.0, "a aproximadamente 1 metro de tu ubicación"),  # singular
        (999.9, "a aproximadamente 1000 metros de tu ubicación"),
    ],
)
def test_build_location_phrase_rounds_to_whole_meters(distance: float, phrase: str) -> None:
    assert build_location_phrase(distance) == phrase
