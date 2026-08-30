"""Supplement the postgame report with compact pace/game-state and WP turning points."""
from __future__ import annotations

from html import escape
import re
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.identity import team_identity
from sports_aggregator.cfb.pace import game_pace_summary
from sports_aggregator.cfb.wp_turning_points import game_turning_points

ANCHOR = "{{ postgame_analysis(game, team_stats, player_stats) }}"
REPLACEMENT = ANCHOR + "\n{{ postgame_pace_and_leverage(game) }}"
WP_MODEL_VERSION = "wp-v2"

STYLE = '''<style>
.pg-analytics{margin:0 0 28px}.pg-section-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-end;margin:22px 0 9px;padding-bottom:7px;border-bottom:1px solid var(--line)}.pg-section-head h3{margin:0;font-size:.9rem}.pg-section-head span{font-size:.56rem;color:var(--muted)}
.pg-summary-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:8px 0}.pg-summary-card{border:1px solid var(--line);background:var(--paper);padding:10px 12px}.pg-summary-card h4{margin:0 0 8px;font-size:.68rem}.pg-summary-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.pg-summary-metric span{display:block;font-size:.48rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:800}.pg-summary-metric strong{display:block;margin-top:3px;font-family:var(--display-font);font-size:.82rem;font-variant-numeric:tabular-nums}
.pg-details{border:1px solid var(--line);background:var(--paper);margin:8px 0 5px}.pg-details summary{cursor:pointer;padding:8px 11px;font-size:.56rem;font-weight:800;text-transform:uppercase;letter-spacing:.055em;color:var(--muted)}.pg-details[open] summary{border-bottom:1px solid var(--line)}.pg-detail-grid{display:grid;grid-template-columns:1fr 1fr}.pg-detail-team{padding:9px 11px}.pg-detail-team+.pg-detail-team{border-left:1px solid var(--line)}.pg-detail-team h4{margin:0 0 6px;font-size:.64rem}.pg-row{display:grid;grid-template-columns:1.35fr .72fr .72fr;gap:8px;padding:4px 0;border-top:1px solid var(--line);font-size:.57rem}.pg-row:first-of-type{border-top:0}.pg-num{text-align:right;font-variant-numeric:tabular-nums}.pg-note{color:var(--muted);font-size:.55rem;line-height:1.45;margin:7px 0 0}
.pg-turning{border-top:1px solid var(--line);margin:10px 0 4px}.pg-turn{display:grid;grid-template-columns:42px minmax(285px,.9fr) minmax(0,1.45fr);gap:15px;padding:15px 4px;border-bottom:1px solid var(--line);align-items:start}.pg-turn-rank{font-family:var(--display-font);font-size:1.18rem;line-height:1;color:var(--team-light);font-variant-numeric:tabular-nums;padding-top:2px}.pg-turn-main{min-width:0}.pg-turn-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.pg-turn-clock{font-family:var(--display-font);font-size:.78rem;font-weight:850;letter-spacing:-.01em}.pg-turn-event{font-size:.5rem;text-transform:uppercase;letter-spacing:.065em;font-weight:900;color:var(--team-light);border:1px solid color-mix(in srgb,var(--team-light) 55%,var(--line));padding:3px 6px}.pg-turn-swing{margin-top:6px;font-family:var(--display-font);font-size:.9rem;line-height:1.15;font-variant-numeric:tabular-nums}.pg-turn-swing strong{font-size:1rem}.pg-turn-wp{margin-top:4px;font-size:.66rem;font-weight:800}.pg-turn-context{margin-top:8px;font-size:.65rem;line-height:1.5;color:var(--muted)}.pg-turn-play{margin:0;font-size:.72rem;line-height:1.55;font-weight:700}.pg-turn-player{font-weight:950}.pg-turn-meta{margin-top:6px;color:var(--muted);font-size:.54rem;line-height:1.4}.pg-turn-attribution{margin-top:6px;font-size:.54rem;color:var(--muted);font-style:italic}
.box-report .efficiency-best{display:none!important}.box-report .efficiency-cell.edge{box-shadow:none!important;background:transparent!important}
@media(max-width:820px){.pg-turn{grid-template-columns:34px 1fr;gap:10px}.pg-turn-detail{grid-column:2}.pg-summary-grid,.pg-detail-grid{grid-template-columns:1fr}.pg-detail-team+.pg-detail-team{border-left:0;border-top:1px solid var(--line)}.pg-section-head span{display:none}}
@media(max-width:460px){.pg-summary-metrics{grid-template-columns:1fr 1fr}.pg-turn{grid-template-columns:28px 1fr}.pg-turn-rank{font-size:1rem}.pg-turn-swing{font-size:.8rem}.pg-turn-play{font-size:.68rem}}
</style>'''


