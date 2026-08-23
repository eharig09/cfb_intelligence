"""SQLite persistence for canonical CFBD entities and raw-derived metrics."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import re
from typing import Any, Iterable, Iterator

from sports_aggregator.cfb.models import Game, PollRanking, Team, normalize_alias
from sports_aggregator.cfb.statlines import category_label, qualifier, sort_stat


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY,
    school TEXT NOT NULL UNIQUE,
    mascot TEXT,
    abbreviation TEXT,
    conference TEXT,
    division TEXT,
    classification TEXT,
    color TEXT,
    alternate_color TEXT,
    logos_json TEXT NOT NULL DEFAULT '[]',
    venue_id INTEGER,
    venue_name TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_aliases (
    team_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'cfbd',
    PRIMARY KEY (team_id, normalized_alias),
    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_team_alias_normalized ON team_aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS players (
 season INTEGER NOT NULL, player_id TEXT NOT NULL, first_name TEXT NOT NULL,
 last_name TEXT NOT NULL, normalized_name TEXT NOT NULL, team TEXT NOT NULL,
 position TEXT, jersey INTEGER, height REAL, weight INTEGER, class_year INTEGER,
 PRIMARY KEY(season,player_id,team));
CREATE INDEX IF NOT EXISTS idx_players_name ON players(season,normalized_name);
CREATE INDEX IF NOT EXISTS idx_players_team ON players(season,team);

CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    season_type TEXT NOT NULL,
    start_date TEXT NOT NULL,
    start_time_tbd INTEGER NOT NULL,
    completed INTEGER NOT NULL,
    neutral_site INTEGER NOT NULL,
    conference_game INTEGER NOT NULL,
    venue_id INTEGER,
    venue TEXT,
    television TEXT,
    home_team_id INTEGER NOT NULL,
    home_team TEXT NOT NULL,
    home_conference TEXT,
    home_points INTEGER,
    home_pregame_elo INTEGER,
    away_team_id INTEGER NOT NULL,
    away_team TEXT NOT NULL,
    away_conference TEXT,
    away_points INTEGER,
    away_pregame_elo INTEGER,
    excitement_index REAL,
    notes TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_season_start ON games(season, start_date);
CREATE INDEX IF NOT EXISTS idx_games_home_team ON games(home_team_id, season);
CREATE INDEX IF NOT EXISTS idx_games_away_team ON games(away_team_id, season);

CREATE TABLE IF NOT EXISTS team_records (
    season INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team TEXT NOT NULL,
    conference TEXT,
    division TEXT,
    games INTEGER NOT NULL,
    wins INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    ties INTEGER NOT NULL,
    expected_wins REAL,
    conference_games INTEGER NOT NULL DEFAULT 0,
    conference_wins INTEGER NOT NULL DEFAULT 0,
    conference_losses INTEGER NOT NULL DEFAULT 0,
    conference_ties INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS player_season_stats (
    season INTEGER NOT NULL, player_id TEXT NOT NULL, player TEXT NOT NULL,
    team TEXT NOT NULL, conference TEXT, position TEXT,
    category TEXT NOT NULL, stat_type TEXT NOT NULL,
    stat_value TEXT, numeric_value REAL,
    PRIMARY KEY(season,player_id,category,stat_type)
);
CREATE INDEX IF NOT EXISTS idx_player_stats_conference
    ON player_season_stats(season,conference,category,stat_type,numeric_value DESC);
CREATE INDEX IF NOT EXISTS idx_player_stats_team
    ON player_season_stats(season,team,category,stat_type,numeric_value DESC);

CREATE TABLE IF NOT EXISTS player_transfers (
    season INTEGER NOT NULL, normalized_name TEXT NOT NULL,
    first_name TEXT NOT NULL, last_name TEXT NOT NULL, position TEXT,
    origin TEXT NOT NULL, destination TEXT, transfer_date TEXT,
    rating REAL, stars INTEGER, eligibility TEXT,
    PRIMARY KEY(season,normalized_name,origin)
);
CREATE INDEX IF NOT EXISTS idx_transfers_origin ON player_transfers(season,origin);
CREATE INDEX IF NOT EXISTS idx_transfers_destination ON player_transfers(season,destination);

CREATE TABLE IF NOT EXISTS draft_picks (
    draft_year INTEGER NOT NULL, overall_pick INTEGER NOT NULL,
    round INTEGER NOT NULL, round_pick INTEGER NOT NULL,
    college_athlete_id TEXT, college_team_id INTEGER, college_team TEXT NOT NULL,
    college_conference TEXT, nfl_team_id INTEGER, nfl_team TEXT NOT NULL,
    player_name TEXT NOT NULL, normalized_name TEXT NOT NULL, position TEXT,
    pre_draft_ranking INTEGER, pre_draft_position_ranking INTEGER,
    pre_draft_grade INTEGER,
    PRIMARY KEY(draft_year,overall_pick)
);
CREATE INDEX IF NOT EXISTS idx_draft_school ON draft_picks(draft_year,college_team);
CREATE INDEX IF NOT EXISTS idx_draft_player ON draft_picks(normalized_name,draft_year);

CREATE TABLE IF NOT EXISTS returning_production (
    season INTEGER NOT NULL, team TEXT NOT NULL, conference TEXT,
    total_ppa REAL, passing_ppa REAL, receiving_ppa REAL, rushing_ppa REAL,
    percent_ppa REAL, percent_passing_ppa REAL,
    percent_receiving_ppa REAL, percent_rushing_ppa REAL,
    usage REAL, passing_usage REAL, receiving_usage REAL, rushing_usage REAL,
    PRIMARY KEY(season,team)
);

CREATE TABLE IF NOT EXISTS rankings (
    season INTEGER NOT NULL,
    season_type TEXT NOT NULL,
    week INTEGER NOT NULL,
    poll TEXT NOT NULL,
    is_final INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    team_id INTEGER,
    school TEXT NOT NULL,
    conference TEXT,
    first_place_votes INTEGER,
    points INTEGER,
    PRIMARY KEY (season, season_type, week, poll, school)
);
CREATE INDEX IF NOT EXISTS idx_rankings_team ON rankings(season, school, week);

CREATE TABLE IF NOT EXISTS team_stats (
    season INTEGER NOT NULL,
    team TEXT NOT NULL,
    conference TEXT,
    stat_name TEXT NOT NULL,
    stat_value TEXT,
    PRIMARY KEY (season, team, stat_name)
);

CREATE TABLE IF NOT EXISTS team_advanced_stats (
    season INTEGER NOT NULL,
    team TEXT NOT NULL,
    conference TEXT,
    offense_success_rate REAL,
    defense_success_rate REAL,
    offense_explosiveness REAL,
    defense_explosiveness REAL,
    offense_ppa REAL,
    defense_ppa REAL,
    offense_points_per_opportunity REAL,
    defense_points_per_opportunity REAL,
    offense_havoc REAL,
    defense_havoc REAL,
    offense_json TEXT NOT NULL,
    defense_json TEXT NOT NULL,
    PRIMARY KEY (season, team)
);

CREATE TABLE IF NOT EXISTS core_ratings (
    season INTEGER NOT NULL,
    through_season_type TEXT NOT NULL,
    through_week INTEGER NOT NULL,
    team TEXT NOT NULL,
    conference TEXT,
    overall REAL NOT NULL,
    offense REAL NOT NULL,
    defense REAL NOT NULL,
    offense_plays INTEGER NOT NULL,
    defense_plays INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    PRIMARY KEY (season, through_season_type, through_week, team)
);
CREATE INDEX IF NOT EXISTS idx_core_latest ON core_ratings(season, through_week, overall DESC);

CREATE TABLE IF NOT EXISTS pff_players (
    season INTEGER NOT NULL,
    pff_player_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    position TEXT,
    pff_team_name TEXT NOT NULL,
    cfbd_team_id INTEGER,
    cfbd_team TEXT,
    cfbd_player_id TEXT,
    candidate_cfbd_player_id TEXT,
    match_status TEXT NOT NULL,
    match_confidence REAL NOT NULL,
    interest_score REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (season, pff_player_id)
);
CREATE INDEX IF NOT EXISTS idx_pff_players_team ON pff_players(season, cfbd_team_id);
CREATE INDEX IF NOT EXISTS idx_pff_players_interest ON pff_players(season, interest_score DESC);

CREATE TABLE IF NOT EXISTS pff_player_metrics (
    season INTEGER NOT NULL,
    pff_player_id TEXT NOT NULL,
    dataset TEXT NOT NULL,
    source_file TEXT NOT NULL,
    game_count INTEGER,
    primary_grade REAL,
    usage_count REAL,
    metrics_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (season, pff_player_id, dataset),
    FOREIGN KEY (season, pff_player_id)
        REFERENCES pff_players(season, pff_player_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pff_position_groups (
    season INTEGER NOT NULL,
    cfbd_team_id INTEGER,
    pff_team_name TEXT NOT NULL,
    position_group TEXT NOT NULL,
    dataset TEXT NOT NULL,
    weighted_grade REAL,
    player_count INTEGER NOT NULL,
    usage_count REAL NOT NULL,
    PRIMARY KEY (season, pff_team_name, position_group, dataset)
);
CREATE INDEX IF NOT EXISTS idx_pff_groups_grade
    ON pff_position_groups(season, dataset, weighted_grade DESC);

CREATE TABLE IF NOT EXISTS pff_imports (
    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    roster_season INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    source_directory TEXT NOT NULL,
    files INTEGER NOT NULL,
    rows INTEGER NOT NULL,
    linked_players INTEGER NOT NULL,
    candidate_transfers INTEGER NOT NULL,
    unresolved_players INTEGER NOT NULL,
    unresolved_teams_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    succeeded INTEGER NOT NULL,
    details_json TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metric(side: dict[str, Any], name: str) -> float | None:
    value = side.get(name)
    return float(value) if value is not None else None


def _numeric(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _hex_color(value: str | None) -> str | None:
    """Normalize a CFBD color to a single-hash CSS hex value.

    CFBD returns colors already prefixed with "#". Templates that prepended
    another one produced "##0c2340", which browsers discard, so team accent
    colors silently never applied.
    """
    if not value:
        return None
    candidate = str(value).strip().lstrip("#")
    return f"#{candidate}" if re.fullmatch(r"[0-9A-Fa-f]{3,8}", candidate) else None


def conference_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


class CFBRepository:
    def __init__(self, database_path: str | Path) -> None:
        self._brands: dict[int, dict[str, Any]] | None = None
        self._brands_by_school: dict[str, dict[str, Any]] | None = None
        self.path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)
            player_primary_key = [
                row["name"] for row in sorted(
                    connection.execute("PRAGMA table_info(players)").fetchall(),
                    key=lambda row: row["pk"] or 99,
                ) if row["pk"]
            ]
            if player_primary_key == ["season", "player_id"]:
                connection.executescript(
                    """
                    ALTER TABLE players RENAME TO players_single_team_legacy;
                    CREATE TABLE players (
                     season INTEGER NOT NULL, player_id TEXT NOT NULL, first_name TEXT NOT NULL,
                     last_name TEXT NOT NULL, normalized_name TEXT NOT NULL, team TEXT NOT NULL,
                     position TEXT, jersey INTEGER, height REAL, weight INTEGER, class_year INTEGER,
                     PRIMARY KEY(season,player_id,team));
                    INSERT OR IGNORE INTO players SELECT * FROM players_single_team_legacy;
                    DROP TABLE players_single_team_legacy;
                    CREATE INDEX IF NOT EXISTS idx_players_name ON players(season,normalized_name);
                    CREATE INDEX IF NOT EXISTS idx_players_team ON players(season,team);
                    """
                )
            record_columns={row["name"] for row in connection.execute("PRAGMA table_info(team_records)")}
            for name in ("conference_games","conference_wins","conference_losses","conference_ties"):
                if name not in record_columns:
                    connection.execute(f"ALTER TABLE team_records ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
            primary_key = [
                row["name"]
                for row in sorted(
                    connection.execute("PRAGMA table_info(rankings)").fetchall(),
                    key=lambda row: row["pk"] or 99,
                )
                if row["pk"]
            ]
            if primary_key and primary_key[-1] == "rank":
                # CFBD polls may contain tied teams with the same numeric rank.
                connection.executescript(
                    """
                    DROP INDEX IF EXISTS idx_rankings_team;
                    ALTER TABLE rankings RENAME TO rankings_rank_key_legacy;
                    CREATE TABLE rankings (
                        season INTEGER NOT NULL, season_type TEXT NOT NULL,
                        week INTEGER NOT NULL, poll TEXT NOT NULL,
                        is_final INTEGER NOT NULL, rank INTEGER NOT NULL,
                        team_id INTEGER, school TEXT NOT NULL, conference TEXT,
                        first_place_votes INTEGER, points INTEGER,
                        PRIMARY KEY (season, season_type, week, poll, school)
                    );
                    INSERT OR REPLACE INTO rankings
                        SELECT * FROM rankings_rank_key_legacy;
                    DROP TABLE rankings_rank_key_legacy;
                    CREATE INDEX idx_rankings_team ON rankings(season, school, week);
                    """
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace_teams(self, teams: Iterable[Team]) -> int:
        items = tuple(teams)
        with self.transaction() as connection:
            for team in items:
                connection.execute(
                    """
                    INSERT INTO teams (
                        team_id, school, mascot, abbreviation, conference, division,
                        classification, color, alternate_color, logos_json, venue_id,
                        venue_name, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(team_id) DO UPDATE SET
                        school=excluded.school, mascot=excluded.mascot,
                        abbreviation=excluded.abbreviation, conference=excluded.conference,
                        division=excluded.division, classification=excluded.classification,
                        color=excluded.color, alternate_color=excluded.alternate_color,
                        logos_json=excluded.logos_json, venue_id=excluded.venue_id,
                        venue_name=excluded.venue_name, updated_at=excluded.updated_at
                    """,
                    (
                        team.team_id, team.school, team.mascot, team.abbreviation,
                        team.conference, team.division, team.classification, team.color,
                        team.alternate_color, json.dumps(team.logos), team.venue_id,
                        team.venue_name, _now_iso(),
                    ),
                )
                connection.execute("DELETE FROM team_aliases WHERE team_id = ?", (team.team_id,))
                for alias in team.aliases:
                    normalized = normalize_alias(alias)
                    if normalized:
                        connection.execute(
                            "INSERT OR IGNORE INTO team_aliases (team_id, alias, normalized_alias) VALUES (?, ?, ?)",
                            (team.team_id, alias, normalized),
                        )
        return len(items)

    def replace_games(self, season: int, games: Iterable[Game]) -> int:
        items = tuple(games)
        with self.transaction() as connection:
            connection.execute("DELETE FROM games WHERE season = ?", (season,))
            connection.executemany(
                """
                INSERT INTO games VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    (
                        game.game_id, game.season, game.week, game.season_type,
                        game.start_date.isoformat(), int(game.start_time_tbd),
                        int(game.completed), int(game.neutral_site), int(game.conference_game),
                        game.venue_id, game.venue, game.home_team_id, game.home_team,
                        game.home_conference, game.home_points, game.home_pregame_elo,
                        game.away_team_id, game.away_team, game.away_conference,
                        game.away_points, game.away_pregame_elo, game.excitement_index,
                        game.notes, _now_iso(),
                    )
                    for game in items
                ],
            )
        return len(items)

    def replace_players(self, season: int, players: Iterable[Any]) -> int:
        items=tuple(players)
        with self.transaction() as connection:
            connection.execute("DELETE FROM players WHERE season=?",(season,))
            connection.executemany("INSERT INTO players VALUES(?,?,?,?,?,?,?,?,?,?,?)",[
                (season,p.player_id,p.first_name,p.last_name,normalize_alias(p.name),p.team,p.position,
                 p.jersey,p.height,p.weight,p.class_year) for p in items])
        return len(items)

    def update_game_media(self, media: Iterable[dict[str, Any]]) -> int:
        outlets: dict[int, list[str]] = {}
        for item in media:
            game_id = int(item["id"])
            outlet = str(item.get("outlet") or "").strip()
            if outlet and outlet not in outlets.setdefault(game_id, []):
                outlets[game_id].append(outlet)
        with self.transaction() as connection:
            for game_id, names in outlets.items():
                connection.execute(
                    "UPDATE games SET television = ? WHERE game_id = ?",
                    (", ".join(names), game_id),
                )
        return len(outlets)

    def replace_records(self, season: int, records: Iterable[dict[str, Any]]) -> int:
        items = tuple(records)
        with self.transaction() as connection:
            connection.execute("DELETE FROM team_records WHERE season = ?", (season,))
            for item in items:
                total = item.get("total") or {}
                conference = item.get("conferenceGames") or {}
                connection.execute(
                    """
                    INSERT INTO team_records(season,team_id,team,conference,division,games,wins,
                    losses,ties,expected_wins,conference_games,conference_wins,
                    conference_losses,conference_ties) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        season, int(item["teamId"]), item["team"], item.get("conference"),
                        item.get("division"), int(total.get("games") or 0),
                        int(total.get("wins") or 0), int(total.get("losses") or 0),
                        int(total.get("ties") or 0), item.get("expectedWins"),
                        int(conference.get("games") or 0),int(conference.get("wins") or 0),
                        int(conference.get("losses") or 0),int(conference.get("ties") or 0),
                    ),
                )
        return len(items)

    def replace_player_stats(self, season: int, stats: Iterable[dict[str, Any]],
                             conference: str | None = None) -> int:
        items=tuple(stats)
        with self.transaction() as connection:
            if conference:
                connection.execute("DELETE FROM player_season_stats WHERE season=? AND conference=?",
                                   (season,conference))
            else:
                connection.execute("DELETE FROM player_season_stats WHERE season=?",(season,))
            connection.executemany(
                """INSERT OR REPLACE INTO player_season_stats VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [(season,str(item["playerId"]),str(item["player"]),str(item["team"]),
                  conference or item.get("conference"),item.get("position"),str(item["category"]),
                  str(item["statType"]),str(item.get("stat") or ""),
                  _numeric(item.get("stat"))) for item in items],
            )
        return len(items)

    def replace_transfers(self, season: int, transfers: Iterable[dict[str, Any]]) -> int:
        items = tuple(transfers); deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
        for item in items:
            name = normalize_alias(f"{item.get('firstName') or ''} {item.get('lastName') or ''}")
            origin = str(item.get("origin") or "")
            if not name or not origin:
                continue
            current = deduplicated.get((name, origin))
            if current is None or (
                bool(item.get("destination")), str(item.get("transferDate") or "")
            ) > (
                bool(current.get("destination")), str(current.get("transferDate") or "")
            ):
                deduplicated[(name, origin)] = item
        with self.transaction() as connection:
            connection.execute("DELETE FROM player_transfers WHERE season=?", (season,))
            connection.executemany(
                """INSERT INTO player_transfers VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        season,
                        normalize_alias(f"{item.get('firstName') or ''} {item.get('lastName') or ''}"),
                        str(item.get("firstName") or ""), str(item.get("lastName") or ""),
                        item.get("position"), str(item.get("origin") or ""),
                        item.get("destination"), item.get("transferDate"),
                        _numeric(item.get("rating")), item.get("stars"), item.get("eligibility"),
                    )
                    for item in deduplicated.values()
                    if item.get("origin") and (item.get("firstName") or item.get("lastName"))
                ],
            )
        return len(deduplicated)

    def replace_draft_picks(self, draft_year: int, picks: Iterable[dict[str, Any]]) -> int:
        items = tuple(picks)
        with self.transaction() as connection:
            connection.execute("DELETE FROM draft_picks WHERE draft_year=?", (draft_year,))
            connection.executemany(
                """INSERT INTO draft_picks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        draft_year, int(item["overall"]), int(item["round"]), int(item["pick"]),
                        str(item["collegeAthleteId"]) if item.get("collegeAthleteId") is not None else None,
                        item.get("collegeId"), str(item.get("collegeTeam") or ""),
                        item.get("collegeConference"), item.get("nflTeamId"),
                        str(item.get("nflTeam") or ""), str(item.get("name") or ""),
                        normalize_alias(str(item.get("name") or "")), item.get("position"),
                        item.get("preDraftRanking"), item.get("preDraftPositionRanking"),
                        item.get("preDraftGrade"),
                    )
                    for item in items
                    if item.get("overall") is not None
                ],
            )
        return len(items)

    def replace_returning_production(self, season: int, rows: Iterable[dict[str, Any]]) -> int:
        items = tuple(rows)
        with self.transaction() as connection:
            connection.execute("DELETE FROM returning_production WHERE season=?", (season,))
            connection.executemany(
                """INSERT INTO returning_production VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        season, item["team"], item.get("conference"),
                        _numeric(item.get("totalPPA")), _numeric(item.get("totalPassingPPA")),
                        _numeric(item.get("totalReceivingPPA")), _numeric(item.get("totalRushingPPA")),
                        _numeric(item.get("percentPPA")), _numeric(item.get("percentPassingPPA")),
                        _numeric(item.get("percentReceivingPPA")), _numeric(item.get("percentRushingPPA")),
                        _numeric(item.get("usage")), _numeric(item.get("passingUsage")),
                        _numeric(item.get("receivingUsage")), _numeric(item.get("rushingUsage")),
                    )
                    for item in items
                    if item.get("team")
                ],
            )
        return len(items)

    def replace_rankings(self, season: int, rankings: Iterable[PollRanking]) -> int:
        items = tuple(rankings)
        with self.transaction() as connection:
            connection.execute("DELETE FROM rankings WHERE season = ?", (season,))
            connection.executemany(
                "INSERT INTO rankings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item.season, item.season_type, item.week, item.poll,
                        int(item.is_final), item.rank, item.team_id, item.school,
                        item.conference, item.first_place_votes, item.points,
                    )
                    for item in items
                ],
            )
        return len(items)

    def replace_team_stats(self, season: int, stats: Iterable[dict[str, Any]]) -> int:
        items = tuple(stats)
        with self.transaction() as connection:
            connection.execute("DELETE FROM team_stats WHERE season = ?", (season,))
            connection.executemany(
                "INSERT INTO team_stats VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        season, item["team"], item.get("conference"), item["statName"],
                        str(item.get("statValue")) if item.get("statValue") is not None else None,
                    )
                    for item in items
                ],
            )
        return len(items)

    def replace_advanced_stats(self, season: int, stats: Iterable[dict[str, Any]]) -> int:
        items = tuple(stats)
        with self.transaction() as connection:
            connection.execute("DELETE FROM team_advanced_stats WHERE season = ?", (season,))
            for item in items:
                offense = item.get("offense") or {}
                defense = item.get("defense") or {}
                connection.execute(
                    "INSERT INTO team_advanced_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        season, item["team"], item.get("conference"),
                        _metric(offense, "successRate"), _metric(defense, "successRate"),
                        _metric(offense, "explosiveness"), _metric(defense, "explosiveness"),
                        _metric(offense, "ppa"), _metric(defense, "ppa"),
                        _metric(offense, "pointsPerOpportunity"),
                        _metric(defense, "pointsPerOpportunity"),
                        _metric(offense.get("havoc") or {}, "total"),
                        _metric(defense.get("havoc") or {}, "total"),
                        json.dumps(offense, separators=(",", ":")),
                        json.dumps(defense, separators=(",", ":")),
                    ),
                )
        return len(items)

    def replace_core_ratings(self, season: int, ratings: Iterable[dict[str, Any]]) -> int:
        items = tuple(ratings)
        with self.transaction() as connection:
            connection.execute("DELETE FROM core_ratings WHERE season = ?", (season,))
            connection.executemany(
                "INSERT INTO core_ratings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        season, item["throughSeasonType"], int(item["throughWeek"]),
                        item["team"], item.get("conference"), float(item["overall"]),
                        float(item["offense"]), float(item["defense"]),
                        int(item["offensePlays"]), int(item["defensePlays"]),
                        item["modelVersion"],
                    )
                    for item in items
                ],
            )
        return len(items)

    def record_sync(self, report: Any) -> None:
        details = [
            {"dataset": item.dataset, "count": item.count, "status": item.status, "message": item.message}
            for item in report.datasets
        ]
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO sync_runs (season, started_at, finished_at, succeeded, details_json) VALUES (?, ?, ?, ?, ?)",
                (
                    report.season, report.started_at.isoformat(), report.finished_at.isoformat(),
                    int(report.succeeded), json.dumps(details, separators=(",", ":")),
                ),
            )

    def resolve_team_alias(self, value: str) -> list[dict[str, Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT t.*, a.alias FROM team_aliases a
                JOIN teams t ON t.team_id = a.team_id
                WHERE a.normalized_alias = ? ORDER BY t.school
                """,
                (normalize_alias(value),),
            ).fetchall()
        return [dict(row) for row in rows]

    def status(self, season: int) -> dict[str, Any]:
        self.initialize()
        with closing(self._connect()) as connection:
            counts = {
                "teams": connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
                "games": connection.execute("SELECT COUNT(*) FROM games WHERE season = ?", (season,)).fetchone()[0],
                "players": connection.execute("SELECT COUNT(*) FROM players WHERE season = ?", (season,)).fetchone()[0],
                "rankings": connection.execute("SELECT COUNT(*) FROM rankings WHERE season = ?", (season,)).fetchone()[0],
                "team_stats": connection.execute("SELECT COUNT(*) FROM team_stats WHERE season = ?", (season,)).fetchone()[0],
                "player_stats": connection.execute("SELECT COUNT(*) FROM player_season_stats WHERE season = ?", (season,)).fetchone()[0],
                "transfers": connection.execute("SELECT COUNT(*) FROM player_transfers WHERE season = ?", (season,)).fetchone()[0],
                "draft_picks": connection.execute("SELECT COUNT(*) FROM draft_picks WHERE draft_year = ?", (season,)).fetchone()[0],
                "returning_production": connection.execute("SELECT COUNT(*) FROM returning_production WHERE season = ?", (season,)).fetchone()[0],
                "advanced_stats": connection.execute("SELECT COUNT(*) FROM team_advanced_stats WHERE season = ?", (season,)).fetchone()[0],
                "core_ratings": connection.execute("SELECT COUNT(*) FROM core_ratings WHERE season = ?", (season,)).fetchone()[0],
                "pff_players": connection.execute("SELECT COUNT(*) FROM pff_players WHERE season = ?", (season,)).fetchone()[0],
                "pff_metrics": connection.execute("SELECT COUNT(*) FROM pff_player_metrics WHERE season = ?", (season,)).fetchone()[0],
            }
            last_sync = connection.execute(
                "SELECT * FROM sync_runs WHERE season = ? ORDER BY sync_id DESC LIMIT 1", (season,)
            ).fetchone()
        sync_payload = dict(last_sync) if last_sync else None
        if sync_payload:
            sync_payload["details"] = json.loads(sync_payload.pop("details_json"))
        return {"season": season, "counts": counts, "last_sync": sync_payload}

    def teams(self, conference: str | None = None, limit: int = 150) -> list[dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM teams"
        params: list[Any] = []
        if conference:
            sql += " WHERE conference = ?"
            params.append(conference)
        sql += " ORDER BY school LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, params).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["logos"] = json.loads(item.pop("logos_json"))
            results.append(item)
        return results

    def conferences(self) -> list[dict[str, Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows=connection.execute("""SELECT conference,COUNT(*) team_count FROM teams
              WHERE conference IS NOT NULL GROUP BY conference ORDER BY conference""").fetchall()
        return [{**dict(row),"slug":conference_slug(row["conference"])} for row in rows]

    def conference_by_slug(self, slug: str) -> dict[str, Any] | None:
        return next((item for item in self.conferences() if item["slug"]==slug),None)

    def conference_standings(self, conference: str, season: int) -> list[dict[str, Any]]:
        self.initialize(); rankings=self.latest_rankings(season)["teams"]
        rank_by_team={item["school"]:item["rank"] for item in rankings}
        elo_by_team=self.team_elo(season)
        with closing(self._connect()) as connection:
            rows=connection.execute("""SELECT t.team_id,t.school,t.mascot,t.color,t.logos_json,
              COALESCE(r.games,0) games,COALESCE(r.wins,0) wins,COALESCE(r.losses,0) losses,
              COALESCE(r.ties,0) ties,COALESCE(r.conference_games,0) conference_games,
              COALESCE(r.conference_wins,0) conference_wins,
              COALESCE(r.conference_losses,0) conference_losses,
              COALESCE(r.conference_ties,0) conference_ties,r.expected_wins
              FROM teams t LEFT JOIN team_records r ON r.team_id=t.team_id AND r.season=?
              WHERE t.conference=?""",(season,conference)).fetchall()
        result=[]
        for row in rows:
            item=dict(row); item["rank"]=rank_by_team.get(item["school"])
            elo=elo_by_team.get(item["team_id"]) or {}
            item["elo"]=elo.get("elo"); item["elo_rank"]=elo.get("elo_rank")
            item["elo_week"]=elo.get("week")
            item["logos"]=json.loads(item.pop("logos_json")); result.append(item)
        result.sort(key=lambda item:(-item["conference_wins"],item["conference_losses"],
                                    -item["wins"],item["losses"],item["rank"] or 999,item["school"]))
        return result

    def team_elo(self, season: int) -> dict[int, dict[str, Any]]:
        """Current Elo per team, derived from CFBD per-game pregame ratings.

        CFBD publishes Elo on the game record (`homePregameElo` / `awayPregameElo`),
        not as a team-level table, and only for games near enough to have been
        rated. Each team therefore takes the rating attached to its most recent
        rated game, and the week that rating came from travels with it so the page
        can say how current it is.
        """
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT team_id,elo,week FROM (
                     SELECT home_team_id team_id,home_pregame_elo elo,week,start_date
                       FROM games WHERE season=? AND home_pregame_elo IS NOT NULL
                     UNION ALL
                     SELECT away_team_id team_id,away_pregame_elo elo,week,start_date
                       FROM games WHERE season=? AND away_pregame_elo IS NOT NULL
                   ) ORDER BY start_date""",
                (season, season)).fetchall()
        latest: dict[int, dict[str, Any]] = {}
        for row in rows:
            if row["team_id"] is None:
                continue
            latest[row["team_id"]] = {"elo": row["elo"], "week": row["week"],
                                      "source": "CFBD pregame Elo"}
        if not latest:
            return {}
        ordered = sorted(latest.items(), key=lambda pair: -(pair[1]["elo"] or 0))
        for rank, (team_id, entry) in enumerate(ordered, start=1):
            entry["elo_rank"] = rank
        return latest

    def conference_games(self, conference: str, season: int, limit: int=30) -> list[dict[str,Any]]:
        self.initialize(); now=datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            rows=connection.execute("""SELECT * FROM games WHERE season=? AND completed=0
              AND start_date>=? AND (home_conference=? OR away_conference=?)
              ORDER BY start_date LIMIT ?""",(season,now,conference,conference,limit)).fetchall()
        return [dict(row) for row in rows]

    #: Leader categories, in reading order, ranked by their conventional stat.
    LEADER_CATEGORIES = ("passing", "rushing", "receiving", "defensive",
                         "interceptions", "kicking")

    def _stat_leaders(self, *, season: int, conference: str | None=None,
                      team: str | None=None, limit: int=8) -> dict[str,Any]:
        """Leaders per category, each carrying the full stat line.

        Ranking by a single statistic and displaying only that number hides the
        context that makes it meaningful, so every leader also returns the other
        statistics CFBD published for the same category.
        """
        self.initialize()
        with closing(self._connect()) as connection:
            scope_column="conference" if conference else "team"; scope_value=conference or team
            available=connection.execute(
                f"SELECT MAX(season) FROM player_season_stats WHERE season<=? AND {scope_column}=?",
                (season,scope_value)).fetchone()[0]
            if available is None: return {"season":None,"groups":{}}
            groups={}
            for category in self.LEADER_CATEGORIES:
                stat_type=sort_stat(category)
                threshold=qualifier(category)
                if threshold is None:
                    rows=connection.execute(f"""SELECT player_id,player,position,team,stat_value,numeric_value
                      FROM player_season_stats WHERE season=? AND {scope_column}=? AND category=? AND stat_type=?
                      AND numeric_value IS NOT NULL AND numeric_value>0
                      ORDER BY numeric_value DESC,player LIMIT ?""",
                      (available,scope_value,category,stat_type,limit)).fetchall()
                else:
                    # Rank on the headline statistic, but only among players who
                    # cleared the usage minimum for the category.
                    qualifying_stat,minimum=threshold
                    rows=connection.execute(f"""SELECT s.player_id,s.player,s.position,s.team,
                      s.stat_value,s.numeric_value
                      FROM player_season_stats s JOIN player_season_stats q
                        ON q.season=s.season AND q.player_id=s.player_id AND q.category=s.category
                        AND q.stat_type=? AND q.numeric_value>=?
                      WHERE s.season=? AND s.{scope_column}=? AND s.category=? AND s.stat_type=?
                      AND s.numeric_value IS NOT NULL
                      ORDER BY s.numeric_value DESC,s.player LIMIT ?""",
                      (qualifying_stat,minimum,available,scope_value,category,stat_type,limit)).fetchall()
                if not rows: continue
                identifiers=[row["player_id"] for row in rows]
                placeholders=",".join("?" for _ in identifiers)
                detail=connection.execute(f"""SELECT player_id,stat_type,stat_value,numeric_value
                  FROM player_season_stats WHERE season=? AND {scope_column}=? AND category=?
                  AND player_id IN ({placeholders})""",
                  (available,scope_value,category,*identifiers)).fetchall()
                line: dict[str, dict[str, Any]] = {}
                for item in detail:
                    value=item["numeric_value"]
                    line.setdefault(item["player_id"],{})[item["stat_type"]]=(
                        value if value is not None else item["stat_value"])
                groups[category]={
                    "label":category_label(category),
                    "stat_type":stat_type,
                    "qualifier":(f"min {threshold[1]:g} {threshold[0]}" if threshold else None),
                    "players":[{**dict(row),"stats":line.get(row["player_id"],{})} for row in rows],
                }
        return {"season":available,"groups":groups}

    def conference_player_leaders(self, conference: str, season: int, limit: int=8) -> dict[str,Any]:
        return self._stat_leaders(season=season,conference=conference,limit=limit)

    def team_player_leaders(self, team: str, season: int, limit: int=8) -> dict[str,Any]:
        return self._stat_leaders(season=season,team=team,limit=limit)

    def get_team(self, team_id: int) -> dict[str,Any] | None:
        self.initialize()
        with closing(self._connect()) as connection:
            row=connection.execute("SELECT * FROM teams WHERE team_id=?",(team_id,)).fetchone()
        if row is None:return None
        item=dict(row); item["logos"]=json.loads(item.pop("logos_json")); return item

    def team_brands(self) -> dict[int, dict[str, Any]]:
        """Team identity for display: school, conference, colors, and primary logo.

        Cached for the life of the repository because team branding changes once
        a season at most, while the pages that need it render it hundreds of
        times per request.
        """
        if self._brands is None:
            self.initialize()
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT team_id,school,abbreviation,mascot,conference,color,"
                    "alternate_color,logos_json FROM teams").fetchall()
            brands: dict[int, dict[str, Any]] = {}
            for row in rows:
                logos = json.loads(row["logos_json"] or "[]")
                brands[row["team_id"]] = {
                    "team_id": row["team_id"], "school": row["school"],
                    "abbreviation": row["abbreviation"], "mascot": row["mascot"],
                    "conference": row["conference"],
                    "color": _hex_color(row["color"]),
                    "alternate_color": _hex_color(row["alternate_color"]),
                    "logo": logos[0] if logos else None,
                }
            self._brands = brands
        return self._brands

    def brand_for(self, team_id: int | None) -> dict[str, Any]:
        """Branding for one team, or empty values when the team is unknown."""
        if team_id is None:
            return {}
        return self.team_brands().get(team_id, {})

    def brand_by_school(self, school: str | None) -> dict[str, Any]:
        """Branding looked up by school name, for packets keyed by name."""
        if not school:
            return {}
        if self._brands_by_school is None:
            self._brands_by_school = {brand["school"]: brand
                                      for brand in self.team_brands().values()}
        return self._brands_by_school.get(school, {})

    def team_schedule(self, team_id: int, season: int) -> list[dict[str,Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows=connection.execute("""SELECT * FROM games WHERE season=?
              AND (home_team_id=? OR away_team_id=?) ORDER BY start_date""",
              (season,team_id,team_id)).fetchall()
        return [dict(row) for row in rows]

    def team_roster(self, team: str, season: int) -> list[dict[str,Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows=connection.execute("""SELECT * FROM players WHERE season=? AND team=?
              ORDER BY CASE position WHEN 'QB' THEN 1 WHEN 'RB' THEN 2 WHEN 'WR' THEN 3
              WHEN 'TE' THEN 4 WHEN 'OL' THEN 5 WHEN 'DL' THEN 6 WHEN 'LB' THEN 7
              WHEN 'DB' THEN 8 ELSE 9 END,position,jersey,last_name""",(season,team)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _position_group(position: str | None) -> tuple[str, str, int]:
        value = (position or "OTHER").upper()
        groups = {
            "QB": ("Offense", "Quarterback", 1),
            "RB": ("Offense", "Backfield", 2), "FB": ("Offense", "Backfield", 2),
            "HB": ("Offense", "Backfield", 2),
            "WR": ("Offense", "Wide receiver", 3), "TE": ("Offense", "Tight end", 4),
            "OL": ("Offense", "Offensive line", 5), "OT": ("Offense", "Offensive line", 5),
            "G": ("Offense", "Offensive line", 5), "C": ("Offense", "Offensive line", 5),
            "DL": ("Defense", "Interior defensive line", 1), "DT": ("Defense", "Interior defensive line", 1),
            "NT": ("Defense", "Interior defensive line", 1), "DE": ("Defense", "Edge", 2),
            "DI": ("Defense", "Interior defensive line", 1),
            "EDGE": ("Defense", "Edge", 2), "ED": ("Defense", "Edge", 2),
            "LB": ("Defense", "Linebacker", 3),
            "CB": ("Defense", "Cornerback", 4), "S": ("Defense", "Safety", 5),
            "DB": ("Defense", "Defensive back", 6), "PK": ("Special teams", "Kicker", 1),
            "P": ("Special teams", "Punter", 2), "LS": ("Special teams", "Long snapper", 3),
        }
        return groups.get(value, ("Other", value.title(), 99))

    def roster_movements(self, team_id: int, season: int) -> dict[str, Any]:
        team = self.get_team(team_id)
        if team is None:
            return {"season": season, "arrivals": [], "departures": [], "counts": {}}
        self.initialize(); previous_season = season - 1
        with closing(self._connect()) as connection:
            current = [dict(row) for row in connection.execute(
                "SELECT * FROM players WHERE season=? AND team=?", (season, team["school"])
            )]
            previous = [dict(row) for row in connection.execute(
                "SELECT * FROM players WHERE season=? AND team=?", (previous_season, team["school"])
            )]
            transfers = [dict(row) for row in connection.execute(
                """SELECT * FROM player_transfers WHERE season=? AND (origin=? OR destination=?)""",
                (season, team["school"], team["school"]),
            )]
            picks = [dict(row) for row in connection.execute(
                "SELECT * FROM draft_picks WHERE draft_year=? AND college_team=?",
                (season, team["school"]),
            )]
            pff = [dict(row) for row in connection.execute(
                """SELECT normalized_name,interest_score FROM pff_players
                   WHERE season=? AND cfbd_team_id=?""", (previous_season, team_id)
            )]
        current_names = {normalize_alias(f"{row['first_name']} {row['last_name']}") for row in current}
        current_ids = {row["player_id"] for row in current}
        previous_names = {normalize_alias(f"{row['first_name']} {row['last_name']}") for row in previous}
        previous_ids = {row["player_id"] for row in previous}
        transfer_in = {row["normalized_name"]: row for row in transfers if row["destination"] == team["school"]}
        transfer_out = {row["normalized_name"]: row for row in transfers if row["origin"] == team["school"]}
        draft_by_name = {row["normalized_name"]: row for row in picks}
        draft_by_id = {row["college_athlete_id"]: row for row in picks if row["college_athlete_id"]}
        interest = {row["normalized_name"]: row["interest_score"] for row in pff}
        arrivals = []
        for row in current:
            name = normalize_alias(f"{row['first_name']} {row['last_name']}")
            if row["player_id"] in previous_ids or name in previous_names:
                continue
            portal = transfer_in.get(name)
            arrivals.append({
                **row, "name": f"{row['first_name']} {row['last_name']}",
                "movement_type": "TRANSFER_IN" if portal else "NEWCOMER",
                "origin": portal["origin"] if portal else None,
                "rating": portal["rating"] if portal else None,
                "evidence": "CFBD transfer portal" if portal else "Roster comparison",
            })
        departures = []
        for row in previous:
            name = normalize_alias(f"{row['first_name']} {row['last_name']}")
            if row["player_id"] in current_ids or name in current_names:
                continue
            drafted = draft_by_id.get(row["player_id"]) or draft_by_name.get(name)
            portal = transfer_out.get(name)
            if drafted:
                movement_type, destination, evidence = "DRAFTED", drafted["nfl_team"], "CFBD NFL Draft"
            elif portal:
                movement_type, destination, evidence = "TRANSFER_OUT", portal["destination"], "CFBD transfer portal"
            elif (row.get("class_year") or 0) >= 4:
                movement_type, destination, evidence = "ELIGIBILITY_DEPARTURE", None, "Inferred from class and roster comparison"
            else:
                movement_type, destination, evidence = "ROSTER_DEPARTURE", None, "Roster comparison; reason unverified"
            departures.append({
                **row, "name": f"{row['first_name']} {row['last_name']}",
                "movement_type": movement_type, "destination": destination,
                "draft_round": drafted["round"] if drafted else None,
                "draft_pick": drafted["overall_pick"] if drafted else None,
                "interest_score": interest.get(name), "evidence": evidence,
            })
        arrivals.sort(key=lambda row: (row["movement_type"] != "TRANSFER_IN", -(row["rating"] or 0), row["name"]))
        departures.sort(key=lambda row: (
            {"DRAFTED": 0, "TRANSFER_OUT": 1, "ELIGIBILITY_DEPARTURE": 2}.get(row["movement_type"], 3),
            -(row["interest_score"] or 0), row["name"],
        ))
        counts: dict[str, int] = {}
        for row in arrivals + departures:
            counts[row["movement_type"]] = counts.get(row["movement_type"], 0) + 1
        return {"season": season, "previous_season": previous_season,
                "arrivals": arrivals, "departures": departures, "counts": counts}

    def team_depth_chart(self, team_id: int, season: int) -> dict[str, Any]:
        team = self.get_team(team_id)
        if team is None:
            return {"season": season, "units": {}, "summary": {}}
        roster = self.team_roster(team["school"], season)
        movements = self.roster_movements(team_id, season)
        arrival_by_id = {row["player_id"]: row for row in movements["arrivals"]}
        self.initialize()
        with closing(self._connect()) as connection:
            previous_ids = {row[0] for row in connection.execute(
                "SELECT player_id FROM players WHERE season=? AND team=?", (season - 1, team["school"])
            )}
            pff_rows = connection.execute(
                """SELECT cfbd_player_id,normalized_name,interest_score FROM pff_players
                   WHERE season=? AND cfbd_team_id=? AND interest_score IS NOT NULL""",
                (season - 1, team_id),
            ).fetchall()
        pff_by_id = {row["cfbd_player_id"]: row["interest_score"] for row in pff_rows if row["cfbd_player_id"]}
        pff_by_name = {row["normalized_name"]: row["interest_score"] for row in pff_rows}
        unit_rows: dict[str, dict[str, list[dict[str, Any]]]] = {}
        returning = upperclassmen = 0
        for row in roster:
            name = normalize_alias(f"{row['first_name']} {row['last_name']}")
            is_returner = row["player_id"] in previous_ids
            returning += int(is_returner); upperclassmen += int((row.get("class_year") or 0) >= 3)
            arrival = arrival_by_id.get(row["player_id"])
            item = {**row, "name": f"{row['first_name']} {row['last_name']}",
                    "is_returner": is_returner,
                    "arrival_type": arrival["movement_type"] if arrival else None,
                    "origin": arrival.get("origin") if arrival else None,
                    "pff_interest": pff_by_id.get(row["player_id"], pff_by_name.get(name))}
            unit, group, order = self._position_group(row.get("position"))
            item["group_order"] = order
            unit_rows.setdefault(unit, {}).setdefault(group, []).append(item)
        for groups in unit_rows.values():
            for players in groups.values():
                players.sort(key=lambda row: (
                    -(row["pff_interest"] or 0), not row["is_returner"],
                    -(row.get("class_year") or 0), row.get("jersey") or 999, row["name"],
                ))
        ordered_units = {
            unit: dict(sorted(groups.items(), key=lambda pair: pair[1][0]["group_order"]))
            for unit, groups in unit_rows.items()
        }
        total = len(roster)
        return {"season": season, "units": ordered_units, "movements": movements,
                "summary": {"players": total, "returners": returning,
                            "continuity_pct": round(100 * returning / total, 1) if total else None,
                            "upperclassmen": upperclassmen,
                            "upperclassmen_pct": round(100 * upperclassmen / total, 1) if total else None,
                            "transfer_arrivals": movements["counts"].get("TRANSFER_IN", 0)}}

    def team_quality_snapshot(self, team_id: int, season: int) -> dict[str, Any]:
        team = self.get_team(team_id)
        if team is None:
            return {"state": "UNAVAILABLE", "cards": []}
        metrics = self.team_metrics(team["school"], season)
        depth = self.team_depth_chart(team_id, season)
        with closing(self._connect()) as connection:
            returning = connection.execute(
                "SELECT * FROM returning_production WHERE season=? AND team=?",
                (season, team["school"]),
            ).fetchone()
            proven = connection.execute(
                """SELECT COUNT(*) FROM pff_players p JOIN players r
                   ON r.season=? AND r.player_id=p.cfbd_player_id
                   WHERE p.season=? AND p.cfbd_team_id=? AND p.interest_score IS NOT NULL""",
                (season, season - 1, team_id),
            ).fetchone()[0]
        if metrics["advanced"] or metrics["core"]:
            return {"state": "LIVE", "season": season, "cards": [], "metrics": metrics}
        row = dict(returning) if returning else {}
        # Formats name the scale explicitly: "rate" is a 0-1 fraction, "pct" is
        # already 0-100. Leaving both as "percent" forced callers to guess, and
        # a 45.9% continuity figure rendered as 4590%.
        cards = [
            {"label": "Returning production", "value": row.get("percent_ppa"), "format": "rate",
             "source": "CFBD returning PPA" if returning else "Awaiting CFBD returning production"},
            {"label": "Roster continuity", "value": depth["summary"].get("continuity_pct"), "format": "pct",
             "source": f"{season - 1} to {season} roster IDs"},
            {"label": "Transfer arrivals", "value": depth["summary"].get("transfer_arrivals"), "format": "int",
             "source": "CFBD transfer portal"},
            {"label": "Proven PFF players", "value": proven, "format": "int",
             "source": f"{season - 1} PFF snapshot on current roster"},
        ]
        return {"state": "PRESEASON", "season": season, "cards": cards,
                "message": "Live team-quality metrics will replace these context signals after games are played."}

    def get_player(self, player_id: str, season: int) -> dict[str, Any] | None:
        self.initialize()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT p.*,t.team_id,t.conference,t.color,t.alternate_color,t.logos_json
                   FROM players p LEFT JOIN teams t ON t.school=p.team
                   WHERE p.player_id=? AND p.season<=? ORDER BY p.season DESC LIMIT 1""",
                (player_id, season),
            ).fetchone()
            if row is None:
                return None
            player = dict(row); player["name"] = f"{player['first_name']} {player['last_name']}"
            player["logos"] = json.loads(player.pop("logos_json") or "[]")
            player["stints"] = [dict(item) for item in connection.execute(
                "SELECT season,team,position,jersey,class_year FROM players WHERE player_id=? ORDER BY season DESC",
                (player_id,),
            )]
            player["stats"] = [dict(item) for item in connection.execute(
                """SELECT * FROM player_season_stats WHERE player_id=?
                   ORDER BY season DESC,category,stat_type""", (player_id,)
            )]
            normalized = normalize_alias(player["name"])
            player["pff"] = [dict(item) for item in connection.execute(
                """SELECT p.*,m.dataset,m.primary_grade,m.usage_count,m.game_count games
                   FROM pff_players p LEFT JOIN pff_player_metrics m
                   ON m.season=p.season AND m.pff_player_id=p.pff_player_id
                   WHERE p.cfbd_player_id=? OR (p.normalized_name=? AND p.cfbd_team=?)
                   ORDER BY p.season DESC,m.primary_grade DESC""",
                (player_id, normalized, player["team"]),
            )]
            player["transfers"] = [dict(item) for item in connection.execute(
                "SELECT * FROM player_transfers WHERE normalized_name=? ORDER BY season DESC",
                (normalized,),
            )]
            player["draft"] = [dict(item) for item in connection.execute(
                """SELECT * FROM draft_picks WHERE college_athlete_id=? OR normalized_name=?
                   ORDER BY draft_year DESC""", (player_id, normalized)
            )]
        return player

    def recent_movements(self, season: int, limit: int = 24) -> list[dict[str, Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            transfers = [dict(row) for row in connection.execute(
                """SELECT 'TRANSFER' event_type,normalized_name,first_name||' '||last_name player_name,
                   position,origin,destination,transfer_date event_date,rating,stars,NULL nfl_team,
                   NULL round,NULL overall_pick FROM player_transfers WHERE season=?
                   ORDER BY transfer_date DESC,rating DESC LIMIT ?""", (season, limit)
            )]
            picks = [dict(row) for row in connection.execute(
                """SELECT 'DRAFTED' event_type,normalized_name,player_name,position,
                   college_team origin,NULL destination,NULL event_date,NULL rating,NULL stars,
                   nfl_team,round,overall_pick FROM draft_picks WHERE draft_year=?
                   ORDER BY overall_pick LIMIT ?""", (season, limit)
            )]
        transfer_slots = (limit + 1) // 2
        return transfers[:transfer_slots] + picks[: max(0, limit - transfer_slots)]

    def team_metrics(self, team: str, season: int) -> dict[str,Any]:
        self.initialize()
        with closing(self._connect()) as connection:
            record=connection.execute("SELECT * FROM team_records WHERE season=? AND team=?",(season,team)).fetchone()
            advanced=connection.execute("SELECT * FROM team_advanced_stats WHERE season=? AND team=?",(season,team)).fetchone()
            core=connection.execute("""SELECT * FROM core_ratings WHERE season=? AND team=?
              ORDER BY through_week DESC LIMIT 1""",(season,team)).fetchone()
            stats=[dict(row) for row in connection.execute("SELECT stat_name,stat_value FROM team_stats WHERE season=? AND team=? ORDER BY stat_name",(season,team))]
        return {"record":dict(record) if record else None,"advanced":dict(advanced) if advanced else None,
                "core":dict(core) if core else None,"stats":stats}

    def pff_team_context(self, team_id: int, season: int=2025, player_limit: int=12) -> dict[str,Any]:
        self.initialize()
        with closing(self._connect()) as connection:
            players=[dict(row) for row in connection.execute("""SELECT p.*,
              GROUP_CONCAT(m.dataset||':'||ROUND(m.primary_grade,1)) grades
              FROM pff_players p LEFT JOIN pff_player_metrics m
              ON m.season=p.season AND m.pff_player_id=p.pff_player_id
              WHERE p.season=? AND p.cfbd_team_id=? AND p.interest_score IS NOT NULL
              GROUP BY p.season,p.pff_player_id ORDER BY p.interest_score DESC LIMIT ?""",
              (season,team_id,player_limit)).fetchall()]
            groups=[dict(row) for row in connection.execute("""SELECT * FROM pff_position_groups
              WHERE season=? AND cfbd_team_id=? AND weighted_grade IS NOT NULL
              AND player_count>=2 ORDER BY weighted_grade DESC""",(season,team_id)).fetchall()]
        movements = self.roster_movements(team_id, season + 1)
        departed = {normalize_alias(row["name"]): row for row in movements["departures"]}
        for player in players:
            movement = departed.get(player["normalized_name"])
            if movement:
                player["roster_status"] = movement["movement_type"]
                player["roster_destination"] = movement["destination"]
                player["player_page_id"] = movement["player_id"]
            elif player.get("cfbd_player_id"):
                player["roster_status"] = "RETURNING"
                player["roster_destination"] = None
                player["player_page_id"] = player["cfbd_player_id"]
            else:
                player["roster_status"] = "UNRESOLVED"
                player["roster_destination"] = None
                player["player_page_id"] = None
        return {"season":season,"players":players,"position_groups":groups}

    def conference_pff_players(self, conference: str, season: int=2025, limit: int=20) -> list[dict[str,Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute("""SELECT p.* FROM pff_players p
              JOIN teams t ON t.team_id=p.cfbd_team_id WHERE p.season=? AND t.conference=?
              AND p.interest_score IS NOT NULL ORDER BY p.interest_score DESC LIMIT ?""",
              (season,conference,limit)).fetchall()]

    def pff_game_units(self, home_team_id: int, away_team_id: int, season: int=2025) -> list[dict[str,Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows=[dict(row) for row in connection.execute("""SELECT * FROM pff_position_groups
              WHERE season=? AND cfbd_team_id IN (?,?) AND weighted_grade IS NOT NULL""",
              (season,home_team_id,away_team_id)).fetchall()]
        by_key={(row["cfbd_team_id"],row["position_group"],row["dataset"]):row for row in rows}
        specs=(("Pass protection","OL","blocking"),("Pass rush","EDGE","pass_rush"),
               ("Interior rush","INTERIOR_DL","pass_rush"),("Coverage","SECONDARY","coverage"),
               ("Rushing","RB","rushing"),("Receiving","WR","receiving"))
        result=[]
        for label,group,dataset in specs:
            home=by_key.get((home_team_id,group,dataset)); away=by_key.get((away_team_id,group,dataset))
            if home or away: result.append({"label":label,"position_group":group,"dataset":dataset,
                "home_grade":home["weighted_grade"] if home else None,
                "away_grade":away["weighted_grade"] if away else None,
                "home_usage":home["usage_count"] if home else None,"away_usage":away["usage_count"] if away else None})
        return result

    def pff_matchups(self, home_team_id: int, away_team_id: int,
                     season: int = 2025) -> list[dict[str, Any]]:
        """Build directional offense-vs-defense comparisons from licensed PFF rows."""
        self.initialize()
        with closing(self._connect()) as connection:
            rows = [dict(row) for row in connection.execute(
                """SELECT p.cfbd_team_id,p.position,m.dataset,m.primary_grade,
                   m.usage_count,m.metrics_json FROM pff_players p
                   JOIN pff_player_metrics m ON m.season=p.season
                   AND m.pff_player_id=p.pff_player_id
                   WHERE p.season=? AND p.cfbd_team_id IN (?,?)""",
                (season, home_team_id, away_team_id),
            )]

        def grade(team_id: int, dataset: str, groups: set[str],
                  raw_field: str | None = None) -> dict[str, Any] | None:
            values: list[tuple[float, float]] = []
            for row in rows:
                if row["cfbd_team_id"] != team_id or row["dataset"] != dataset:
                    continue
                _, group, _ = self._position_group(row["position"])
                if group not in groups:
                    continue
                value = row["primary_grade"]
                if raw_field:
                    try:
                        value = _numeric(json.loads(row["metrics_json"] or "{}").get(raw_field))
                    except (TypeError, json.JSONDecodeError):
                        value = None
                if value is not None:
                    values.append((float(value), float(row["usage_count"] or 1)))
            if not values:
                return None
            usage = sum(weight for _, weight in values)
            weighted = sum(value * weight for value, weight in values) / usage
            return {"grade": round(weighted, 1), "players": len(values), "usage": round(usage, 1)}

        specs = (
            ("Rushing", "Rush offense", "rushing", {"Backfield"}, None,
             "Run defense", "defense", {"Interior defensive line", "Edge", "Linebacker"}, "grades_run_defense"),
            ("Run blocking", "Run-block grade", "blocking", {"Offensive line", "Tight end"}, "grades_run_block",
             "Run defense", "defense", {"Interior defensive line", "Edge", "Linebacker"}, "grades_run_defense"),
            ("Pass protection", "Pass-block grade", "blocking", {"Offensive line"}, "grades_pass_block",
             "Pass rush", "pass_rush", {"Interior defensive line", "Edge", "Linebacker"}, None),
            ("Passing", "Pass grade", "passing", {"Quarterback"}, None,
             "Coverage", "coverage", {"Defensive back", "Cornerback", "Safety", "Linebacker"}, None),
            ("Receiving", "Receiving grade", "receiving", {"Wide receiver", "Tight end", "Backfield"}, None,
             "Coverage", "coverage", {"Defensive back", "Cornerback", "Safety", "Linebacker"}, None),
        )

        def direction(attack_team: int, defend_team: int, spec: tuple) -> dict[str, Any]:
            attack = grade(attack_team, spec[2], spec[3], spec[4])
            defense = grade(defend_team, spec[6], spec[7], spec[8])
            edge = None
            if attack and defense:
                difference = round(attack["grade"] - defense["grade"], 1)
                if abs(difference) < 2.5:
                    edge = {"side": "EVEN", "margin": abs(difference)}
                else:
                    edge = {"side": "OFFENSE" if difference > 0 else "DEFENSE",
                            "margin": abs(difference)}
            return {"attack_label": spec[1], "attack": attack,
                    "counter_label": spec[5], "counter": defense, "edge": edge}

        result = []
        for spec in specs:
            away_direction = direction(away_team_id, home_team_id, spec)
            home_direction = direction(home_team_id, away_team_id, spec)
            if away_direction["attack"] or away_direction["counter"] or home_direction["attack"] or home_direction["counter"]:
                result.append({"label": spec[0], "away_attacks": away_direction,
                               "home_attacks": home_direction})
        return result

    def latest_rankings(self, season: int) -> dict[str, Any]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM rankings WHERE season = ? ORDER BY week DESC, poll, rank",
                (season,),
            ).fetchall()
        if not rows:
            return {"week": None, "poll": None, "teams": []}
        latest_week = max(row["week"] for row in rows)
        latest = [row for row in rows if row["week"] == latest_week]
        polls = {row["poll"] for row in latest}

        def poll_priority(name: str) -> tuple[int, str]:
            lower = name.casefold()
            if "playoff" in lower or "cfp" in lower or "committee" in lower:
                return (0, name)
            if lower.startswith("ap") or "associated press" in lower:
                return (1, name)
            if "coach" in lower:
                return (2, name)
            return (3, name)

        selected = sorted(polls, key=poll_priority)[0]
        teams = [dict(row) for row in latest if row["poll"] == selected]
        return {"week": latest_week, "poll": selected, "teams": teams}

    def upcoming_games(self, season: int, limit: int = 16) -> list[dict[str, Any]]:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM games
                WHERE season = ? AND completed = 0 AND start_date >= ?
                ORDER BY start_date LIMIT ?
                """,
                (season, now, limit),
            ).fetchall()
        rankings = self.latest_rankings(season)["teams"]
        rank_by_school = {row["school"]: row["rank"] for row in rankings}
        results = []
        for row in rows:
            item = dict(row)
            item["home_rank"] = rank_by_school.get(item["home_team"])
            item["away_rank"] = rank_by_school.get(item["away_team"])
            results.append(item)
        return results

    def get_game(self, game_id: int) -> dict[str, Any] | None:
        self.initialize()
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()
            if row is None:
                return None
            game = dict(row)
            season = game["season"]
            record_rows = connection.execute(
                "SELECT * FROM team_records WHERE season = ? AND team_id IN (?, ?)",
                (season, game["home_team_id"], game["away_team_id"]),
            ).fetchall()
            metric_rows = connection.execute(
                "SELECT * FROM team_advanced_stats WHERE season = ? AND team IN (?, ?)",
                (season, game["home_team"], game["away_team"]),
            ).fetchall()
        game["records"] = {row["team"]: dict(row) for row in record_rows}
        game["advanced_metrics"] = {row["team"]: dict(row) for row in metric_rows}
        rankings = self.latest_rankings(game["season"])["teams"]
        game["rankings"] = {row["school"]: row["rank"] for row in rankings}
        return game
