"""Ensure newly ingested local reporting is immediately usable by team pages."""

from __future__ import annotations


def news_postprocess_steps() -> list[str]:
    """Shared downstream content stages required after any local-news ingest."""
    return ["retag", "cluster", "roles", "score"]
