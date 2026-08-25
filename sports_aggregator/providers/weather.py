"""Kickoff weather forecasts from Open-Meteo.

Open-Meteo is used because it needs no API key and no account for
non-commercial use, which keeps the integration free of secrets and of a paid
dependency. Its free tier is rate-limited and asks that callers stay reasonable,
so one request covers a venue's whole 16-day window rather than one request per
game, and responses are cached on disk.

The forecast horizon is roughly 16 days. A game further out than that has no
forecast, which is a normal state and is reported as such rather than treated as
a failure -- most of a season's schedule is beyond the horizon at any moment.

Snapshots are stored rather than overwritten. A forecast taken ten days out and
one taken on game morning are different pieces of information, and the change
between them is often the interesting part.

Licensing: Open-Meteo publishes its data under CC-BY 4.0 for non-commercial use.
Attribution is retained on every stored row via the source field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: Hourly variables requested. Kept to what a football preview can actually use.
HOURLY_VARIABLES = (
    "temperature_2m", "precipitation_probability", "precipitation",
    "wind_speed_10m", "wind_gusts_10m", "relative_humidity_2m",
    "visibility", "weather_code",
)

#: Open-Meteo publishes about 16 days ahead.
FORECAST_HORIZON_DAYS = 16

#: WMO weather codes grouped into labels a reader understands.
WEATHER_CODES = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


def weather_condition(code: int | None) -> str:
    """Translate a WMO code without treating valid clear-sky code zero as missing."""
    if code is None:
        return "Condition unavailable"
    return WEATHER_CODES.get(int(code), f"Weather code {code}")

#: Thresholds for the explainable flags. Chosen for football, not meteorology:
#: 15 mph sustained is where the passing and kicking game is discussed.
HIGH_WIND_MPH = 15.0
HEAVY_GUST_MPH = 25.0
RAIN_RISK_PERCENT = 40.0
HEAVY_RAIN_INCHES = 0.15
EXTREME_HEAT_F = 88.0
EXTREME_COLD_F = 28.0


@dataclass(frozen=True)
class Forecast:
    """One hourly forecast, aligned to a kickoff."""

    kickoff: str
    forecast_hour: str
    temperature: float | None
    precipitation_probability: float | None
    precipitation: float | None
    wind_speed: float | None
    wind_gusts: float | None
    humidity: float | None
    visibility: float | None
    weather_code: int | None

    @property
    def condition(self) -> str:
        return weather_condition(self.weather_code)


def weather_flags(forecast: Forecast) -> list[dict[str, str]]:
    """Explainable flags, each carrying the number that produced it."""
    flags: list[dict[str, str]] = []
    if forecast.wind_speed is not None and forecast.wind_speed >= HIGH_WIND_MPH:
        flags.append({"flag": "HIGH_WIND",
                      "detail": f"{forecast.wind_speed:.0f} mph sustained wind"})
    if forecast.wind_gusts is not None and forecast.wind_gusts >= HEAVY_GUST_MPH:
        flags.append({"flag": "HEAVY_GUSTS",
                      "detail": f"gusts to {forecast.wind_gusts:.0f} mph"})
    probability = forecast.precipitation_probability
    amount = forecast.precipitation
    if ((probability is not None and probability >= RAIN_RISK_PERCENT)
            or (amount is not None and amount >= HEAVY_RAIN_INCHES)):
        parts = []
        if probability is not None:
            parts.append(f"{probability:.0f}% chance")
        if amount:
            parts.append(f"{amount:.2f} in")
        flags.append({"flag": "RAIN_RISK", "detail": ", ".join(parts) or "precipitation expected"})
    if forecast.temperature is not None:
        if forecast.temperature >= EXTREME_HEAT_F:
            flags.append({"flag": "EXTREME_HEAT",
                          "detail": f"{forecast.temperature:.0f}°F at kickoff"})
        elif forecast.temperature <= EXTREME_COLD_F:
            flags.append({"flag": "EXTREME_COLD",
                          "detail": f"{forecast.temperature:.0f}°F at kickoff"})
    return flags


class OpenMeteoClient:
    """Fetches hourly forecasts for a venue and aligns them to kickoff."""

    name = "open-meteo"

    def __init__(self, cache_path: str | Path = "instance/weather",
                 base_url: str = FORECAST_URL, session=None, timeout: int = 40,
                 cache_seconds: int = 3600) -> None:
        self.cache_path = Path(cache_path)
        self.base_url = base_url
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "cfb-intelligence/1.0 (weather)")
        # A scheduled refresh can hit dozens of venues in a short burst. Retry
        # transient rate limits and upstream errors rather than turning one brief
        # Open-Meteo hiccup into 60 failed games.
        if session is None:
            retry = Retry(
                total=4,
                connect=3,
                read=3,
                status=4,
                backoff_factor=0.75,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET"}),
                respect_retry_after_header=True,
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)
        self.timeout = timeout
        self.cache_seconds = cache_seconds

    def _cache_file(self, latitude: float, longitude: float) -> Path:
        return self.cache_path / f"{latitude:.3f}_{longitude:.3f}.json"

    def venue_forecast(self, latitude: float, longitude: float, *,
                       force: bool = False) -> dict[str, Any]:
        """The 16-day hourly forecast for one venue.

        One request per venue covers every home game inside the horizon, which
        keeps this well within a free tier that asks callers to be reasonable.
        """
        cached = self._cache_file(latitude, longitude)
        if not force and cached.exists():
            age = datetime.now(timezone.utc).timestamp() - cached.stat().st_mtime
            if age < self.cache_seconds:
                return json.loads(cached.read_text(encoding="utf-8"))
        response = self.session.get(self.base_url, params={
            "latitude": latitude, "longitude": longitude,
            "hourly": ",".join(HOURLY_VARIABLES),
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
            "precipitation_unit": "inch", "timezone": "UTC",
            "forecast_days": FORECAST_HORIZON_DAYS,
        }, timeout=self.timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = (response.text or "").strip().replace("\n", " ")[:240]
            suffix = f" response={detail}" if detail else ""
            raise RuntimeError(
                f"Open-Meteo HTTP {response.status_code} for "
                f"{latitude:.3f},{longitude:.3f}{suffix}"
            ) from exc
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError(
                f"Open-Meteo invalid response for {latitude:.3f},{longitude:.3f}: "
                f"{str(payload)[:240]}"
            )
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    @staticmethod
    def at_kickoff(payload: dict[str, Any], kickoff: str) -> Forecast | None:
        """The forecast hour nearest a kickoff, or None if outside the horizon."""
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            return None
        try:
            target = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
        except ValueError:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        best_index, best_delta = None, None
        for index, stamp in enumerate(times):
            try:
                moment = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            delta = abs((moment - target).total_seconds())
            if best_delta is None or delta < best_delta:
                best_index, best_delta = index, delta
        # More than an hour from the nearest sample means the kickoff sits
        # outside the published window, not that this hour is close enough.
        if best_index is None or best_delta is None or best_delta > 3600:
            return None

        def value(name: str) -> Any:
            series = hourly.get(name) or []
            return series[best_index] if best_index < len(series) else None

        code = value("weather_code")
        return Forecast(
            kickoff=target.isoformat(),
            forecast_hour=times[best_index],
            temperature=value("temperature_2m"),
            precipitation_probability=value("precipitation_probability"),
            precipitation=value("precipitation"),
            wind_speed=value("wind_speed_10m"),
            wind_gusts=value("wind_gusts_10m"),
            humidity=value("relative_humidity_2m"),
            visibility=value("visibility"),
            weather_code=int(code) if code is not None else None,
        )

    @staticmethod
    def within_horizon(kickoff: str, now: datetime | None = None) -> bool:
        """Whether a kickoff can have a forecast at all."""
        try:
            target = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
        except ValueError:
            return False
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        return reference <= target <= reference + timedelta(days=FORECAST_HORIZON_DAYS)
