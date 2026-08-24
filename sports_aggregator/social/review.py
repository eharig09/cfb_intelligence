"""Human review queue and classifier measurements for CFB content.

The rules in :mod:`content`, :mod:`roles`, and :mod:`relevance` are intentionally
explainable. This module supplies the missing feedback loop: a stratified CSV
queue, durable human labels, and precision/recall reports against current rules.
"""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import closing
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.stories import _is_cfb_relevant


REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS content_review_labels (
 content_id INTEGER NOT NULL, reviewer TEXT NOT NULL,
 relevant_label INTEGER CHECK(relevant_label IN (0,1)),
 topics_label_json TEXT, role_label TEXT,
 teams_label_json TEXT, players_label_json TEXT,
 priority_label INTEGER CHECK(priority_label BETWEEN 1 AND 5),
 notes TEXT NOT NULL DEFAULT '', reviewed_at TEXT NOT NULL,
 PRIMARY KEY(content_id,reviewer),
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_content_reviews_reviewer
 ON content_review_labels(reviewer,reviewed_at DESC);
"""

REVIEW_FIELDS = (
    "content_id", "platform", "published_at", "source_name", "title", "excerpt", "url",
    "review_reason",
    "predicted_relevant", "predicted_topics", "predicted_role", "role_confidence",
    "relevance_score", "predicted_team_ids", "predicted_teams",
    "predicted_player_keys", "predicted_players",
    "label_relevant", "label_topics", "label_role", "label_team_ids",
    "label_player_keys", "label_priority", "notes", "reviewer",
)


def _safe_divide(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0


def binary_metrics(pairs: Iterable[tuple[bool, bool]]) -> dict[str, Any]:
    """Precision/recall metrics for ``(prediction, label)`` pairs."""
    values = list(pairs)
    tp = sum(predicted and expected for predicted, expected in values)
    fp = sum(predicted and not expected for predicted, expected in values)
    fn = sum(not predicted and expected for predicted, expected in values)
    tn = sum(not predicted and not expected for predicted, expected in values)
    precision = _safe_divide(tp, tp + fp)
    recall = _safe_divide(tp, tp + fn)
    return {
        "count": len(values), "accuracy": _safe_divide(tp + tn, len(values)),
        "precision": precision, "recall": recall,
        "f1": _safe_divide(2 * precision * recall, precision + recall),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def multilabel_metrics(
    pairs: Iterable[tuple[set[Any], set[Any]]]
) -> dict[str, Any]:
    """Micro precision/recall plus exact-match accuracy for set-valued labels."""
    values = list(pairs)
    tp = sum(len(predicted & expected) for predicted, expected in values)
    fp = sum(len(predicted - expected) for predicted, expected in values)
    fn = sum(len(expected - predicted) for predicted, expected in values)
    precision = _safe_divide(tp, tp + fp)
    recall = _safe_divide(tp, tp + fn)
    return {
        "count": len(values),
        "exact_match": _safe_divide(
            sum(predicted == expected for predicted, expected in values), len(values)),
        "precision": precision, "recall": recall,
        "f1": _safe_divide(2 * precision * recall, precision + recall),
        "tp": tp, "fp": fp, "fn": fn,
    }


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        rank = (cursor + 1 + end) / 2
        for index in order[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def spearman(pairs: Iterable[tuple[float, float]]) -> float | None:
    """Spearman rank correlation without a heavy statistics dependency."""
    values = list(pairs)
    if len(values) < 2:
        return None
    left = _average_ranks([pair[0] for pair in values])
    right = _average_ranks([pair[1] for pair in values])
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_scale = sum((x - left_mean) ** 2 for x in left)
    right_scale = sum((y - right_mean) ** 2 for y in right)
    if not left_scale or not right_scale:
        return None
    return round(numerator / (left_scale * right_scale) ** 0.5, 3)


def _decode_label(value: str | None, *, integers: bool = False) -> list[Any] | None:
    """Blank means not reviewed; ``NONE`` records an explicitly empty set."""
    text = (value or "").strip()
    if not text:
        return None
    if text.casefold() == "none":
        return []
    parts = [part.strip() for part in text.replace(";", "|").split("|") if part.strip()]
    if integers:
        return sorted({int(part) for part in parts})
    return sorted(set(parts))


class ContentReviewRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        ContentRepository(self.path).initialize()
        with closing(self._connect()) as connection:
            connection.executescript(REVIEW_SCHEMA)

    def _predictions(self) -> list[dict[str, Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT c.*,COALESCE(e.name,c.publisher_name,c.author_name,'Source') source_name,
                   COALESCE(cr.role,c.source_role) predicted_role,
                   COALESCE(cr.confidence,0.0) role_confidence,
                   COALESCE(rel.score,0.0) relevance_score
                   FROM content_items c LEFT JOIN source_entities e USING(source_entity_id)
                   LEFT JOIN content_roles cr USING(content_id)
                   LEFT JOIN content_relevance rel USING(content_id)
                   ORDER BY c.published_at DESC,c.content_id DESC"""
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                content_id = item["content_id"]
                topics = {inner[0] for inner in connection.execute(
                    "SELECT topic FROM content_topics WHERE content_id=?", (content_id,))}
                teams = [dict(inner) for inner in connection.execute(
                    """SELECT ct.team_id,t.school,ct.confidence FROM content_teams ct
                       LEFT JOIN teams t USING(team_id) WHERE ct.content_id=?
                       ORDER BY ct.confidence DESC,t.school""", (content_id,))]
                players = [dict(inner) for inner in connection.execute(
                    """SELECT cp.season,cp.player_id,
                       TRIM(COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,'')) name,
                       cp.confidence FROM content_players cp LEFT JOIN players p
                       ON p.season=cp.season AND p.player_id=cp.player_id
                       WHERE cp.content_id=? ORDER BY cp.confidence DESC,name""", (content_id,))]
                games = {inner[0] for inner in connection.execute(
                    "SELECT game_id FROM content_games WHERE content_id=? AND game_match_score>=0.75",
                    (content_id,))}
                relevance_item = {
                    **item,
                    "topics": topics,
                    "teams": {team["team_id"] for team in teams if team["confidence"] >= 0.75},
                    "players": {(player["season"], player["player_id"]) for player in players
                                if player["confidence"] >= 0.75},
                    "games": games,
                }
                text = " ".join(filter(None, (item["title"], item["body_text"], item["summary"])))
                result.append({
                    **item,
                    "predicted_relevant": _is_cfb_relevant(relevance_item),
                    "topics": topics, "teams": teams, "players": players, "games": games,
                    "excerpt": " ".join(text.split())[:800],
                    "url": item.get("original_url") or item.get("canonical_url") or "",
                })
        return result

    @staticmethod
    def _triage_reasons(item: dict[str, Any]) -> list[str]:
        """Name high-value exceptions instead of asking editors to read everything."""
        reasons = []
        relevant = bool(item["predicted_relevant"])
        if relevant and float(item.get("role_confidence") or 0) < 0.72:
            reasons.append("low role confidence")
        if relevant and not item["topics"]:
            reasons.append("relevant but unclassified")
        if relevant and not item["teams"] and not item["players"] and not item["games"]:
            reasons.append("relevant with no entity scope")
        if any(0.55 <= float(team["confidence"]) < 0.90 for team in item["teams"]):
            reasons.append("borderline team link")
        if any(0.55 <= float(player["confidence"]) < 0.90 for player in item["players"]):
            reasons.append("borderline player link")
        score = float(item.get("relevance_score") or 0)
        if 35 <= score <= 65:
            reasons.append("ranking boundary")
        if not relevant and (item["topics"] or item["teams"] or item["players"]):
            reasons.append("relevance/entity disagreement")
        return reasons

    def review_queue(self, limit: int = 75, reviewer: str = "local",
                     mode: str = "triage") -> list[dict[str, Any]]:
        """Return unreviewed exceptions, or a broad stratified audit sample."""
        if mode not in {"triage", "stratified"}:
            raise ValueError("mode must be 'triage' or 'stratified'")
        self.initialize()
        with closing(self._connect()) as connection:
            reviewed = {row[0] for row in connection.execute(
                "SELECT content_id FROM content_review_labels WHERE reviewer=?", (reviewer,))}
        buckets: dict[tuple[str, str, str], deque[dict[str, Any]]] = defaultdict(deque)
        for item in self._predictions():
            if item["content_id"] in reviewed:
                continue
            reasons = self._triage_reasons(item)
            if mode == "triage" and not reasons:
                continue
            item = {**item, "review_reason": " | ".join(reasons) or "stratified audit"}
            topic = sorted(item["topics"])[0] if item["topics"] else "UNCLASSIFIED"
            buckets[(item["platform"], item["predicted_role"], topic)].append(item)
        selected = []
        keys = sorted(buckets)
        while keys and len(selected) < max(0, limit):
            remaining = []
            for key in keys:
                if buckets[key] and len(selected) < limit:
                    selected.append(buckets[key].popleft())
                if buckets[key]:
                    remaining.append(key)
            keys = remaining
        return selected

    @staticmethod
    def _csv_row(item: dict[str, Any], reviewer: str) -> dict[str, Any]:
        return {
            "content_id": item["content_id"], "platform": item["platform"],
            "published_at": item["published_at"], "source_name": item["source_name"],
            "title": item["title"], "excerpt": item["excerpt"], "url": item["url"],
            "review_reason": item.get("review_reason") or "stratified audit",
            "predicted_relevant": int(item["predicted_relevant"]),
            "predicted_topics": "|".join(sorted(item["topics"])),
            "predicted_role": item["predicted_role"],
            "role_confidence": item["role_confidence"],
            "relevance_score": item["relevance_score"],
            "predicted_team_ids": "|".join(str(team["team_id"]) for team in item["teams"]),
            "predicted_teams": "|".join(
                f"{team['school'] or team['team_id']} ({team['confidence']:.2f})" for team in item["teams"]),
            "predicted_player_keys": "|".join(
                f"{player['season']}:{player['player_id']}" for player in item["players"]),
            "predicted_players": "|".join(
                f"{player['name'] or player['player_id']} ({player['confidence']:.2f})"
                for player in item["players"]),
            "label_relevant": "", "label_topics": "", "label_role": "",
            "label_team_ids": "", "label_player_keys": "", "label_priority": "",
            "notes": "", "reviewer": reviewer,
        }

    def export_csv(self, output: str | Path, limit: int = 75,
                   reviewer: str = "local", mode: str = "triage") -> dict[str, Any]:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [self._csv_row(item, reviewer)
                for item in self.review_queue(limit, reviewer, mode)]
        with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return {"exported": len(rows), "mode": mode, "output": str(output_path)}

    def import_csv(self, source: str | Path, reviewer: str | None = None) -> dict[str, Any]:
        self.initialize()
        saved = skipped = 0
        with Path(source).open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            valid_ids = {row[0] for row in connection.execute("SELECT content_id FROM content_items")}
            for row in rows:
                content_id = int(row["content_id"])
                if content_id not in valid_ids:
                    raise ValueError(f"unknown content_id {content_id}")
                relevant_text = (row.get("label_relevant") or "").strip().casefold()
                relevant = None
                if relevant_text:
                    if relevant_text not in {"0", "1", "false", "true", "no", "yes"}:
                        raise ValueError(f"invalid relevance label for content_id {content_id}")
                    relevant = int(relevant_text in {"1", "true", "yes"})
                topics = _decode_label(row.get("label_topics"))
                role = (row.get("label_role") or "").strip() or None
                teams = _decode_label(row.get("label_team_ids"), integers=True)
                players = _decode_label(row.get("label_player_keys"))
                priority_text = (row.get("label_priority") or "").strip()
                priority = int(priority_text) if priority_text else None
                if priority is not None and priority not in range(1, 6):
                    raise ValueError(f"priority must be 1-5 for content_id {content_id}")
                if all(value is None for value in (relevant, topics, role, teams, players, priority)):
                    skipped += 1
                    continue
                label_reviewer = reviewer or (row.get("reviewer") or "local").strip() or "local"
                connection.execute(
                    """INSERT INTO content_review_labels VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(content_id,reviewer) DO UPDATE SET
                       relevant_label=excluded.relevant_label,
                       topics_label_json=excluded.topics_label_json,
                       role_label=excluded.role_label,teams_label_json=excluded.teams_label_json,
                       players_label_json=excluded.players_label_json,
                       priority_label=excluded.priority_label,notes=excluded.notes,
                       reviewed_at=excluded.reviewed_at""",
                    (content_id, label_reviewer, relevant,
                     json.dumps(topics) if topics is not None else None, role,
                     json.dumps(teams) if teams is not None else None,
                     json.dumps(players) if players is not None else None,
                     priority, (row.get("notes") or "").strip(), now),
                )
                saved += 1
            connection.commit()
        return {"saved": saved, "skipped": skipped, "source": str(source)}

    def report(self, reviewer: str | None = None) -> dict[str, Any]:
        """Compare saved human labels with the classifiers as they run now."""
        predictions = {item["content_id"]: item for item in self._predictions()}
        self.initialize()
        sql = "SELECT * FROM content_review_labels"
        params: tuple[Any, ...] = ()
        if reviewer:
            sql += " WHERE reviewer=?"
            params = (reviewer,)
        with closing(self._connect()) as connection:
            labels = [dict(row) for row in connection.execute(sql, params)]
        relevance_pairs = []
        topic_pairs = []
        role_pairs = []
        team_pairs_75 = []
        team_pairs_90 = []
        player_pairs_75 = []
        priority_pairs = []
        for label in labels:
            predicted = predictions.get(label["content_id"])
            if not predicted:
                continue
            if label["relevant_label"] is not None:
                relevance_pairs.append((predicted["predicted_relevant"], bool(label["relevant_label"])))
            if label["topics_label_json"] is not None:
                topic_pairs.append((set(predicted["topics"]), set(json.loads(label["topics_label_json"]))))
            if label["role_label"] is not None:
                role_pairs.append((predicted["predicted_role"], label["role_label"]))
            if label["teams_label_json"] is not None:
                expected = set(json.loads(label["teams_label_json"]))
                team_pairs_75.append((
                    {team["team_id"] for team in predicted["teams"] if team["confidence"] >= 0.75}, expected))
                team_pairs_90.append((
                    {team["team_id"] for team in predicted["teams"] if team["confidence"] >= 0.90}, expected))
            if label["players_label_json"] is not None:
                expected = set(json.loads(label["players_label_json"]))
                player_pairs_75.append((
                    {f"{player['season']}:{player['player_id']}" for player in predicted["players"]
                     if player["confidence"] >= 0.75}, expected))
            if label["priority_label"] is not None:
                priority_pairs.append((predicted["relevance_score"], label["priority_label"]))
        role_correct = sum(predicted == expected for predicted, expected in role_pairs)
        confusion: dict[str, int] = defaultdict(int)
        for predicted, expected in role_pairs:
            if predicted != expected:
                confusion[f"{expected} -> {predicted}"] += 1
        return {
            "reviews": len(labels),
            "relevance": binary_metrics(relevance_pairs),
            "topics": multilabel_metrics(topic_pairs),
            "roles": {
                "count": len(role_pairs),
                "accuracy": _safe_divide(role_correct, len(role_pairs)),
                "confusion": dict(sorted(confusion.items(), key=lambda pair: -pair[1])),
            },
            "teams_at_0_75": multilabel_metrics(team_pairs_75),
            "teams_at_0_90": multilabel_metrics(team_pairs_90),
            "players_at_0_75": multilabel_metrics(player_pairs_75),
            "relevance_priority": {
                "count": len(priority_pairs), "spearman": spearman(priority_pairs),
            },
        }
