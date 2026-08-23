import pathlib
p = pathlib.Path("sports_aggregator/social/content.py")
t = p.read_text(encoding="utf-8")

t = t.replace('''def display_text(item: dict[str, Any], limit: int = 180) -> str:''',
'''#: Markers that an item is about professional football rather than college.
#: An unscoped name match inside one of these is almost always a different
#: person: the Chiefs' Trey Smith is not Purdue's, and a Jaguars preseason post
#: is not about a Vanderbilt lineman.
PRO_CONTEXT = re.compile(
    r"\b(?:nfl|chiefs|jaguars|bengals|packers|steelers|ravens|browns|bills|dolphins|"
    r"patriots|jets|texans|colts|titans|broncos|raiders|chargers|cowboys|eagles|"
    r"commanders|giants|bears|lions|vikings|falcons|panthers|saints|buccaneers|"
    r"cardinals|rams|49ers|seahawks|preseason nfl|rookie (?:season|camp)|"
    r"training camp battle|pro bowl|super bowl)\b", re.I)

#: Evidence that an item really is about college football.
COLLEGE_CONTEXT = re.compile(
    r"\b(?:college football|cfb|ncaa|fbs|heisman|recruit|commit|transfer portal|"
    r"big ten|sec|acc|big 12|pac-12|mountain west|sun belt|conference usa|"
    r"american athletic|bowl game|playoff|signing day|spring game|coordinator)\b", re.I)


def display_text(item: dict[str, Any], limit: int = 180) -> str:''')

t = t.replace('''            elif len(rows) == 1:
                # Unique across every roster, but the team was not resolved from
                # the text. Still the same person; simply less corroborated.
                player, confidence = rows[0], 0.72
                method = "exact_full_name_unscoped"''',
'''            elif len(rows) == 1 and unscoped_allowed:
                # Unique across every roster, but the team was not resolved from
                # the text. Still the same person; simply less corroborated.
                player, confidence = rows[0], 0.72
                method = "exact_full_name_unscoped"''')

t = t.replace('''        by_name = self._players_by_season[season]
        found = []
        for name, rows in by_name.items():''',
'''        by_name = self._players_by_season[season]
        # An unscoped match needs the item to look like college football and not
        # like the pro game, otherwise a shared name silently becomes a link.
        window = text[:2000]
        unscoped_allowed = bool(
            (team_names or COLLEGE_CONTEXT.search(window))
            and not PRO_CONTEXT.search(window))
        found = []
        for name, rows in by_name.items():''')
p.write_text(t, encoding="utf-8")
print("unscoped guard added")
