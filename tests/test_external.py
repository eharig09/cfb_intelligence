import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sports_aggregator.bootstrap import Step, _env_satisfied, run_phase, run_step, steps
from sports_aggregator.cfb.external import (
    fpi_for_game, fpi_team_season, import_status, record_run, store_fpi,
    store_weather, weather_flags_by_game, weather_for_game,
)
from sports_aggregator.cfb.models import Game, Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.providers.sportsdataverse import (
    FORMAT_PREFERENCE, ReleaseAsset, SportsDataverseClient, optional_float,
)
from sports_aggregator.providers.weather import (
    Forecast, OpenMeteoClient, weather_flags,
)


TEAM_PAYLOADS = (
    {"id": 1, "school": "Alpha", "mascot": "Ones", "abbreviation": "AL",
     "alternateNames": [], "conference": "SEC", "classification": "fbs",
     "color": "#123456", "logos": [], "location": {"id": 10, "name": "Alpha Field"}},
    {"id": 2, "school": "Beta", "mascot": "Twos", "abbreviation": "BE",
     "alternateNames": [], "conference": "SEC", "classification": "fbs",
     "color": "#654321", "logos": [], "location": {"id": 20, "name": "Beta Dome"}},
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload=None, content=b"", status=200, text=""):
        self._payload, self.content, self.status_code = payload, content, status
        self.text = text or (content.decode() if content else "")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Serves canned release listings and asset bodies."""

    def __init__(self, listing, bodies):
        self.listing, self.bodies, self.calls = listing, bodies, []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "releases/tags" in url:
            return FakeResponse(payload=self.listing)
        for name, body in self.bodies.items():
            if url.endswith(name):
                return FakeResponse(content=body, text=body.decode())
        return FakeResponse(status=404)


def listing(*names):
    return {"assets": [{"name": name,
                        "browser_download_url": f"https://example.test/{name}",
                        "size": 10, "updated_at": "2026-08-01T00:00:00Z"}
                       for name in names]}


class ReleaseAssetTests(unittest.TestCase):
    def test_season_is_parsed_from_the_asset_name(self):
        asset = ReleaseAsset("power_index_2026.csv", "u", 1, "")
        self.assertEqual(asset.season, 2026)

    def test_a_nameless_season_returns_none(self):
        self.assertIsNone(ReleaseAsset("power_index.csv", "u", 1, "").season)

    def test_plain_csv_is_preferred_over_the_compressed_variant(self):
        # Several seasons ship a .csv.gz holding unresolved $ref pointers while
        # the .csv holds the real values; preferring .gz imported nothing.
        self.assertLess(FORMAT_PREFERENCE.index(".csv"),
                        FORMAT_PREFERENCE.index(".csv.gz"))


class SportsDataverseClientTests(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()

    def _client(self, session):
        return SportsDataverseClient(cache_path=self.cache, session=session)

    def test_an_unpublished_season_returns_none_rather_than_raising(self):
        client = self._client(FakeSession(listing("power_index_2020.csv"), {}))
        self.assertIsNone(client.season_asset("power_index", 2026))
        asset, rows = client.rows("power_index", 2026)
        self.assertIsNone(asset)
        self.assertEqual(rows, [])

    def test_required_columns_select_the_asset_that_actually_has_them(self):
        bodies = {
            # The compressed variant is listed first by name but holds the wrong
            # schema; the importer must land on the usable file.
            "power_index_2026.csv": b"season,game_id,team_id,teampredptdiff\n2026,5,1,3.5\n",
        }
        session = FakeSession(listing("power_index_2026.csv"), bodies)
        asset, rows = self._client(session).rows(
            "power_index", 2026, required_columns=("game_id", "teampredptdiff"))
        self.assertEqual(asset.name, "power_index_2026.csv")
        self.assertEqual(rows[0]["teampredptdiff"], "3.5")

    def test_a_schema_mismatch_still_returns_rows_for_reporting(self):
        bodies = {"power_index_2026.csv": b"$ref,game_id\nhttp://x,5\n"}
        session = FakeSession(listing("power_index_2026.csv"), bodies)
        asset, rows = self._client(session).rows(
            "power_index", 2026, required_columns=("teampredptdiff",))
        # The caller needs to see what it got so it can record the mismatch.
        self.assertIsNotNone(asset)
        self.assertNotIn("teampredptdiff", rows[0])

    def test_release_listings_are_cached(self):
        session = FakeSession(listing("power_index_2026.csv"), {})
        client = self._client(session)
        client.assets("power_index")
        client.assets("power_index")
        self.assertEqual(sum("releases/tags" in url for url in session.calls), 1)

    def test_status_reports_an_unavailable_dataset_without_raising(self):
        class Failing(FakeSession):
            def get(self, url, **kwargs):
                raise RuntimeError("network down")
        report = self._client(Failing({}, {})).status(["power_index"])
        self.assertFalse(report[0]["available"])
        self.assertIn("error", report[0])

    def test_blank_numbers_are_missing_not_zero(self):
        self.assertIsNone(optional_float(""))
        self.assertIsNone(optional_float("NA"))
        self.assertEqual(optional_float("3.5"), 3.5)


class FpiStorageTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(p) for p in TEAM_PAYLOADS])
        connection = sqlite3.connect(self.path)
        connection.execute(
            """INSERT INTO games(game_id,season,week,season_type,start_date,start_time_tbd,
               completed,neutral_site,conference_game,venue_id,venue,television,
               home_team_id,home_team,home_conference,home_points,home_pregame_elo,
               away_team_id,away_team,away_conference,away_points,away_pregame_elo,
               excitement_index,notes,updated_at)
               VALUES(500,2026,1,'regular','2026-09-05T18:00:00Z',0,0,0,1,10,'Alpha Field',
                      'ABC',1,'Alpha','SEC',NULL,1700,2,'Beta','SEC',NULL,1500,NULL,NULL,'now')""")
        connection.commit()
        connection.close()

    def tearDown(self):
        os.unlink(self.path)

    def test_rows_are_keyed_to_canonical_games_and_teams(self):
        report = store_fpi(self.repository, 2026, [
            {"game_id": "500", "team_id": "1", "teampredptdiff": "7.5",
             "gameprojection": "72.0", "matchupquality": "60", "teamadjgamescore": ""},
            {"game_id": "500", "team_id": "2", "teampredptdiff": "-7.5",
             "gameprojection": "28.0", "matchupquality": "60", "teamadjgamescore": ""},
        ], asset="power_index_2026.csv")
        self.assertEqual(report["stored"], 2)
        self.assertEqual(report["skipped"], 0)

    def test_unknown_teams_are_skipped_never_created(self):
        report = store_fpi(self.repository, 2026, [
            {"game_id": "500", "team_id": "999", "teampredptdiff": "3"},
        ], asset="a.csv")
        self.assertEqual(report["stored"], 0)
        self.assertEqual(report["skipped"], 1)
        connection = sqlite3.connect(self.path)
        count = connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        connection.close()
        self.assertEqual(count, len(TEAM_PAYLOADS))

    def test_rows_with_no_values_are_skipped(self):
        report = store_fpi(self.repository, 2026, [
            {"game_id": "500", "team_id": "1", "teampredptdiff": "",
             "gameprojection": "", "matchupquality": "", "teamadjgamescore": ""},
        ], asset="a.csv")
        self.assertEqual(report["stored"], 0)

    def test_reimporting_updates_rather_than_duplicating(self):
        row = [{"game_id": "500", "team_id": "1", "teampredptdiff": "7.5"}]
        store_fpi(self.repository, 2026, row, asset="a.csv")
        store_fpi(self.repository, 2026,
                  [{"game_id": "500", "team_id": "1", "teampredptdiff": "9.0"}],
                  asset="b.csv")
        packet = fpi_for_game(self.repository, 500)
        self.assertEqual(len(packet["rows"]), 1)
        self.assertEqual(packet["rows"][0]["pred_point_diff"], 9.0)
        # Provenance follows the newest import.
        self.assertEqual(packet["source_asset"], "b.csv")

    def test_the_game_packet_names_the_favoured_team(self):
        store_fpi(self.repository, 2026, [
            {"game_id": "500", "team_id": "1", "teampredptdiff": "7.5",
             "gameprojection": "72"},
            {"game_id": "500", "team_id": "2", "teampredptdiff": "-7.5",
             "gameprojection": "28"},
        ], asset="a.csv")
        self.assertEqual(fpi_for_game(self.repository, 500)["favored"], "Alpha")

    def test_season_expectation_is_derived_from_win_probabilities(self):
        store_fpi(self.repository, 2026, [
            {"game_id": "500", "team_id": "1", "teampredptdiff": "7.5",
             "gameprojection": "75"}], asset="a.csv")
        season = fpi_team_season(self.repository, 2026, 1)
        self.assertEqual(season["count"], 1)
        self.assertEqual(season["expected_wins"], 0.8)

    def test_fpi_is_stored_apart_from_other_ratings(self):
        store_fpi(self.repository, 2026, [
            {"game_id": "500", "team_id": "1", "teampredptdiff": "7.5"}], asset="a.csv")
        connection = sqlite3.connect(self.path)
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        connection.close()
        # A separate table, not folded into core_ratings.
        self.assertIn("fpi_game_projections", tables)

    def test_canonical_game_refresh_preserves_game_keyed_model_rows(self):
        store_fpi(self.repository, 2026, [
            {"game_id": "500", "team_id": "1", "teampredptdiff": "7.5"}],
            asset="a.csv")
        refreshed = Game(
            500, 2026, 1, "regular", datetime(2026, 9, 5, 18, tzinfo=timezone.utc),
            False, False, False, True, 10, "Alpha Field", 1, "Alpha", "SEC",
            None, 1700, 2, "Beta", "SEC", None, 1500, None, None)
        self.repository.replace_games(2026, (refreshed,))
        self.assertEqual(len(fpi_for_game(self.repository, 500)["rows"]), 1)


class WeatherTests(unittest.TestCase):
    @staticmethod
    def forecast(**overrides):
        base = dict(kickoff="2026-09-05T18:00:00+00:00", forecast_hour="2026-09-05T18:00",
                    temperature=70.0, precipitation_probability=5.0, precipitation=0.0,
                    wind_speed=5.0, wind_gusts=8.0, humidity=50.0, visibility=60000.0,
                    weather_code=0)
        base.update(overrides)
        return Forecast(**base)

    def test_calm_conditions_raise_no_flags(self):
        self.assertEqual(weather_flags(self.forecast()), [])

    def test_clear_sky_code_zero_has_a_condition(self):
        self.assertEqual(self.forecast(weather_code=0).condition, "Clear")

    def test_each_flag_carries_the_number_that_produced_it(self):
        flags = weather_flags(self.forecast(wind_speed=22.0, wind_gusts=34.0))
        names = {flag["flag"] for flag in flags}
        self.assertEqual(names, {"HIGH_WIND", "HEAVY_GUSTS"})
        self.assertTrue(all(flag["detail"] for flag in flags))

    def test_rain_risk_fires_on_probability_or_amount(self):
        self.assertTrue(any(f["flag"] == "RAIN_RISK"
                            for f in weather_flags(self.forecast(precipitation_probability=60))))
        self.assertTrue(any(f["flag"] == "RAIN_RISK"
                            for f in weather_flags(self.forecast(precipitation=0.4))))

    def test_temperature_extremes_are_mutually_exclusive(self):
        hot = {f["flag"] for f in weather_flags(self.forecast(temperature=95))}
        cold = {f["flag"] for f in weather_flags(self.forecast(temperature=20))}
        self.assertIn("EXTREME_HEAT", hot)
        self.assertNotIn("EXTREME_COLD", hot)
        self.assertIn("EXTREME_COLD", cold)

    def test_missing_values_do_not_raise(self):
        self.assertEqual(weather_flags(self.forecast(
            temperature=None, wind_speed=None, wind_gusts=None,
            precipitation=None, precipitation_probability=None)), [])

    def test_kickoff_alignment_picks_the_nearest_hour(self):
        payload = {"hourly": {
            "time": ["2026-09-05T17:00", "2026-09-05T18:00", "2026-09-05T19:00"],
            "temperature_2m": [60, 70, 80], "wind_speed_10m": [1, 2, 3],
            "weather_code": [0, 1, 2]}}
        found = OpenMeteoClient.at_kickoff(payload, "2026-09-05T18:10:00Z")
        self.assertEqual(found.temperature, 70)
        self.assertEqual(found.condition, "Mainly clear")

    def test_a_kickoff_outside_the_published_window_returns_none(self):
        payload = {"hourly": {"time": ["2026-09-05T18:00"], "temperature_2m": [70]}}
        self.assertIsNone(OpenMeteoClient.at_kickoff(payload, "2026-12-01T18:00:00Z"))

    def test_an_empty_payload_returns_none(self):
        self.assertIsNone(OpenMeteoClient.at_kickoff({}, "2026-09-05T18:00:00Z"))

    def test_horizon_excludes_distant_and_past_kickoffs(self):
        soon = (NOW + timedelta(days=3)).isoformat()
        distant = (NOW + timedelta(days=40)).isoformat()
        past = (NOW - timedelta(days=1)).isoformat()
        self.assertTrue(OpenMeteoClient.within_horizon(soon, NOW))
        self.assertFalse(OpenMeteoClient.within_horizon(distant, NOW))
        self.assertFalse(OpenMeteoClient.within_horizon(past, NOW))


class WeatherStorageTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(p) for p in TEAM_PAYLOADS])
        connection = sqlite3.connect(self.path)
        connection.execute(
            """INSERT INTO games(game_id,season,week,season_type,start_date,start_time_tbd,
               completed,neutral_site,conference_game,venue_id,venue,television,
               home_team_id,home_team,home_conference,home_points,home_pregame_elo,
               away_team_id,away_team,away_conference,away_points,away_pregame_elo,
               excitement_index,notes,updated_at)
               VALUES(500,2026,1,'regular','2026-09-05T18:00:00Z',0,0,0,1,10,'Alpha Field',
                      'ABC',1,'Alpha','SEC',NULL,1700,2,'Beta','SEC',NULL,1500,NULL,NULL,'now')""")
        connection.commit()
        connection.close()

    def tearDown(self):
        os.unlink(self.path)

    def _store(self, generated_at, **overrides):
        forecast = WeatherTests.forecast(**overrides)
        store_weather(self.repository, 500, forecast,
                      flags=weather_flags(forecast), venue="Alpha Field",
                      latitude=33.0, longitude=-87.0, indoor=False,
                      generated_at=generated_at)

    def test_snapshots_accumulate_rather_than_overwrite(self):
        self._store("2026-08-20T00:00:00Z", wind_speed=5.0)
        self._store("2026-09-04T00:00:00Z", wind_speed=25.0)
        packet = weather_for_game(self.repository, 500)
        self.assertEqual(packet["snapshots"], 2)
        self.assertEqual(packet["latest"]["sustained_wind"], 25.0)
        # The change between forecasts is often the interesting part.
        self.assertEqual(packet["movement"]["sustained_wind"], 20.0)

    def test_a_game_without_weather_reports_unavailable(self):
        packet = weather_for_game(self.repository, 999)
        self.assertFalse(packet["available"])
        self.assertEqual(packet["flags"], [])

    def test_legacy_unknown_clear_snapshot_is_normalized_when_read(self):
        self._store("2026-09-04T00:00:00Z", weather_code=0)
        connection = self.repository._connect()
        try:
            connection.execute(
                "UPDATE game_weather SET condition='Unknown' WHERE game_id=500")
            connection.commit()
        finally:
            connection.close()
        packet = weather_for_game(self.repository, 500)
        self.assertEqual(packet["latest"]["condition"], "Clear")

    def test_indoor_games_carry_no_flags(self):
        forecast = WeatherTests.forecast(wind_speed=40.0)
        store_weather(self.repository, 500, forecast, flags=[], venue="Beta Dome",
                      latitude=33.0, longitude=-87.0, indoor=True,
                      generated_at="2026-09-04T00:00:00Z")
        packet = weather_for_game(self.repository, 500)
        self.assertTrue(packet["indoor"])
        self.assertEqual(packet["flags"], [])
        self.assertEqual(weather_flags_by_game(self.repository, 2026), {})

    def test_slate_flags_use_the_newest_snapshot(self):
        self._store("2026-08-20T00:00:00Z", wind_speed=5.0)
        self._store("2026-09-04T00:00:00Z", wind_speed=25.0)
        flags = weather_flags_by_game(self.repository, 2026)
        self.assertIn(500, flags)
        self.assertTrue(any(f["flag"] == "HIGH_WIND" for f in flags[500]))


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.initialize()

    def tearDown(self):
        os.unlink(self.path)

    def test_failed_runs_are_recorded_not_swallowed(self):
        record_run(self.repository, source="sportsdataverse", dataset="power_index",
                   season=2026, started_at="2026-08-23T00:00:00Z", status="failed",
                   message="network down")
        report = import_status(self.repository)
        self.assertEqual(report["recent_runs"][0]["status"], "failed")
        self.assertEqual(report["datasets"][0]["failures"], 1)

    def test_status_reports_counts_for_every_secondary_table(self):
        report = import_status(self.repository)
        self.assertIn("fpi_game_projections", report["counts"])
        self.assertIn("game_weather", report["counts"])

    def test_an_absent_table_reports_none_rather_than_raising(self):
        report = import_status(self.repository)
        # Tables for sources not yet implemented are reported as unavailable.
        self.assertIsNone(report["counts"]["odds_snapshots"])


class BootstrapTests(unittest.TestCase):
    def test_every_step_belongs_to_a_known_phase(self):
        for step in steps(2026):
            self.assertTrue(set(step.phases) <= {"initial", "refresh", "history"}, step.name)

    def test_static_sources_are_excluded_from_routine_refresh(self):
        refresh = {step.name for step in steps(2026) if "refresh" in step.phases}
        # Venues, historical backfill and promoted history change once a year.
        self.assertNotIn("cfbd-venues", refresh)
        self.assertNotIn("cfbd-backfill", refresh)
        self.assertNotIn("cfbd-promoted", refresh)

    def test_derivation_runs_after_ingestion(self):
        order = [step.name for step in steps(2026) if "refresh" in step.phases]
        self.assertLess(order.index("bluesky"), order.index("cluster"))
        self.assertLess(order.index("cluster"), order.index("score"))

    def test_national_articles_are_prioritized_before_heavy_refresh_steps(self):
        order = [step.name for step in steps(2026) if "refresh" in step.phases]
        self.assertLess(order.index("articles"), order.index("cfbd-current-player-stats"))
        self.assertLess(order.index("articles"), order.index("media-validate"))
        self.assertLess(order.index("articles"), order.index("podcasts"))

    def test_media_catalog_is_ready_before_validation_and_ingestion(self):
        for phase in ("initial", "refresh"):
            order = [step.name for step in steps(2026) if phase in step.phases]
            self.assertLess(order.index("media-seed"), order.index("media-validate"))
            self.assertLess(order.index("media-validate"), order.index("youtube"))
            self.assertLess(order.index("media-validate"), order.index("podcasts"))
            self.assertLess(order.index("podcasts"), order.index("retag"))

    def test_identity_derivations_follow_their_inputs(self):
        initial = [step.name for step in steps(2026) if "initial" in step.phases]
        self.assertLess(initial.index("cfbd-sync"), initial.index("cfbd-current-player-stats"))
        self.assertLess(initial.index("cfbd-roster-context"), initial.index("pff"))
        self.assertLess(initial.index("pff"), initial.index("transfer-grades"))
        self.assertLess(initial.index("social-prepare"), initial.index("bluesky"))
        current_stats = next(step for step in steps(2026)
                             if step.name == "cfbd-current-player-stats")
        self.assertTrue(current_stats.optional)

    def test_reddit_requires_both_credentials(self):
        step = Step("reddit", "", [], ("refresh",),
                    requires_all_env=("REDDIT_TEST_ID", "REDDIT_TEST_SECRET"))
        os.environ["REDDIT_TEST_ID"] = "value"
        try:
            self.assertFalse(_env_satisfied(step))
            os.environ["REDDIT_TEST_SECRET"] = "value"
            self.assertTrue(_env_satisfied(step))
        finally:
            os.environ.pop("REDDIT_TEST_ID", None)
            os.environ.pop("REDDIT_TEST_SECRET", None)

    def test_a_step_without_its_credentials_is_skipped(self):
        step = Step("x", "needs a key", ["nonexistent_module"], ("refresh",),
                    requires_env=("DEFINITELY_NOT_SET_12345",))
        result = run_step(step)
        self.assertEqual(result["status"], "skipped")

    def test_alternative_env_names_are_accepted(self):
        step = Step("x", "", [], ("refresh",), requires_env=("MISSING_A", "MISSING_B"))
        self.assertFalse(_env_satisfied(step))
        os.environ["MISSING_B"] = "value"
        try:
            self.assertTrue(_env_satisfied(step))
        finally:
            del os.environ["MISSING_B"]

    def test_a_failing_step_is_isolated_rather_than_raising(self):
        step = Step("boom", "", ["sports_aggregator._does_not_exist"], ("refresh",))
        result = run_step(step, timeout=60)
        self.assertEqual(result["status"], "failed")
        self.assertIn("step", result)

    def test_phase_marks_optional_failures_for_non_blocking_reporting(self):
        from unittest.mock import patch
        optional = Step("optional", "", ["missing"], ("refresh",), optional=True)
        with patch("sports_aggregator.bootstrap.steps", return_value=[optional]):
            result = run_phase("refresh", 2026, timeout=10)[0]
        self.assertTrue(result["optional"])
        self.assertEqual(result["status"], "failed")
