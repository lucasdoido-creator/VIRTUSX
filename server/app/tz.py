"""Fusos horários sem depender do pacote 'tzdata'.

Usa zoneinfo quando a base do sistema existe (Linux/macOS ou tzdata instalado). No Windows sem tzdata,
cai para regras embutidas das regiões operadas. Regras conferidas em 2026:
- America/Sao_Paulo: UTC-3 fixo (horário de verão abolido em 2019).
- Europe/Lisbon: UTC+0; UTC+1 do último domingo de março 01:00 UTC ao último domingo de outubro 01:00 UTC.
- America/New_York: UTC-5; UTC-4 do 2º domingo de março 07:00 UTC ao 1º domingo de novembro 06:00 UTC.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone, tzinfo

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo, ZoneInfoNotFoundError = None, Exception


def _last_sunday(year, month):
    d = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _nth_sunday(year, month, n):
    d = datetime(year, month, 1)
    first = d + timedelta(days=(6 - d.weekday()) % 7)
    return first + timedelta(weeks=n - 1)


class _Rule(tzinfo):
    def __init__(self, key, std, dst=None, window=None):
        self.key, self._std, self._dst, self._window = key, timedelta(hours=std), dst, window

    def _is_dst_utc(self, utc_naive):
        if self._dst is None:
            return False
        start, end = self._window(utc_naive.year)
        return start <= utc_naive < end

    def utcoffset(self, dt):
        if dt is None:
            return self._std
        if self._dst is None:
            return self._std
        local = dt.replace(tzinfo=None)
        one = timedelta(hours=1)
        dst_ok = self._is_dst_utc(local - self._std - one)   # interpretação como horário de verão
        std_ok = not self._is_dst_utc(local - self._std)      # interpretação como horário padrão
        if dst_ok and std_ok:  # hora ambígua (volta do relógio): fold=0 é a primeira ocorrência (verão)
            return self._std if getattr(dt, "fold", 0) else self._std + one
        if dst_ok:
            return self._std + one
        if std_ok:
            return self._std
        # hora inexistente (relógio adiantado): fold=0 usa o deslocamento anterior à mudança, como o zoneinfo
        return self._std if not getattr(dt, "fold", 0) else self._std + one

    def dst(self, dt):
        return self.utcoffset(dt) - self._std

    def tzname(self, dt):
        return self.key

    def fromutc(self, dt):
        utc = dt.replace(tzinfo=None)
        off = self._std + (timedelta(hours=1) if self._is_dst_utc(utc) else timedelta(0))
        local = (utc + off).replace(tzinfo=self)
        if self.utcoffset(local) != off:  # segunda ocorrência de uma hora ambígua
            local = local.replace(fold=1)
        return local

    def __repr__(self):
        return f"_Rule({self.key})"


_FALLBACK = {
    "America/Sao_Paulo": _Rule("America/Sao_Paulo", -3),
    "Europe/Lisbon": _Rule("Europe/Lisbon", 0, True,
                           lambda y: (_last_sunday(y, 3).replace(hour=1), _last_sunday(y, 10).replace(hour=1))),
    "America/New_York": _Rule("America/New_York", -5, True,
                              lambda y: (_nth_sunday(y, 3, 2).replace(hour=7), _nth_sunday(y, 11, 1).replace(hour=6))),
    "UTC": timezone.utc,
}


def get_tz(key: str):
    if ZoneInfo is not None and os.environ.get("FORCE_TZ_FALLBACK") != "1":
        try:
            return ZoneInfo(key)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    if key in _FALLBACK:
        return _FALLBACK[key]
    raise ValueError(f"Fuso horário não suportado sem tzdata: {key}. Instale 'tzdata' (pip install tzdata).")
