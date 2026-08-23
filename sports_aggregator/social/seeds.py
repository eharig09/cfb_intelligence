"""User-curated CFB Bluesky sources. Handles here are never auto-discovered."""

from sports_aggregator.social.models import SourceProfile


def _source(handle, name, organization, source_type, tags, *, conferences=(), teams=(),
            reliability=4, reporting=3, analysis=3, breaking=2, prospect=2, g5=2,
            priority=3):
    return SourceProfile(
        handle=handle, display_name=name, organization=organization,
        source_type=source_type, specialties=tuple(tags),
        conferences=tuple(conferences), teams=tuple(teams), reliability=reliability,
        original_reporting_score=reporting, analysis_score=analysis,
        breaking_news_score=breaking, prospect_score=prospect, g5_score=g5,
        priority=priority,
    )


CFB_BLUESKY_SEEDS = (
    _source("rossdellenger.bsky.social", "Ross Dellenger", "Yahoo Sports", "REPORTER", ("national_reporting","breaking_news","governance","playoff","conferences","realignment","coaching","business"), reliability=5, reporting=5, breaking=5, priority=5),
    _source("slmandel.bsky.social", "Stewart Mandel", "The Athletic", "REPORTER_ANALYST", ("national_reporting","analysis","playoff","rankings","conference","features"), reliability=5, reporting=4, analysis=5, priority=5),
    _source("chrisvannini.com", "Chris Vannini", "The Athletic", "REPORTER", ("national_reporting","G5","coaching","conference","playoff","emerging_teams"), reliability=5, reporting=5, g5=5, priority=5),
    _source("scottdochterman.bsky.social", "Scott Dochterman", "The Athletic", "REPORTER", ("national_reporting","Big_Ten","team_analysis","conference"), conferences=("Big Ten",), reliability=5, reporting=5, priority=4),
    _source("davidubben.bsky.social", "David Ubben", "The Athletic", "REPORTER", ("national_reporting","features","team_analysis","culture"), reliability=5, reporting=4, analysis=4, priority=4),
    _source("sethemerson.bsky.social", "Seth Emerson", "The Athletic", "BEAT_REPORTER", ("SEC","Georgia","national_reporting","team_reporting"), conferences=("SEC",), teams=("Georgia",), reliability=5, reporting=5, priority=4),
    _source("brettmcmurphy.bsky.social", "Brett McMurphy", "On3", "INSIDER", ("breaking_news","bowls","playoff","coaching","schedules","conference"), reliability=5, reporting=5, breaking=5, priority=5),
    _source("danwolken.bsky.social", "Dan Wolken", "Yahoo Sports", "REPORTER_ANALYST", ("national_reporting","governance","playoff","coaching","opinion_analysis"), reliability=4, reporting=4, analysis=4, priority=4),
    _source("stevengodfrey.bsky.social", "Steven Godfrey", "Yahoo Sports / College Football Enquirer", "REPORTER_ANALYST", ("national_reporting","analysis","coaching","program_context","culture"), reliability=4, reporting=4, analysis=5, priority=4),
    _source("dennisdoddcbs.bsky.social", "Dennis Dodd", "CBS Sports", "REPORTER", ("national_reporting","governance","conference","realignment","playoff","breaking_news"), reliability=5, reporting=5, breaking=4, priority=4),
    _source("tomfornelli.bsky.social", "Tom Fornelli", "CBS Sports", "ANALYST", ("national_analysis","betting_context","rankings","team_analysis","Big_Ten","game_preview"), conferences=("Big Ten",), reliability=4, analysis=5, priority=4),
    _source("espnbillc.bsky.social", "Bill Connelly", "ESPN", "ANALYST", ("analytics","SP+","projections","returning_production","team_strength","conference_previews","G5","matchup_analysis"), reliability=5, analysis=5, g5=5, priority=5),
    _source("cfbfilmroom.bsky.social", "CFB Film Room", None, "ANALYST", ("analytics","advanced_stats","matchup_analysis","player_analysis","scheme","efficiency"), reliability=4, analysis=5, prospect=4, priority=5),
    _source("sec-statcat.bsky.social", "SEC StatCat", "On3 / SECStatCat", "ANALYST", ("SEC","analytics","scheme","transfers","player_impact","matchup_analysis"), conferences=("SEC",), reliability=4, analysis=5, prospect=4, priority=5),
    _source("cfbtracker.bsky.social", "CFB Tracker", None, "DATA", ("statistics","graphics","rankings","data"), reliability=3, analysis=3, priority=3),
    _source("torchfootball.bsky.social", "Torch Football", None, "DATA_ANALYST", ("data","maps","roster_geography","college_football_analysis"), reliability=3, analysis=4, priority=3),
    _source("collegefootballdata.com", "CollegeFootballData", "CFBD", "DEVELOPER_SOURCE", ("data","CFBD","API","statistics","models","developer_source"), reliability=5, reporting=2, analysis=4, priority=4),
    _source("puntandrally.bsky.social", "Punt & Rally", None, "ANALYST", ("analytics","team_previews","matchup_analysis","team_analysis"), reliability=3, analysis=4, priority=3),
    _source("shelwick.bsky.social", "Steve Helwick", None, "REPORTER", ("G5","AAC","MAC","CUSA","Big_12","Houston","Rice","team_reporting","emerging_teams"), conferences=("American Athletic","Mid-American","Conference USA","Big 12"), teams=("Houston","Rice"), reliability=5, reporting=5, g5=5, priority=5),
    _source("roadtocfb.bsky.social", "Road to CFB", "Action Network contributor", "ANALYST", ("national_CFB","G5","stadiums","regional_CFB","team_context","game_analysis"), reliability=3, analysis=4, g5=5, priority=4),
    _source("offtackleempire.bsky.social", "Off Tackle Empire", None, "COMMUNITY_ANALYSIS", ("Big_Ten","team_coverage","regional_analysis","community_analysis"), conferences=("Big Ten",), reliability=3, analysis=3, priority=3),
    _source("skhanjr.bsky.social", "Sam Khan Jr.", "The Athletic", "REPORTER", ("transfer_portal","roster_management","recruiting","NIL","revenue_sharing","personnel"), reliability=5, reporting=5, prospect=4, priority=5),
    _source("ariwasserman.bsky.social", "Ari Wasserman", "On3", "REPORTER_ANALYST", ("recruiting","roster_talent","national_CFB","team_building","blue_chip_ratio"), reliability=5, reporting=4, analysis=4, prospect=5, priority=5),
    _source("charlespower.bsky.social", "Charles Power", "On3", "SCOUT", ("recruiting","scouting","rankings","prospect_evaluation","high_school","future_players"), reliability=4, analysis=4, prospect=5, priority=4),
    _source("theucreport.bsky.social", "UCReport", None, "SCOUT", ("recruiting","prospects","ESPN300","high_school","scouting"), reliability=3, prospect=4, priority=3),
    _source("on3.com", "On3", "On3", "OUTLET", ("national_CFB","recruiting","portal","prospects","news"), reliability=4, reporting=3, prospect=4, priority=4),
    _source("ryanmccrystal.bsky.social", "Ryan McCrystal", "Sharp Football Analysis", "DRAFT_ANALYST", ("NFL_Draft","college_players","analytics","prospects","player_evaluation"), reliability=4, analysis=4, prospect=5, priority=5),
    _source("acosta32jp.bsky.social", "JP Acosta", "CBS Sports", "ANALYST", ("football_analysis","scheme","player_evaluation","NFL","CFB"), reliability=4, analysis=4, prospect=4, priority=4),
    _source("michiganrivals.bsky.social", "Maize & Blue Review", None, "TEAM_OUTLET", ("Michigan","Big_Ten","team_reporting","recruiting"), conferences=("Big Ten",), teams=("Michigan",), reliability=4, reporting=4, priority=4),
    _source("kennyjordan.bsky.social", "Kenny Jordan", "SpartanMag / On3", "BEAT_REPORTER", ("Michigan_State","Big_Ten","team_reporting","recruiting"), conferences=("Big Ten",), teams=("Michigan State",), reliability=4, reporting=4, priority=4),
    _source("ncstaterivals.bsky.social", "NC State On3/Rivals", None, "TEAM_OUTLET", ("NC_State","ACC","team_reporting","recruiting"), conferences=("ACC",), teams=("NC State",), reliability=4, reporting=4, priority=4),
    _source("olemissfb.bsky.social", "Ole Miss Football", "Ole Miss", "OFFICIAL_TEAM", ("Ole_Miss","SEC","roster","game_day","awards","official_updates"), conferences=("SEC",), teams=("Ole Miss",), reliability=5, reporting=5, analysis=1, priority=4),
    _source("espn.com", "ESPN", "ESPN", "OUTLET", ("national_CFB","news"), reliability=4, priority=3),
    _source("cbssports.bsky.social", "CBS Sports", "CBS Sports", "OUTLET", ("national_CFB","news"), reliability=4, priority=3),
    _source("theathletic.com", "The Athletic", "The Athletic", "OUTLET", ("national_CFB","news","analysis"), reliability=4, priority=3),
    _source("collegefootballcfn.bsky.social", "College Football News", None, "ANALYSIS_OUTLET", ("predictions","previews","rankings","analysis"), reliability=3, analysis=4, priority=3),
    _source("allcfbig.bsky.social", "All College Football", None, "AGGREGATOR", ("aggregation","discovery"), reliability=2, reporting=1, priority=2),
    _source("cfbot.bsky.social", "CFBot", None, "BOT", ("game_updates","scores","automation"), reliability=3, reporting=1, analysis=1, priority=2),
    _source("cfb-data-bot.bsky.social", "College Football Data Bot", None, "BOT_DATA", ("data","automation"), reliability=2, reporting=1, analysis=2, priority=1),
    _source("realbachscore.bsky.social", "Rachel Bachman", "Wall Street Journal", "REPORTER", ("college_sports","business","conferences","economics","governance"), reliability=5, reporting=5, priority=4),
    _source("sportsmediawatch.bsky.social", "Sports Media Watch", None, "SPECIALIST", ("television","ratings","broadcasting","scheduling","media_rights"), reliability=4, analysis=4, priority=3),
)
