"""Where each team throws, and what it is worth: middle of the field vs outside.

A pass over the middle is worth about four times one outside it -- 0.42 EPA per
attempt against 0.11 and 0.08 across the classified 2025 attempts -- and deep
middle is the most valuable throw in the sport. That gap is what these blocks
show, on the matchup page before the game and in the postgame report after it.

Two things are said out loud rather than assumed. The attempt counts are printed
next to every rate, because coverage is partial before 2026 and a middle EPA over
six throws is not a number to read. And the difference is not a recommendation:
quarterbacks throw over the middle when coverage allows it, so the number
describes where value was available, not what would happen if a team forced more
of them.
"""

from __future__ import annotations

from html import escape
from typing import Any

from markupsafe import Markup

from sports_aggregator.cfb.middle_of_field import (
    MIN_GAME_PLAYS, MIN_SEASON_PLAYS, game_middle, middle_verdict, team_season_middle,
)
from sports_aggregator.cfb.passing_plays import DEPTH_BANDS, MIN_SEASON_ATTEMPTS, passer_profile


STYLE = '''<style>
.pass-map{margin:8px 0 4px}
.pass-map-grid{display:grid;grid-template-columns:1fr;gap:10px}
@media(min-width:760px){.pass-map-grid{grid-template-columns:1fr 1fr}}
.pass-team{border:1px solid var(--line);background:var(--paper);min-width:0}
.pass-team h4{margin:0;padding:8px 11px;border-bottom:1px solid var(--line);font-size:.72rem;
  display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.pass-team h4 span{color:var(--muted);font-size:.55rem;font-weight:700}
.pass-side{padding:9px 11px}
.pass-side+.pass-side{border-top:1px solid var(--line)}
.pass-side-label{font-size:.5rem;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);font-weight:850;margin-bottom:6px}
.pass-row{display:grid;grid-template-columns:minmax(64px,.6fr) repeat(3,minmax(0,1fr));gap:8px;
  padding:5px 0;border-top:1px solid var(--cell-line);font-size:.63rem;align-items:baseline}
.pass-row:first-of-type{border-top:0}
.pass-row .num{text-align:right;font-variant-numeric:tabular-nums}
.pass-row.head{color:var(--muted);font-size:.5rem;text-transform:uppercase;
  letter-spacing:.06em;font-weight:850}
.pass-row strong{font-family:var(--display-font);font-size:.78rem}
.pass-row .best{color:var(--edge);font-weight:900}
.pass-thin{color:var(--muted);font-style:italic}
.pass-note{color:var(--muted);font-size:.56rem;line-height:1.45;margin:8px 0 0}
.mof-verdict{border-left:3px solid var(--edge);background:var(--paper);padding:8px 12px;margin:8px 0;font-size:.72rem}
.mof-block+.mof-block{margin-top:14px}
.mof-block h3{margin:0 0 6px;font-size:.74rem}
.mof-grid{border:1px solid var(--line);background:var(--paper);margin:8px 0 4px}
.mof-row{display:grid;grid-template-columns:minmax(96px,1.1fr) repeat(var(--mof-columns),minmax(0,1fr));gap:8px;padding:7px 11px;border-top:1px solid var(--cell-line);align-items:baseline}
.mof-row:first-child{border-top:0}
.mof-header{background:var(--head-bg);color:var(--head-ink)}
.mof-head{font-size:.52rem;text-transform:uppercase;letter-spacing:.05em;font-weight:850;text-align:right}
.mof-label{font-size:.63rem;font-weight:750}
.mof-total{border-top:2px solid var(--line-strong)}
.mof-total .mof-label{font-weight:900}
.mof-cell{text-align:right;min-width:0}
.mof-cell strong{display:block;font-family:var(--display-font);font-size:.82rem;font-variant-numeric:tabular-nums}
.mof-cell strong.best{color:var(--edge)}
.mof-cell span{display:block;color:var(--muted);font-size:.5rem;margin-top:1px}
.pass-qb-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);margin:8px 0}
@media(min-width:620px){.pass-qb-facts{grid-template-columns:repeat(5,minmax(0,1fr))}}
.pass-qb-fact{background:var(--paper);padding:8px 10px}
.pass-qb-fact b{display:block;font:800 1.02rem var(--display-font);font-variant-numeric:tabular-nums}
.pass-qb-fact span{display:block;color:var(--muted);font-size:.54rem;text-transform:uppercase;
  letter-spacing:.05em;font-weight:850;margin-top:2px}
.pass-depth{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:10px 0 4px}
.pass-depth div{border-top:2px solid var(--line);padding-top:6px}
.pass-depth b{display:block;font:800 .92rem var(--display-font);font-variant-numeric:tabular-nums}
.pass-depth span{display:block;color:var(--muted);font-size:.52rem;text-transform:uppercase;
  letter-spacing:.05em;font-weight:850;margin-top:2px}
</style>'''


