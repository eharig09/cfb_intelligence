import os
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import secrets
import subprocess
import sys

import click
from flask import Flask, abort, jsonify, render_template, request, session
from dotenv import load_dotenv

from sports_aggregator.cfb.refresh_window import profile_for
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.web import cfb_pages
from sports_aggregator.cfb.data_status import data_status_pages
from sports_aggregator.cfb.conference_features import (
    conference_schedule_elo,
    install_market_freshness_note,
    zap_content_url,
)
from sports_aggregator.cfb.conference_extras import (
    conference_leader_packet,
    team_schedule_elo,
)
from sports_aggregator.cfb.model_market_display import install_model_comparison_display
from sports_aggregator.cfb.wiki_context_enrichment import install_wiki_context_enrichment
from sports_aggregator.cfb.coordinator_display import install_coordinator_display
from sports_aggregator.cfb.cfbdepth_display import install_cfbdepth_display
from sports_aggregator.cfb.cfbdepth_enhancements import install_cfbdepth_enhancements
from sports_aggregator.cfb.player_game_log import player_game_log_table
from sports_aggregator.cfb.player_career_context import player_career_context
from sports_aggregator.catalog import list_leagues
from sports_aggregator.service import build_default_service
from sports_aggregator.social.registry import SourceRegistry
from sports_aggregator.social.unified import UnifiedSourceRegistry
from sports_aggregator.social.source_admin import add_or_update_source
from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.stories import StoryRepository
from sports_aggregator.cfb.repository import _logo_pair
from sports_aggregator.cfb.views import height_label
from sports_aggregator.social.roles import role_label
from sports_aggregator.tables import format_value
from sports_aggregator.page_cache import cache
from sports_aggregator.scheduled_refresh import REFRESH_PROFILES
from sports_aggregator.web import league_pages
load_dotenv()


def _env_flag(name: str, default: bool) -> bool:
    fallback = "1" if default else "0"
    return os.getenv(name, fallback).strip().lower() not in {"0", "false", "no", "off"}


def _legacy_dashboards_default() -> bool:
    return not _env_flag("RENDER", False)


def _tail_lines(path: Path, limit: int = 80) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return [line.rstrip("\n") for line in deque(handle, maxlen=limit)]
    except OSError:
        return []


