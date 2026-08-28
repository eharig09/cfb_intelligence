"""College-football structured data, persistence, scoring, and web views."""

from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError, CFBDRequestError
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.sync import CFBDataSync
from sports_aggregator.cfb.schedule_time_display import install_schedule_noon_display_fix

install_schedule_noon_display_fix()

__all__ = [
    "CFBDClient",
    "CFBDConfigurationError",
    "CFBDRequestError",
    "CFBDataSync",
    "CFBRepository",
]