def _f2(value: Any, *, signed: bool = True) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:+.2f}" if signed else f"{number:.2f}"


def _pct(value: Any) -> str:
    try:
        return f"{100 * float(value):.0f}%"
    except (TypeError, ValueError):
        return "—"


def _f1(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def _side(label: str, buckets: dict[str, Any], *, minimum: int, better_high: bool) -> str:
    """One team, one side of the ball: middle against everything else."""
    middle, outside = buckets.get("middle") or {}, buckets.get("outside") or {}
    rows = [
        '<div class="pass-row head"><span></span><span class="num">EPA/att</span>'
        '<span class="num">Att</span><span class="num">ADOT</span></div>'
    ]
    values = [middle.get("epa_per_attempt"), outside.get("epa_per_attempt")]
    thin = [(middle.get("attempts") or 0) < minimum, (outside.get("attempts") or 0) < minimum]
    # Only mark an edge where both sides carry enough attempts to compare.
    best = None
    if not any(thin) and None not in values and values[0] != values[1]:
        best = 0 if (values[0] > values[1]) == better_high else 1
    for index, (name, bucket) in enumerate((("Middle", middle), ("Outside", outside))):
        attempts = bucket.get("attempts") or 0
        classes = "pass-row" + (" pass-thin" if thin[index] else "")
        epa = f'<strong class="{"best" if best == index else ""}">{_f2(bucket.get("epa_per_attempt"))}</strong>'
        rows.append(
            f'<div class="{classes}"><span>{escape(name)}</span>'
            f'<span class="num">{epa}</span>'
            f'<span class="num">{attempts}</span>'
            f'<span class="num">{_f1(bucket.get("adot"))}</span></div>')
    share = buckets.get("middle_share")
    share_text = f" · {_pct(share)} of attempts to the middle" if share is not None else ""
    return (f'<div class="pass-side"><div class="pass-side-label">{escape(label)}'
            f'{escape(share_text)}</div>{"".join(rows)}</div>')



def _empty(reason: str, *, heading: bool = True) -> Markup:
    return Markup(
        STYLE + '<section class="section pass-map">'
        + (_TITLE if heading else "")
        + f'<div class="empty">{escape(reason)}</div></section>')



#: Run and pass, each split middle against outside, then the two together. The
#: combined row is the one the verdict is drawn from: a team can lose the middle
#: on the ground and win it through the air, and neither half alone says which.
ROWS = (("run", "middle", "Run — middle"), ("run", "outside", "Run — outside"),
        ("pass", "middle", "Pass — middle"), ("pass", "outside", "Pass — outside"),
        ("combined", "middle", "All middle"), ("combined", "outside", "All outside"))

NOTE = ("Where each team attacked and what it returned, split between the middle "
        "of the field and outside it, run and pass together. EPA is our "
        "event-aligned ep-v2 model; pass direction is CFBD per-attempt detail and "
        "rush direction is parsed from play text, so coverage varies by game and "
        "the play count behind every figure is shown. A middle throw has been "
        "worth about four times an outside one, but that reflects where coverage "
        "allowed the ball to go: it describes value that was available, not a "
        "play-call recommendation.")


def _cell(bucket, *, minimum, best=False):
    plays = bucket.get("plays") or 0
    thin = plays < minimum
    success = bucket.get("success_rate")
    tail = "" if success is None else " \u00b7 %s success" % _pct(success)
    mark = "best" if best and not thin else ""
    return ('<div class="mof-cell%s"><strong class="%s">%s</strong>'
            '<span>%d plays%s</span></div>'
            % (" pass-thin" if thin else "", mark, _f2(bucket.get("epa_per_play")), plays, tail))


def _matrix(columns, *, minimum, better_high=True):
    """One row per split, one column per thing being compared."""
    width = len(columns)
    head = "".join('<div class="mof-head">%s</div>' % escape(label) for label, _ in columns)
    body = []
    for source, side, label in ROWS:
        values = [data[source][side].get("epa_per_play") for _l, data in columns]
        counts = [data[source][side].get("plays") or 0 for _l, data in columns]
        usable = [v for v, c in zip(values, counts) if v is not None and c >= minimum]
        winner = None
        if len(usable) == width and width > 1 and len(set(usable)) > 1:
            winner = values.index(max(usable) if better_high else min(usable))
        cells = "".join(_cell(data[source][side], minimum=minimum, best=(i == winner))
                        for i, (_l, data) in enumerate(columns))
        emphasis = " mof-total" if source == "combined" else ""
        body.append('<div class="mof-row%s"><div class="mof-label">%s</div>%s</div>'
                    % (emphasis, escape(label), cells))
    return ('<div class="mof-grid" style="--mof-columns:%d">'
            '<div class="mof-row mof-header"><div class="mof-label"></div>%s</div>%s</div>'
            % (width, head, "".join(body)))


#: Printed only where nothing above it already says what this is. The postgame
#: report wraps the block in a numbered chapter whose head is the title.
_TITLE = '<h2>Middle of the field</h2>'


def _shell_middle(inner, note, *, heading=True):
    return Markup(
        STYLE + '<section class="section pass-map">'
        + (_TITLE if heading else "")
        + '<div class="section-note">%s</div>%s<p class="pass-note">%s</p></section>'
        % (escape(NOTE), inner, escape(note)))


def render_game(repository, game):
    """Postgame: both offences compared in the middle, run and pass.

    Offence only. In one game a team's "middle allowed" is the opponent's
    "middle attacked" -- the same plays counted again -- so printing both sides
    for both teams showed two numbers four times.
    """
    game_id = int(game.get("game_id") or 0)
    if not game_id:
        return Markup("")
    try:
        teams = game_middle(repository, game_id)
    except Exception:
        return _empty("Directional detail could not be read for this game.",
                      heading=False)
    order = [str(game.get("away_team") or ""), str(game.get("home_team") or "")]
    columns = [(team, teams[team]) for team in order if team in teams] or list(teams.items())
    if not columns or not any(data["plays"] for _team, data in columns):
        return _empty("No classified run or pass direction is stored for this game. "
                      "Coverage varies by game and is thin before 2025.",
                      heading=False)
    verdict = middle_verdict(dict(columns))
    banner = ""
    if verdict:
        banner = ('<div class="mof-verdict"><strong>%s</strong> won the middle, '
                  '%s EPA per play to %s.</div>'
                  % (escape(verdict["winner"]), _f2(verdict["winner_epa"]),
                     _f2(verdict["loser_epa"])))
    return _shell_middle(
        banner + _matrix(columns, minimum=MIN_GAME_PLAYS),
        "A split under %d plays is greyed and carries no edge mark. Each column is "
        "that team with the ball, so what the opponent gave up is the other column "
        "rather than a repeat of it." % MIN_GAME_PLAYS,
        heading=False)


def render_matchup(repository, game):
    """Pre-game: each offence against the defence it is about to face."""
    season = int(game.get("season") or 0)
    away = str(game.get("away_team") or "")
    home = str(game.get("home_team") or "")
    if not season or not away or not home:
        return Markup("")
    try:
        splits = {team: team_season_middle(repository, team, season) for team in (away, home)}
    except Exception:
        return _empty("Directional detail could not be read for %d." % season)
    if not any(side["plays"] for team in splits.values()
               for side in (team["offense"], team["defense"])):
        return _empty("No classified run or pass direction is stored for %d yet." % season)
    blocks = []
    for attacker, defender in ((away, home), (home, away)):
        columns = [("%s with the ball" % attacker, splits[attacker]["offense"]),
                   ("%s allowed" % defender, splits[defender]["defense"])]
        blocks.append('<div class="mof-block"><h3>When %s has the ball</h3>%s</div>'
                      % (escape(attacker), _matrix(columns, minimum=MIN_SEASON_PLAYS)))
    return _shell_middle(
        "".join(blocks),
        "Season to date. A split under %d plays is greyed and carries no edge mark. "
        "Each block pairs one offence against the defence it faces, rather than "
        "printing both teams twice." % MIN_SEASON_PLAYS)


_DEPTH_LABELS = {"behind_line": "Behind LOS", "short": "Short (0-9)",
                 "intermediate": "Intermediate (10-19)", "deep": "Deep (20+)"}


def render_passer(repository, player: dict[str, Any], season: Any) -> Markup:
    """A quarterback's season: depth, direction, and what the throws returned."""
    player_id = str(player.get("player_id") or "")
    try:
        year = int(season or player.get("season") or 0)
    except (TypeError, ValueError):
        year = 0
    if not player_id or not year:
        return Markup("")
    try:
        profile = passer_profile(repository, player_id, year)
    except Exception:
        return Markup("")
    # Silent for everyone who did not throw: this belongs on a passer's page,
    # not as an empty panel on every lineman's.
    if not profile["attempts"]:
        return Markup("")

    facts = "".join(
        f'<div class="pass-qb-fact"><b>{value}</b><span>{escape(label)}</span></div>'
        for label, value in (
            ("Attempts", profile["attempts"]),
            ("Completion", _pct(profile["completion_rate"])),
            ("ADOT", _f1(profile["adot"])),
            ("YAC / comp", _f1(profile["yac_per_completion"])),
            ("EPA / attempt", _f2(profile["epa_per_attempt"]))))

    air = profile["air_yards_available"]
    depth_total = sum(profile["depth"].values()) or 1
    depth = "".join(
        f'<div><b>{_pct(profile["depth"][name] / depth_total)}</b>'
        f'<span>{escape(_DEPTH_LABELS[name])}</span></div>'
        for name, _low, _high in DEPTH_BANDS)

    direction = _side("By direction", profile["direction"],
                      minimum=MIN_SEASON_ATTEMPTS, better_high=True)
    return Markup(
        STYLE + '<section class="section pass-map"><h2>Passing profile</h2>'
        '<div class="section-note">Where this quarterback throws and what it returns. '
        "Air yards and direction come from CFBD per-attempt detail; EPA is our "
        "event-aligned ep-v2 model.</div>"
        f'<div class="pass-qb-facts">{facts}</div>'
        f'<div class="pass-side-label">Depth of target</div>'
        f'<div class="pass-depth">{depth}</div>'
        f'<div class="pass-team">{direction}</div>'
        f'<p class="pass-note">Depth is drawn from the {air} of {profile["attempts"]} '
        f'attempts carrying air yards, and direction from the '
        f'{profile["direction"]["attempts"]} that carry it. CFBD publishes both on a '
        'subset before 2026, so these are shares of what was measured rather than of '
        'every throw.</p></section>')


def install_passing_display(app) -> None:
    """Registered as template globals, so the templates call them where they want.

    Deliberately not another Jinja-loader wrapper: the postgame report is already
    assembled by four of those chained on each other's output strings, and each
    new link makes the section order depend on an import order somewhere else.
    """
    if app.extensions.get("passing_display_installed"):
        return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["passing_game_splits"] = (
        lambda game: render_game(repository, dict(game)))
    app.jinja_env.globals["passing_matchup_splits"] = (
        lambda game: render_matchup(repository, dict(game)))
    app.jinja_env.globals["passing_passer_profile"] = (
        lambda player, season=None: render_passer(repository, dict(player), season))
    app.extensions["passing_display_installed"] = True
