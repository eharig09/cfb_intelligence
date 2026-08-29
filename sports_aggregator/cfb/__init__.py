"""College-football structured data, persistence, scoring, and web views."""

from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError, CFBDRequestError
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.sync import CFBDataSync
from sports_aggregator.cfb.schedule_time_display import install_schedule_noon_display_fix
from sports_aggregator.cfb.leader_scope import install_team_leader_scope_guard

install_schedule_noon_display_fix()
install_team_leader_scope_guard(CFBRepository)

__all__ = [
    "CFBDClient",
    "CFBDConfigurationError",
    "CFBDRequestError",
    "CFBDataSync",
    "CFBRepository",
]
