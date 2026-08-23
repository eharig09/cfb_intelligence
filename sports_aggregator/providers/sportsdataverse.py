"""Static-release downloader for the SportsDataverse college-football datasets.

SportsDataverse publishes ESPN-derived college-football data as release assets on
``sportsdataverse/sportsdataverse-data``, one asset per season per format under
``espn_cfb_*`` tags. Static assets are preferred to undocumented live ESPN
endpoints: they are versioned, cacheable, and do not break when ESPN changes an
internal route.

Two things this module learned the hard way and encodes:

* **Asset URLs are resolved, never constructed.** Formats are not uniform across
  seasons -- the 2025 power-index release ships ``.csv.gz`` while 2026 ships only
  ``.csv`` -- so building a URL by pattern produces a 404 on exactly the season
  you care about most.
* **The published schema can differ from the documentation.** ``DATASETS.md``
  describes the power index in long format; the released files are wide. Callers
  receive the rows as published and normalize them themselves.

Licensing: the data is ESPN-derived and redistributed by SportsDataverse under
the terms of that project. It is used here for non-commercial analysis, with
provenance retained on every stored row. No credentials are required.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import io
import json
from pathlib import Path
from typing import Any, Iterator

import requests

from sports_aggregator.providers.base import ProviderFetchError


DATA_REPOSITORY = "sportsdataverse/sportsdataverse-data"
RELEASES_API = "https://api.github.com/repos/{repository}/releases/tags/{tag}"

#: Release tags this application knows how to consume.
TAGS = {
    "power_index": "espn_cfb_power_index",
    "injuries": "espn_cfb_injuries",
    "game_rosters": "espn_cfb_game_rosters",
    "betting": "espn_cfb_betting",
    "player_box": "espn_cfb_player_box",
    "team_box": "espn_cfb_team_box",
    "drives": "espn_cfb_drives",
    "play_participants": "espn_cfb_play_participants",
}

#: Candidate asset formats, in order of preference. Parquet needs an optional
#: dependency, so the CSV variants are the dependable path.
#:
#: Plain CSV is preferred over the compressed variant deliberately. The two are
#: not always the same data: several power-index seasons ship a `.csv.gz` holding
#: unresolved ESPN `$ref` pointers while the `.csv` holds the actual values.
#: Preferring the compressed file silently imported zero usable rows.
FORMAT_PREFERENCE = (".csv", ".csv.gz")


@dataclass(frozen=True)
class ReleaseAsset:
    """One downloadable file from a release."""

    name: str
    url: str
    size: int
    updated_at: str

    @property
    def season(self) -> int | None:
        digits = "".join(character if character.isdigit() else " "
                         for character in self.name).split()
        for token in digits:
            if len(token) == 4 and token.startswith("20"):
                return int(token)
        return None


class SportsDataverseClient:
    """Resolves and downloads SportsDataverse release assets, with a disk cache."""

    name = "sportsdataverse"

    def __init__(self, cache_path: str | Path = "instance/sportsdataverse",
                 repository: str = DATA_REPOSITORY, session=None, timeout: int = 120) -> None:
        self.cache_path = Path(cache_path)
        self.repository = repository
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "cfb-intelligence/1.0 (data ingestion)")
        self.timeout = timeout

    # -- release discovery -------------------------------------------------

    def assets(self, dataset: str, *, force: bool = False) -> list[ReleaseAsset]:
        """Every asset published under a dataset's release tag.

        The listing itself is cached, because a release is republished at most
        daily and the API is rate-limited for unauthenticated callers.
        """
        tag = TAGS.get(dataset, dataset)
        cached = self.cache_path / "releases" / f"{tag}.json"
        if not force and cached.exists():
            payload = json.loads(cached.read_text(encoding="utf-8"))
        else:
            response = self.session.get(
                RELEASES_API.format(repository=self.repository, tag=tag),
                timeout=self.timeout)
            if response.status_code == 404:
                raise ProviderFetchError(f"SportsDataverse release tag not found: {tag}")
            response.raise_for_status()
            payload = response.json()
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(payload), encoding="utf-8")
        return [
            ReleaseAsset(name=item["name"], url=item["browser_download_url"],
                         size=int(item.get("size") or 0),
                         updated_at=str(item.get("updated_at") or ""))
            for item in payload.get("assets") or []
        ]

    def season_assets(self, dataset: str, season: int, *,
                      force: bool = False) -> list[ReleaseAsset]:
        """Every usable asset for one season, best format first."""
        candidates = [asset for asset in self.assets(dataset, force=force)
                      if asset.season == season]
        ordered: list[ReleaseAsset] = []
        for suffix in FORMAT_PREFERENCE:
            ordered.extend(asset for asset in candidates
                           if asset.name.endswith(suffix) and asset not in ordered)
        return ordered

    def season_asset(self, dataset: str, season: int, *,
                     force: bool = False) -> ReleaseAsset | None:
        """The best available asset for one season, or None if unpublished.

        Returning None rather than raising is deliberate: a dataset that has not
        published a given season is a normal state, not a failure.
        """
        assets = self.season_assets(dataset, season, force=force)
        return assets[0] if assets else None

    def published_seasons(self, dataset: str, *, force: bool = False) -> list[int]:
        seasons = {asset.season for asset in self.assets(dataset, force=force)}
        return sorted(season for season in seasons if season)

    # -- download ----------------------------------------------------------

    def download(self, asset: ReleaseAsset, *, force: bool = False) -> bytes:
        """Fetch an asset, caching the bytes so a re-import costs nothing."""
        cached = self.cache_path / "assets" / asset.name
        if not force and cached.exists() and cached.stat().st_size > 0:
            return cached.read_bytes()
        response = self.session.get(asset.url, timeout=self.timeout)
        response.raise_for_status()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(response.content)
        return response.content

    def _read(self, asset: ReleaseAsset, *, force: bool = False) -> list[dict[str, Any]]:
        payload = self.download(asset, force=force)
        if asset.name.endswith(".gz"):
            payload = gzip.decompress(payload)
        text = payload.decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(text)))

    def rows(self, dataset: str, season: int, *, force: bool = False,
             required_columns: tuple[str, ...] = ()
             ) -> tuple[ReleaseAsset | None, list[dict[str, Any]]]:
        """Rows for one dataset-season, exactly as published.

        Normalization is the importer's job: this returns what the file says so
        a schema change surfaces as unexpected columns rather than silent loss.

        When ``required_columns`` is given, each candidate format is checked and
        the first one that actually carries those columns is used. Formats within
        a release are not guaranteed to hold the same content, and a caller that
        trusts the preferred format alone can import an empty result without
        anything looking wrong.
        """
        candidates = self.season_assets(dataset, season, force=force)
        if not candidates:
            return None, []
        fallback: tuple[ReleaseAsset, list[dict[str, Any]]] | None = None
        for asset in candidates:
            rows = self._read(asset, force=force)
            if not required_columns:
                return asset, rows
            if rows and set(required_columns) <= set(rows[0].keys()):
                return asset, rows
            if fallback is None:
                fallback = (asset, rows)
        # Nothing matched the expected schema; return the best-effort read so the
        # caller can record what it actually found.
        return fallback if fallback else (candidates[0], [])

    def status(self, datasets: Iterator[str] | None = None) -> list[dict[str, Any]]:
        """What each dataset currently publishes, for reporting."""
        report = []
        for dataset in datasets or TAGS:
            entry: dict[str, Any] = {"dataset": dataset, "tag": TAGS.get(dataset, dataset)}
            try:
                assets = self.assets(dataset)
                seasons = sorted({asset.season for asset in assets if asset.season})
                entry.update({
                    "assets": len(assets),
                    "seasons": seasons,
                    "latest_season": seasons[-1] if seasons else None,
                    "available": bool(assets),
                    "updated_at": max((asset.updated_at for asset in assets), default=None),
                })
            except Exception as exc:
                entry.update({"assets": 0, "seasons": [], "available": False,
                              "error": str(exc)[:200]})
            report.append(entry)
        return report


def optional_float(value: Any) -> float | None:
    """CSV blanks are missing values, not zeros."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"NA", "NAN", "NULL", "NONE"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def optional_int(value: Any) -> int | None:
    number = optional_float(value)
    return int(number) if number is not None else None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
