"""Evidence-based postgame story and decisive-factor analysis.

The writer is deliberately deterministic.  It does not invent a football story
from a final score; it ranks measurable factors from the cached game/team/player
box score and tells the reader how much of the usual postgame checklist is
actually supported by stored data.

This is the first layer of a broader postgame system.  Drive/play data, frozen
pregame expectations and game-level PFF/snap exports can be added later without
changing the report contract: they become additional evidence/factors rather
than a second competing narrative engine.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Any, Iterable

from sports_aggregator.cfb.depth_chart_observed import observed_depth_roles


@dataclass(frozen=True, slots=True)
class Factor:
    key: str
    label: str
    winner: str | None
    loser: str | None
    score: float
    headline: str
    detail: str
    confidence: str = "high"
    source: str = "CFBD team box score"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "winner": self.winner,
            "loser": self.loser, "score": round(self.score, 2),
            "headline": self.headline, "detail": self.detail,
            "confidence": self.confidence, "source": self.source,
        }


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _ratio(value: Any) -> tuple[float, float] | None:
    if value in (None, ""):
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*[/\-]\s*(\d+(?:\.\d+)?)", str(value))
    if not match:
        return None
    made, attempts = float(match.group(1)), float(match.group(2))
    return made, attempts


def _clock_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"(\d+)\s*:\s*(\d+)", str(value))
    if not match:
        return None
    return 60 * int(match.group(1)) + int(match.group(2))


def _team_stats(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in rows:
        team = str(row.get("team") or "").strip()
        category = str(row.get("category") or row.get("stat_type") or "").strip()
        if not team or not category:
            continue
        value = row.get("numeric_value")
        if value is None:
            value = row.get("stat_value")
        result[team][category] = value
        # Some rows carry points directly rather than as a category value.
        if row.get("points") is not None:
            result[team]["points"] = row.get("points")
    return dict(result)


def _get(stats: dict[str, Any], *keys: str) -> Any:
    folded = {str(key).casefold(): value for key, value in stats.items()}
    for key in keys:
        if key in stats:
            return stats[key]
        if key.casefold() in folded:
            return folded[key.casefold()]
    return None


def _side(game: dict[str, Any]) -> tuple[str, str, float, float]:
    away, home = str(game.get("away_team")), str(game.get("home_team"))
    away_points = _num(game.get("away_points")) or 0.0
    home_points = _num(game.get("home_points")) or 0.0
    if home_points >= away_points:
        return home, away, home_points, away_points
    return away, home, away_points, home_points


def _higher_factor(key: str, label: str, a_team: str, b_team: str,
                   a_value: float | None, b_value: float | None, *,
                   meaningful: float, scale: float, unit: str = "",
                   detail_label: str | None = None) -> Factor | None:
    if a_value is None or b_value is None:
        return None
    delta = a_value - b_value
    if abs(delta) < meaningful:
        return None
    winner, loser = (a_team, b_team) if delta > 0 else (b_team, a_team)
    high, low = (a_value, b_value) if delta > 0 else (b_value, a_value)
    score = min(100.0, 35.0 + 65.0 * min(abs(delta) / max(scale, 0.001), 1.0))
    suffix = unit
    detail_name = detail_label or label.lower()
    return Factor(
        key, label, winner, loser, score,
        f"{winner} controlled {detail_name}",
        f"{winner} finished at {high:g}{suffix} versus {low:g}{suffix} for {loser}.",
    )


def _lower_factor(key: str, label: str, a_team: str, b_team: str,
                  a_value: float | None, b_value: float | None, *,
                  meaningful: float, scale: float, unit: str = "",
                  noun: str | None = None) -> Factor | None:
    factor = _higher_factor(key, label, a_team, b_team, a_value, b_value,
                            meaningful=meaningful, scale=scale, unit=unit,
                            detail_label=noun or label.lower())
    if factor is None:
        return None
    # Invert who benefited because lower is better.
    return Factor(
        factor.key, factor.label, factor.loser, factor.winner, factor.score,
        f"{factor.loser} won the {noun or label.lower()} battle",
        factor.detail.replace(factor.winner, "__HIGH__", 1)
                     .replace(factor.loser, factor.winner, 1)
                     .replace("__HIGH__", factor.loser, 1),
        factor.confidence, factor.source,
    )


def _conversion_factor(key: str, label: str, a_team: str, b_team: str,
                       a: Any, b: Any, *, meaningful: float = .12) -> Factor | None:
    ar, br = _ratio(a), _ratio(b)
    if not ar or not br or ar[1] <= 0 or br[1] <= 0:
        return None
    ap, bp = ar[0] / ar[1], br[0] / br[1]
    if abs(ap - bp) < meaningful:
        return None
    winner, loser = (a_team, b_team) if ap > bp else (b_team, a_team)
    wr, lr = (ar, br) if ap > bp else (br, ar)
    score = min(100.0, 40.0 + abs(ap - bp) * 120)
    return Factor(
        key, label, winner, loser, score,
        f"{winner} won on {label.lower()}",
        f"{winner} converted {int(wr[0])}/{int(wr[1])} ({100*max(ap,bp):.0f}%) while "
        f"{loser} went {int(lr[0])}/{int(lr[1])} ({100*min(ap,bp):.0f}%).",
    )


def decisive_factors(game: dict[str, Any], team_rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats = _team_stats(team_rows)
    away, home = str(game.get("away_team")), str(game.get("home_team"))
    a, h = stats.get(away, {}), stats.get(home, {})
    factors: list[Factor] = []
    considered: list[str] = []

    def add(name: str, factor: Factor | None) -> None:
        considered.append(name)
        if factor is not None:
            factors.append(factor)

    add("total offense", _higher_factor(
        "yards", "Total offense", away, home,
        _num(_get(a, "totalYards")), _num(_get(h, "totalYards")),
        meaningful=60, scale=180, unit=" yards", detail_label="yardage"))
    add("yards per pass", _higher_factor(
        "pass_efficiency", "Passing efficiency", away, home,
        _num(_get(a, "yardsPerPass")), _num(_get(h, "yardsPerPass")),
        meaningful=1.25, scale=3.5, unit=" yds/att", detail_label="passing efficiency"))
    add("yards per rush", _higher_factor(
        "rush_efficiency", "Rushing efficiency", away, home,
        _num(_get(a, "yardsPerRushAttempt")), _num(_get(h, "yardsPerRushAttempt")),
        meaningful=.8, scale=2.5, unit=" yds/carry", detail_label="rushing efficiency"))
    add("first downs", _higher_factor(
        "first_downs", "First downs", away, home,
        _num(_get(a, "firstDowns")), _num(_get(h, "firstDowns")),
        meaningful=5, scale=12, detail_label="sustained offense"))
    add("third down", _conversion_factor(
        "third_down", "Third down", away, home,
        _get(a, "thirdDownEff"), _get(h, "thirdDownEff")))
    add("fourth down", _conversion_factor(
        "fourth_down", "Fourth down", away, home,
        _get(a, "fourthDownEff"), _get(h, "fourthDownEff"), meaningful=.25))

    a_to, h_to = _num(_get(a, "turnovers")), _num(_get(h, "turnovers"))
    if a_to is not None and h_to is not None:
        considered.append("turnovers")
        if abs(a_to - h_to) >= 1:
            winner, loser = (away, home) if a_to < h_to else (home, away)
            low, high = min(a_to, h_to), max(a_to, h_to)
            factors.append(Factor(
                "turnovers", "Turnovers", winner, loser,
                min(100, 55 + 18 * abs(a_to - h_to)),
                f"{winner} protected the football better",
                f"{loser} committed {high:g} turnover{'s' if high != 1 else ''}; {winner} committed {low:g}."
            ))

    # Defensive disruption is a composite because one stat can understate the
    # game (e.g. pressures without sacks) and box score availability varies.
    considered.append("defensive disruption")
    def disruption(s: dict[str, Any]) -> tuple[float, list[str]]:
        values = []
        score = 0.0
        for key, label, weight in (("sacks", "sacks", 3.0),
                                   ("tacklesForLoss", "TFL", 1.4),
                                   ("qbHurries", "hurries", .8),
                                   ("passesDeflected", "PD", .6),
                                   ("passesIntercepted", "INT", 2.5),
                                   ("fumblesRecovered", "FR", 2.5)):
            value = _num(_get(s, key))
            if value is not None:
                score += value * weight
                values.append(f"{value:g} {label}")
        return score, values
    ad, ad_parts = disruption(a); hd, hd_parts = disruption(h)
    if (ad_parts or hd_parts) and abs(ad - hd) >= 3:
        winner, loser = (away, home) if ad > hd else (home, away)
        win_score, lose_score = (ad, hd) if ad > hd else (hd, ad)
        win_parts = ad_parts if ad > hd else hd_parts
        lose_parts = hd_parts if ad > hd else ad_parts
        factors.append(Factor(
            "disruption", "Defensive disruption", winner, loser,
            min(100, 45 + 8 * (win_score - lose_score)),
            f"{winner}'s defense created more disruption",
            f"{winner}: {', '.join(win_parts) or 'limited recorded disruption'}; "
            f"{loser}: {', '.join(lose_parts) or 'limited recorded disruption'}."
        ))

    # Penalty row is commonly stored as "N-YDS"; compare penalty yards when
    # present but keep confidence medium because enforcement context is absent.
    considered.append("penalties")
    def penalty_yards(value: Any) -> float | None:
        ratio = _ratio(value)
        return ratio[1] if ratio else None
    apy, hpy = penalty_yards(_get(a, "totalPenaltiesYards")), penalty_yards(_get(h, "totalPenaltiesYards"))
    if apy is not None and hpy is not None and abs(apy - hpy) >= 25:
        winner, loser = (away, home) if apy < hpy else (home, away)
        low, high = min(apy, hpy), max(apy, hpy)
        factors.append(Factor(
            "penalties", "Penalty cost", winner, loser,
            min(85, 35 + abs(apy-hpy)),
            f"{winner} gave away fewer penalty yards",
            f"{winner} was charged with {low:g} penalty yards versus {high:g} for {loser}.",
            confidence="medium"))

    considered.append("possession")
    apos = _clock_seconds(_get(a, "possessionTime")); hpos = _clock_seconds(_get(h, "possessionTime"))
    if apos is not None and hpos is not None and abs(apos-hpos) >= 300:
        winner, loser = (away, home) if apos > hpos else (home, away)
        high, low = max(apos, hpos), min(apos, hpos)
        factors.append(Factor(
            "possession", "Possession", winner, loser,
            min(75, 35 + abs(apos-hpos)/20),
            f"{winner} controlled more of the clock",
            f"{winner} held the ball for {int(high//60)}:{int(high%60):02d} versus {int(low//60)}:{int(low%60):02d}.",
            confidence="medium"))

    # Favor factors that align with the winner, but retain a strong losing-team
    # edge because it can explain why a game stayed close or why the score was
    # misleading. Never force the final-score winner into every explanation.
    game_winner, _, _, _ = _side(game)
    factors.sort(key=lambda factor: (factor.winner == game_winner, factor.score), reverse=True)
    available = sum(1 for name in considered if name)  # stable denominator for UI
    coverage = {
        "considered": considered,
        "factor_count": len(factors),
        "coverage_note": (
            "Team-box evidence covers efficiency, possession, conversions, turnovers, "
            "penalties and defensive disruption when the provider supplies those fields. "
            "Drive-level field position, explosives, success rate and game-state leverage "
            "remain pending richer play/drive data."
        ),
        "available_categories": available,
    }
    return [factor.as_dict() for factor in factors[:6]], coverage


def _player_lines(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    players: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        team = str(row.get("team") or "").strip()
        name = str(row.get("player") or row.get("player_name") or "").strip()
        if not team or not name:
            continue
        key = (team, str(row.get("player_id") or name))
        item = players.setdefault(key, {
            "team": team, "player": name, "player_id": row.get("player_id"),
            "stats": defaultdict(dict), "score": 0.0,
        })
        category = str(row.get("category") or "")
        stat_type = str(row.get("stat_type") or "").upper()
        value = row.get("numeric_value")
        if value is None:
            value = row.get("stat_value")
        item["stats"][category][stat_type] = value
        number = _num(value)
        if number is None:
            continue
        weights = {
            ("passing", "YDS"): .035, ("passing", "TD"): 6,
            ("rushing", "YDS"): .08, ("rushing", "TD"): 6,
            ("receiving", "YDS"): .08, ("receiving", "REC"): .7,
            ("receiving", "TD"): 6, ("defensive", "TOT"): .8,
            ("defensive", "TFL"): 2.0, ("defensive", "SACKS"): 3.2,
            ("defensive", "PD"): 1.2, ("interceptions", "INT"): 5,
            ("fumbles", "REC"): 4, ("kicking", "PTS"): 1.0,
            ("puntReturns", "TD"): 7, ("kickReturns", "TD"): 7,
        }
        item["score"] += number * weights.get((category, stat_type), 0.0)
    return list(players.values())


def _player_summary(player: dict[str, Any]) -> str:
    stats = player["stats"]
    parts: list[str] = []
    passing = stats.get("passing", {})
    rushing = stats.get("rushing", {})
    receiving = stats.get("receiving", {})
    defense = stats.get("defensive", {})
    ints = stats.get("interceptions", {})
    if _num(passing.get("YDS")) is not None:
        line = f"{_num(passing.get('YDS')):g} pass yds"
        if _num(passing.get("TD")) is not None:
            line += f", {_num(passing.get('TD')):g} TD"
        if _num(passing.get("INT")) is not None:
            line += f", {_num(passing.get('INT')):g} INT"
        parts.append(line)
    if _num(rushing.get("YDS")) is not None:
        line = f"{_num(rushing.get('YDS')):g} rush yds"
        if _num(rushing.get("TD")) is not None:
            line += f", {_num(rushing.get('TD')):g} TD"
        parts.append(line)
    if _num(receiving.get("YDS")) is not None or _num(receiving.get("REC")) is not None:
        rec = _num(receiving.get("REC")); yds = _num(receiving.get("YDS"))
        line = f"{rec:g} rec" if rec is not None else "Receiving"
        if yds is not None:
            line += f", {yds:g} yds"
        if _num(receiving.get("TD")) is not None:
            line += f", {_num(receiving.get('TD')):g} TD"
        parts.append(line)
    defensive = []
    for key, label in (("TOT", "tkl"), ("TFL", "TFL"), ("SACKS", "sack"), ("PD", "PD")):
        value = _num(defense.get(key))
        if value:
            defensive.append(f"{value:g} {label}")
    ivalue = _num(ints.get("INT"))
    if ivalue:
        defensive.append(f"{ivalue:g} INT")
    if defensive:
        parts.append(", ".join(defensive))
    return " · ".join(parts[:2]) or "Recorded game impact"


def player_impacts(player_rows: Iterable[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    players = _player_lines(player_rows)
    players.sort(key=lambda row: (-row["score"], row["team"], row["player"]))
    return [
        {"team": row["team"], "player": row["player"], "player_id": row.get("player_id"),
         "impact_score": round(row["score"], 2), "summary": _player_summary(row)}
        for row in players if row["score"] > 0
    ][:limit]


def role_updates(repository, game: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    season = int(game.get("season") or 0)
    output: list[dict[str, Any]] = []
    for team in (game.get("away_team"), game.get("home_team")):
        if not team:
            continue
        observed = observed_depth_roles(repository, str(team), season)
        by_position: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in observed.values():
            by_position[str(item.get("position") or "UNK")].append(item)
        for position, rows in by_position.items():
            rows.sort(key=lambda row: -float(row.get("observed_score") or 0))
            for rank, row in enumerate(rows, 1):
                if not row.get("can_reorder"):
                    continue
                output.append({
                    "team": team, "player_id": row.get("player_id"), "position": position,
                    "observed_rank": rank, "games": row.get("observed_games"),
                    "confidence": row.get("confidence"), "latest_week": row.get("latest_week"),
                    "observed_score": row.get("observed_score"),
                })
    output.sort(key=lambda row: (row["observed_rank"], -float(row.get("observed_score") or 0)))
    return output[:limit]


def postgame_report(repository, game: dict[str, Any], team_rows: Iterable[dict[str, Any]],
                    player_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    winner, loser, winner_points, loser_points = _side(game)
    margin = winner_points - loser_points
    factors, coverage = decisive_factors(game, team_rows)
    winner_factors = [factor for factor in factors if factor.get("winner") == winner]
    if winner_factors:
        top = winner_factors[0]
        story = (
            f"{winner} beat {loser} {winner_points:g}-{loser_points:g}. "
            f"The strongest measurable separator was {top['label'].lower()}: {top['detail']}"
        )
        if len(winner_factors) > 1:
            story += f" {winner_factors[1]['headline']} as well."
    else:
        story = (
            f"{winner} beat {loser} {winner_points:g}-{loser_points:g}, but the currently "
            "stored team-box fields do not isolate one dominant statistical separator."
        )
    if margin <= 7:
        complexion = "one-score game"
    elif margin <= 17:
        complexion = "clear but competitive result"
    else:
        complexion = "decisive result"
    return {
        "winner": winner, "loser": loser, "margin": margin,
        "complexion": complexion, "story": story,
        "factors": factors, "players": player_impacts(player_rows),
        "roles": role_updates(repository, game), "coverage": coverage,
    }