def _cache_dir(app: Flask) -> str:
    configured = (os.getenv("CFB_PAGE_CACHE_DIR") or "").strip()
    if configured:
        return configured
    database = Path(os.getenv("CFB_DATABASE_PATH") or os.path.join(app.instance_path, "cfb.sqlite3"))
    return str(database.parent / "page_cache")


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    refresh_token = os.getenv("CFB_REFRESH_TOKEN", "")

    app.config.from_mapping(
        CACHE_TYPE="FileSystemCache",
        CACHE_DIR=_cache_dir(app),
        CACHE_THRESHOLD=int(os.getenv("CFB_PAGE_CACHE_THRESHOLD", "150")),
        CACHE_DEFAULT_TIMEOUT=int(os.getenv("CFB_PAGE_CACHE_SECONDS", "900")),
        REGISTER_LEGACY_DASHBOARDS=_env_flag(
            "REGISTER_LEGACY_DASHBOARDS", _legacy_dashboards_default()),
        CFB_DEFAULT_SEASON=int(os.getenv("CFB_DEFAULT_SEASON", "0")) or None,
        CFB_DISPLAY_TIMEZONE=os.getenv("CFB_DISPLAY_TIMEZONE", "America/New_York"),
        CFB_REFRESH_TOKEN=refresh_token,
        CFB_ADMIN_PIN=os.getenv("CFB_ADMIN_PIN", "").strip(),
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY", "").strip() or refresh_token or secrets.token_hex(32),
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
    app.extensions["source_registry"] = app.config.get("SOURCE_REGISTRY") or SourceRegistry(source_database_path)
    app.extensions["unified_source_registry"] = app.config.get("UNIFIED_SOURCE_REGISTRY") or UnifiedSourceRegistry(source_database_path)
    app.extensions["content_repository"] = app.config.get("CONTENT_REPOSITORY") or ContentRepository(source_database_path)
    app.extensions["story_repository"] = app.config.get("STORY_REPOSITORY") or StoryRepository(source_database_path)

    install_market_freshness_note()
    install_model_comparison_display()
    install_wiki_context_enrichment()
    install_coordinator_display(app)
    install_cfbdepth_display(app)
    install_cfbdepth_enhancements(app)

    def require_refresh_auth() -> None:
        if session.get("cfb_admin") is True:
            return
        refresh_expected = str(app.config.get("CFB_REFRESH_TOKEN") or "").strip()
        pin_expected = str(app.config.get("CFB_ADMIN_PIN") or "").strip()
        provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        valid_refresh = bool(refresh_expected and provided and secrets.compare_digest(provided, refresh_expected))
        valid_pin = bool(pin_expected and provided and secrets.compare_digest(provided, pin_expected))
        if valid_refresh or valid_pin:
            if valid_pin:
                session["cfb_admin"] = True
                session.permanent = True
            return
        if not refresh_expected and not pin_expected:
            abort(503, description="CFB_REFRESH_TOKEN or CFB_ADMIN_PIN is not configured")
        abort(401)

    @app.route("/")
    def index():
        return render_template("index.html", leagues=list_leagues(), legacy_dashboards=app.config["REGISTER_LEGACY_DASHBOARDS"])

    @app.post("/internal/cfb-admin-login")
    def cfb_admin_login():
        expected = str(app.config.get("CFB_ADMIN_PIN") or "").strip()
        if not expected:
            abort(503, description="CFB_ADMIN_PIN is not configured")
        payload = request.get_json(silent=True) or request.form
        provided = str(payload.get("pin") or "").strip()
        if not provided or not secrets.compare_digest(provided, expected):
            abort(401)
        session["cfb_admin"] = True
        session.permanent = True
        return jsonify({"status": "ok"})

    @app.post("/internal/cfb-admin-logout")
    def cfb_admin_logout():
        session.pop("cfb_admin", None)
        return jsonify({"status": "ok"})

    @app.get("/college-football/source-admin/")
    def cfb_source_admin():
        return render_template("cfb_source_admin.html")

    @app.post("/internal/cfb-source-seed")
    def cfb_source_seed():
        require_refresh_auth()
        payload = request.get_json(silent=True) or request.form
        try:
            result = add_or_update_source(
                app.extensions["unified_source_registry"],
                app.extensions["source_registry"],
                dict(payload),
            )
        except (TypeError, ValueError) as exc:
            abort(400, description=str(exc))
        cache.clear()
        return jsonify({"status": "seeded", **result})

    @app.post("/internal/cfb-refresh")
    def start_cfb_refresh():
        require_refresh_auth()
        profile = (request.args.get("profile") or "light").strip().casefold()
        season = app.config.get("CFB_DEFAULT_SEASON") or datetime.now().year
        decision = None
        if profile == "auto":
            decision = profile_for(app.extensions["cfb_repository"], season=season)
            profile = decision["profile"]
            if profile is None:
                return jsonify({"status": "skipped", "season": season, **decision}), 200
        elif profile not in REFRESH_PROFILES:
            abort(400, description="profile must be auto or one of " + ", ".join(sorted(REFRESH_PROFILES)))

        root = Path(__file__).resolve().parent
        subprocess.Popen(
            [sys.executable, "-m", "sports_aggregator.tracked_refresh", "--season", str(season), "--profile", profile],
            cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        )
        return jsonify({"status": "accepted", "season": season, "profile": profile,
                        **({"reason": decision["reason"], "games": decision["games"]} if decision else {})}), 202

    @app.post("/internal/cfb-content-zap")
    def cfb_content_zap():
        require_refresh_auth()
        payload = request.get_json(silent=True) or request.form
        target = str(payload.get("url") or "").strip()
        try:
            result = zap_content_url(app.extensions["content_repository"], target)
        except ValueError as exc:
            abort(400, description=str(exc))
        cache.clear()
        return jsonify({"status": "zapped", **result})

    @app.get("/internal/cfb-refresh-status")
    def cfb_refresh_status():
        require_refresh_auth()
        database = Path(app.config["CFB_DATABASE_PATH"])
        instance = database.parent
        logs = instance / "refresh_logs"
        candidates = sorted(logs.glob("refresh-*.log"), key=lambda path: path.stat().st_mtime, reverse=True) if logs.exists() else []
        latest_log = candidates[0] if candidates else None
        history_path = instance / "scheduled_refresh_history.jsonl"
        history_lines = _tail_lines(history_path, 1)
        last_refresh = None
        if history_lines:
            try:
                last_refresh = json.loads(history_lines[-1])
            except json.JSONDecodeError:
                last_refresh = {"status": "unreadable_history_record"}
        lock_path = instance / "scheduled_refresh.lock"
        lock = None
        if lock_path.exists():
            try:
                lock = json.loads(lock_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                lock = {"present": True}
        return jsonify({
            "running": lock_path.exists(), "lock": lock, "last_refresh": last_refresh,
            "latest_log": str(latest_log) if latest_log else None,
            "latest_log_lines": _tail_lines(latest_log, 100) if latest_log else [],
        })

    app.jinja_env.filters["cell"] = format_value
    app.jinja_env.filters["height"] = height_label
    app.jinja_env.filters["role"] = role_label
    app.jinja_env.filters["logo_pair"] = _logo_pair
    app.jinja_env.globals["conference_elo_summary"] = lambda conference, season: conference_schedule_elo(
        app.extensions["cfb_repository"], conference, season
    )
    app.jinja_env.globals["conference_leader_packet"] = lambda conference, season: conference_leader_packet(
        app.extensions["cfb_repository"], conference, season
    )
    app.jinja_env.globals["team_elo_summary"] = lambda team_id, season: team_schedule_elo(
        app.extensions["cfb_repository"], int(team_id), int(season)
    )
    app.jinja_env.globals["player_game_log"] = lambda player, season: player_game_log_table(
        app.extensions["cfb_repository"], player, int(season)
    )
    app.jinja_env.globals["player_career_context"] = lambda player: player_career_context(
        app.extensions["cfb_repository"], player
    )

    app.register_blueprint(league_pages)
    app.register_blueprint(cfb_pages)
    app.register_blueprint(data_status_pages)

    @app.cli.command("sync-cfb")
    @click.option("--year", type=int, default=lambda: app.config.get("CFB_DEFAULT_SEASON") or datetime.now().year)
    @click.option("--force", is_flag=True, help="Bypass cached CFBD responses.")
    @click.option("--basic", is_flag=True, help="Skip advanced stats and CORE ratings.")
    def sync_cfb(year: int, force: bool, basic: bool) -> None:
        from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError
        from sports_aggregator.cfb.sync import CFBDataSync
        client = CFBDClient(raw_cache_path=app.config["CFBD_RAW_CACHE_PATH"])
        if not client.configured:
            raise click.ClickException(str(CFBDConfigurationError("CFBD_API_KEY is required")))
        report = CFBDataSync(client, app.extensions["cfb_repository"]).sync(year, force=force, include_advanced=not basic)
        for dataset in report.datasets:
            click.echo(f"{dataset.dataset}: {dataset.status} ({dataset.count})")
        if not report.succeeded:
            raise click.ClickException("One or more CFBD datasets failed; inspect application logs.")

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