class _Loader(BaseLoader):
    def __init__(self, wrapped): self.wrapped = wrapped
    def get_source(self, environment, template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_box_score.html" and "postgame_pace_and_leverage(" not in source:
            source = source.replace(ANCHOR, REPLACEMENT, 1)
        return source, filename, uptodate
    def list_templates(self):
        return self.wrapped.list_templates() if hasattr(self.wrapped, "list_templates") else []


def _pct(value: Any) -> str:
    return "—" if value is None else f"{100 * float(value):.1f}%"


def _rate(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}/min"


def _summary_card(team: str, states: dict[str, Any]) -> str:
    overall = states.get("overall") or {}
    neutral = states.get("neutral") or {}
    passing = states.get("passing_downs") or {}
    return (
        '<article class="pg-summary-card">'
        f'<h4>{escape(team)}</h4><div class="pg-summary-metrics">'
        '<div class="pg-summary-metric"><span>Overall tempo</span>'
        f'<strong>{escape(_rate(overall.get("play_rate")))}</strong></div>'
        '<div class="pg-summary-metric"><span>Neutral pass rate</span>'
        f'<strong>{escape(_pct(neutral.get("pass_rate")))}</strong></div>'
        '<div class="pg-summary-metric"><span>Passing-down pass</span>'
        f'<strong>{escape(_pct(passing.get("pass_rate")))}</strong></div>'
        '</div></article>'
    )


def _detail_team(team: str, states: dict[str, Any]) -> str:
    labels = (
        ("overall", "Overall"), ("neutral", "Neutral"), ("leading", "Leading"), ("trailing", "Trailing"),
        ("leading_one_score", "Lead ≤8"), ("leading_multi_score", "Lead 9+"),
        ("trailing_one_score", "Trail ≤8"), ("trailing_multi_score", "Trail 9+"),
        ("standard_downs", "Standard downs"), ("passing_downs", "Passing downs"),
    )
    rows = []
    for key, label in labels:
        row = states.get(key)
        if not row or not row.get("plays"): continue
        rows.append(
            '<div class="pg-row">'
            f'<span>{escape(label)} <small>({int(row.get("plays") or 0)})</small></span>'
            f'<span class="pg-num">{escape(_rate(row.get("play_rate")))}</span>'
            f'<span class="pg-num">{escape(_pct(row.get("pass_rate")))}</span></div>'
        )
    return (
        '<section class="pg-detail-team">'
        f'<h4>{escape(team)}</h4>'
        '<div class="pg-row"><strong>Situation</strong><strong class="pg-num">Tempo</strong><strong class="pg-num">Pass</strong></div>'
        + ''.join(rows) + '</section>'
    )


def _down_distance(row: dict[str, Any]) -> str:
    down = row.get("down"); distance = row.get("distance")
    if down is None or distance is None: return ""
    try: down_i = int(down); distance_i = int(distance)
    except (TypeError, ValueError): return ""
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(down_i, "th")
    return f"{down_i}{suffix} & {distance_i}"


def _field_position(row: dict[str, Any]) -> str:
    offense = str(row.get("offense") or ""); defense = str(row.get("defense") or "")
    try: ytg = int(row.get("yards_to_goal"))
    except (TypeError, ValueError): return ""
    if ytg <= 50: return f"at the {defense} {ytg}" if defense else f"{ytg} yards from goal"
    own = 100 - ytg
    return f"at the {offense} {own}" if offense else f"{ytg} yards from goal"


def _scoreline(row: dict[str, Any], game: dict[str, Any]) -> str:
    home = row.get("home_score"); away = row.get("away_score")
    if home is None or away is None: return ""
    return f"{game.get('away_team') or 'Away'} {away} · {game.get('home_team') or 'Home'} {home}"


def _event_label(row: dict[str, Any]) -> str:
    text = f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()
    if "touchdown" in text and "intercept" in text: return "Pick-six"
    if "touchdown" in text and "fumble" in text: return "Fumble TD"
    if "touchdown" in text: return "Touchdown"
    if "intercept" in text: return "Interception"
    if "fumble" in text: return "Fumble"
    if "field goal" in text: return "Field goal"
    if "safety" in text: return "Safety"
    if int(row.get("down") or 0) == 4: return "Fourth down"
    return ""


def _event_clock(row: dict[str, Any]) -> tuple[int, int, int]:
    period = row.get("event_period") if row.get("event_period") is not None else row.get("period")
    minute = row.get("event_clock_minutes") if row.get("event_clock_minutes") is not None else row.get("clock_minutes")
    second = row.get("event_clock_seconds") if row.get("event_clock_seconds") is not None else row.get("clock_seconds")
    try: return int(period or 0), int(minute or 0), int(second or 0)
    except (TypeError, ValueError): return 0, 0, 0


def _team_codes(team: str) -> set[str]:
    letters = re.sub(r"[^A-Z]", "", team.upper())
    words = re.findall(r"[A-Za-z]+", team)
    codes = {letters[:size] for size in range(2, min(6, len(letters) + 1))}
    if len(words) > 1: codes.add("".join(word[0] for word in words).upper())
    return {code for code in codes if len(code) >= 2}


def _humanize_field_codes(text: str, game: dict[str, Any]) -> str:
    teams = (str(game.get("away_team") or "Away"), str(game.get("home_team") or "Home"))
    code_map: dict[str, str] = {}
    for team in teams:
        for code in _team_codes(team): code_map[code] = team
    def replace(match: re.Match[str]) -> str:
        code = match.group(1).upper(); yard = int(match.group(2)); team = code_map.get(code)
        if not team: return match.group(0)
        return f"{team} goal line" if yard == 0 else f"{team} {yard}"
    return re.sub(r"\b([A-Z]{2,6})(\d{2})\b", replace, text)


def _clean_play_text(text: str, game: dict[str, Any]) -> str:
    human = _humanize_field_codes(text, game)
    human = re.sub(r"^\(\d{1,2}:\d{2}\)\s*", "", human).strip()
    # Provider completions often repeat the catch/end spot when there was no YAC.
    human = re.sub(
        r"caught at ([A-Za-zÀ-žʻ'’ .\-]+(?:goal line|\d{1,2})),\s*for (-?\d+) yards to (?:the )?\1",
        lambda m: f"caught at {m.group(1)} — {m.group(2)} yards",
        human,
        flags=re.I,
    )
    return human


def _team_colors(repository, game: dict[str, Any]) -> dict[str, str]:
    colors: dict[str, str] = {}
    for side in ("away", "home"):
        team = str(game.get(f"{side}_team") or ""); team_id = game.get(f"{side}_team_id")
        if not team or team_id is None: continue
        try:
            identity = team_identity(repository.brand_for(int(team_id)))
            colors[team] = str(identity.get("accent_dark") or identity.get("accent") or "var(--team-light)")
        except Exception: colors[team] = "var(--team-light)"
    return colors


_PLAYER = re.compile(r"#\d+\s+([A-Z][A-Za-z.'’\-]*(?:\s+[A-Z][A-Za-z.'’\-]*)?)")
_DEFENSE_CUES = ("broken up by", "tackled by", "sacked by", "intercepted by", "forced by", "recovered by", "blocked by", "hurried by")


def _play_html(text: str, game: dict[str, Any], colors: dict[str, str], offense: str, defense: str) -> str:
    human = _clean_play_text(text, game)
    pieces: list[str] = []
    last = 0
    for match in _PLAYER.finditer(human):
        pieces.append(escape(human[last:match.start()]))
        before = human[max(0, match.start() - 40):match.start()].casefold().rstrip()
        parenthetical = match.start() > 0 and human[match.start() - 1] == "("
        defender = parenthetical or any(before.endswith(cue) for cue in _DEFENSE_CUES)
        team = defense if defender else offense
        color = colors.get(team, "var(--team-light)")
        prefix = human[match.start():match.start(1)]
        pieces.append(escape(prefix))
        pieces.append(
            f'<span class="pg-turn-player" style="color:{escape(color, quote=True)}">'
            f'{escape(match.group(1))}</span>'
        )
        last = match.end()
    pieces.append(escape(human[last:]))
    return "".join(pieces)


def _render(repository, game: dict[str, Any]) -> Markup:
    game_id = int(game.get("game_id") or 0)
    try: pace = game_pace_summary(repository, game_id)
    except Exception: pace = {"teams": {}}
    try: turns = game_turning_points(repository, game_id, model_version=WP_MODEL_VERSION)
    except Exception: turns = []
    teams = pace.get("teams") or {}
    if not teams and not turns: return Markup("")

    colors = _team_colors(repository, game)
    away_team = str(game.get("away_team") or "Away"); home_team = str(game.get("home_team") or "Home")
    away_color = colors.get(away_team, "var(--team-light)"); home_color = colors.get(home_team, "var(--team-light)")
    efficiency_override = (
        '<style>'
        f'.box-report .efficiency-row .efficiency-cell:nth-child(2).edge{{color:{escape(away_color, quote=True)}!important}}'
        f'.box-report .efficiency-row .efficiency-cell:nth-child(3).edge{{color:{escape(home_color, quote=True)}!important}}'
        '</style>'
    )

    pace_html = ""
    if teams:
        summaries = ''.join(_summary_card(team, states) for team, states in teams.items())
        details = ''.join(_detail_team(team, states) for team, states in teams.items())
        pace_html = (
            '<div class="pg-section-head"><h3>Pace & game state</h3><span>pace-v1 · situational detail on demand</span></div>'
            f'<div class="pg-summary-grid">{summaries}</div>'
            '<details class="pg-details"><summary>View full pace splits</summary>'
            f'<div class="pg-detail-grid">{details}</div></details>'
            '<p class="pg-note">Tempo uses represented same-drive game-clock intervals between qualifying rush/pass snaps; it is a comparison proxy, not wall-clock seconds to snap.</p>'
        )

    turn_rows = []
    for rank, row in enumerate(turns, 1):
        period, minute, second = _event_clock(row)
        leverage = float(row.get("leverage") or 0); before = row.get("home_wp_before"); after = row.get("home_wp_after")
        event_label = _event_label(row); context = []
        scoreline = _scoreline(row, game)
        if scoreline: context.append(scoreline)
        dd = _down_distance(row)
        if dd: context.append(dd)
        field = _field_position(row)
        if field: context.append(field)
        offense = str(row.get("offense") or ""); defense = str(row.get("defense") or "")
        if offense: context.append(f"{offense} ball")
        gained = row.get("yards_gained")
        if gained is not None:
            try:
                yards = int(gained)
                context.append(f"gain of {yards} yards" if yards >= 0 else f"loss of {abs(yards)} yards")
            except (TypeError, ValueError): pass

        wp_html = ""
        if before is not None and after is not None:
            direction = "↑" if float(after) > float(before) else "↓" if float(after) < float(before) else "→"
            wp_html = f'<div class="pg-turn-wp">{escape(home_team)} WP&nbsp; {100 * float(before):.1f}% {direction} {100 * float(after):.1f}%</div>'
        label_html = f'<span class="pg-turn-event">{escape(event_label)}</span>' if event_label else ""
        attribution = "Major event matched to surrounding valid WP states." if row.get("attribution") == "special_event" else "WP transition attributed to this pre-play state."
        play_html = _play_html(str(row.get("play_text") or row.get("play_type") or "Play"), game, colors, offense, defense)
        turn_rows.append(
            '<article class="pg-turn">'
            f'<div class="pg-turn-rank">{rank:02d}</div>'
            '<div class="pg-turn-main">'
            f'<div class="pg-turn-head"><span class="pg-turn-clock">Q{period} · {minute}:{second:02d}</span>{label_html}</div>'
            f'<div class="pg-turn-swing"><strong>{100 * leverage:.1f}</strong> win-probability points</div>'
            f'{wp_html}<div class="pg-turn-context">{" · ".join(escape(piece) for piece in context)}</div>'
            '</div>'
            '<div class="pg-turn-detail">'
            f'<p class="pg-turn-play">{play_html}</p>'
            f'<div class="pg-turn-attribution">{escape(attribution)}</div>'
            '<div class="pg-turn-meta">WP direction is checked against ep-v1 play value; large unsupported state discontinuities are suppressed.</div>'
            '</div></article>'
        )
    turning_html = ''.join(turn_rows) or f'<div class="empty">Fit and score {escape(WP_MODEL_VERSION)} to identify leverage and turning points.</div>'

    return Markup(
        STYLE + efficiency_override + '<section class="section pg-analytics">' + pace_html +
        f'<div class="pg-section-head"><h3>Turning points</h3><span>{escape(WP_MODEL_VERSION)} · EPA-checked event swings</span></div>'
        f'<div class="pg-turning">{turning_html}</div></section>'
    )


def install_postgame_analytics_display(app) -> None:
    if app.extensions.get("postgame_analytics_display_installed"): return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["postgame_pace_and_leverage"] = lambda game: _render(repository, dict(game))
    app.jinja_loader = _Loader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["postgame_analytics_display_installed"] = True
