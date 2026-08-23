import os
from datetime import datetime

import click
from flask import Flask, render_template
from flask_caching import Cache
from dotenv import load_dotenv

from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.web import cfb_pages
from sports_aggregator.catalog import list_leagues
from sports_aggregator.service import build_default_service
from sports_aggregator.social.registry import SourceRegistry
from sports_aggregator.social.unified import UnifiedSourceRegistry
from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.stories import StoryRepository
from sports_aggregator.cfb.views import height_label
from sports_aggregator.tables import format_value
from sports_aggregator.web import league_pages

cache = Cache()
load_dotenv()


def _env_flag(name: str, default: bool) -> bool:
    fallback = "1" if default else "0"
    return os.getenv(name, fallback).strip().lower() not in {"0", "false", "no", "off"}


def create_app(test_config: dict | None = None) -> Flask:
    """Application factory used by local, production, and test entry points."""
    app = Flask(__name__)

    app.config.from_mapping(
        CACHE_TYPE="flask_caching.backends.simplecache.SimpleCache",
        CACHE_DEFAULT_TIMEOUT=3600,
        REGISTER_LEGACY_DASHBOARDS=_env_flag("REGISTER_LEGACY_DASHBOARDS", True),
        CFB_DEFAULT_SEASON=int(os.getenv("CFB_DEFAULT_SEASON", "0")) or None,
        CFB_DISPLAY_TIMEZONE=os.getenv("CFB_DISPLAY_TIMEZONE", "America/New_York"),
        CFB_DATABASE_PATH=os.getenv(
            "CFB_DATABASE_PATH", os.path.join(app.instance_path, "cfb.sqlite3")
        ),
        CFBD_RAW_CACHE_PATH=os.getenv(
            "CFBD_RAW_CACHE_PATH", os.path.join(app.instance_path, "cfbd_raw")
        ),
    )
    if test_config:
        app.config.update(test_config)

    cache.init_app(app)
    app.extensions["league_aggregation_service"] = app.config.get(
        "LEAGUE_AGGREGATION_SERVICE"
    ) or build_default_service()
    app.extensions["cfb_repository"] = app.config.get("CFB_REPOSITORY") or CFBRepository(
        app.config["CFB_DATABASE_PATH"]
    )
    source_database_path = app.extensions["cfb_repository"].path
    app.extensions["source_registry"] = app.config.get("SOURCE_REGISTRY") or SourceRegistry(
        source_database_path
    )
    app.extensions["unified_source_registry"] = app.config.get(
        "UNIFIED_SOURCE_REGISTRY"
    ) or UnifiedSourceRegistry(source_database_path)
    app.extensions["content_repository"] = app.config.get("CONTENT_REPOSITORY") or ContentRepository(
        source_database_path
    )
    app.extensions["story_repository"] = app.config.get("STORY_REPOSITORY") or StoryRepository(
        source_database_path
    )

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            leagues=list_leagues(),
            legacy_dashboards=app.config["REGISTER_LEGACY_DASHBOARDS"],
        )

    # One number formatter for every template, so the same statistic cannot
    # render as 0.686, 68.6% and 0.7% on three different pages.
    app.jinja_env.filters["cell"] = format_value
    app.jinja_env.filters["height"] = height_label

    app.register_blueprint(league_pages)
    app.register_blueprint(cfb_pages)

    @app.cli.command("sync-cfb")
    @click.option(
        "--year",
        type=int,
        default=lambda: app.config.get("CFB_DEFAULT_SEASON") or datetime.now().year,
    )
    @click.option("--force", is_flag=True, help="Bypass cached CFBD responses.")
    @click.option("--basic", is_flag=True, help="Skip advanced stats and CORE ratings.")
    def sync_cfb(year: int, force: bool, basic: bool) -> None:
        """Synchronize the college-football structured data store."""
        from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError
        from sports_aggregator.cfb.sync import CFBDataSync

        client = CFBDClient(raw_cache_path=app.config["CFBD_RAW_CACHE_PATH"])
        if not client.configured:
            raise click.ClickException(str(CFBDConfigurationError("CFBD_API_KEY is required")))
        report = CFBDataSync(client, app.extensions["cfb_repository"]).sync(
            year, force=force, include_advanced=not basic
        )
        for dataset in report.datasets:
            click.echo(f"{dataset.dataset}: {dataset.status} ({dataset.count})")
        if not report.succeeded:
            raise click.ClickException("One or more CFBD datasets failed; inspect application logs.")

    # These dashboards predate the generic aggregation layer and import large
    # analytics dependencies. Keep their URLs stable while allowing lightweight
    # processes and tests to opt out during the migration.
    if app.config["REGISTER_LEGACY_DASHBOARDS"]:
        from blueprints.bengals import bengals
        from reds.reds import reds

        app.register_blueprint(reds, url_prefix="/reds")
        app.register_blueprint(bengals, url_prefix="/bengals")
    return app


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "1").strip().lower() not in {"0", "false", "no"}
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    create_app().run(debug=debug, host=host, port=port)
