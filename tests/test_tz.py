"""O fallback de fusos (Windows sem tzdata) deve bater com a base oficial zoneinfo."""
from datetime import datetime, timedelta, timezone

import pytest

from app import tz


@pytest.mark.parametrize("key", ["America/Sao_Paulo", "Europe/Lisbon", "America/New_York"])
def test_fallback_igual_ao_zoneinfo(key):
    try:
        from zoneinfo import ZoneInfo
        ref = ZoneInfo(key)
    except Exception:
        pytest.skip("base zoneinfo indisponível neste sistema")
    fb = tz._FALLBACK[key]
    t = datetime(2025, 1, 1, tzinfo=timezone.utc)
    while t < datetime(2028, 1, 1, tzinfo=timezone.utc):  # a cada 3 h por 3 anos, cobre as viradas de horário
        assert t.astimezone(fb).utcoffset() == t.astimezone(ref).utcoffset(), (key, t)
        assert t.astimezone(fb).replace(tzinfo=None) == t.astimezone(ref).replace(tzinfo=None)
        t += timedelta(hours=3)
    for d in range(0, 1100, 7):  # meia-noite local → UTC (usado nos fechamentos)
        day = (datetime(2025, 1, 1) + timedelta(days=d)).date()
        a = datetime.combine(day, datetime.min.time(), fb).astimezone(timezone.utc)
        b = datetime.combine(day, datetime.min.time(), ref).astimezone(timezone.utc)
        assert a == b, (key, day)


def test_forca_fallback(monkeypatch):
    monkeypatch.setenv("FORCE_TZ_FALLBACK", "1")
    assert tz.get_tz("America/Sao_Paulo") is tz._FALLBACK["America/Sao_Paulo"]
    with pytest.raises(ValueError):
        tz.get_tz("Asia/Tokyo")
