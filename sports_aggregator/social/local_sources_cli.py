"""CLI for reproducible nationwide local-source research."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from sports_aggregator.social.local_sources import (
    enrich_machine_endpoints,
    import_source_graph,
    research_registry,
    write_deliverables,
)


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("research", "enrich", "render", "import"))
    parser.add_argument("--database", default=os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    parser.add_argument("--output-dir", default="data/local_sources")
    parser.add_argument("--classification", choices=("fbs", "fcs"), default="fbs")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--sources-per-team", type=int, default=3)
    parser.add_argument("--max-workers", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--discover-native", action="store_true")
    args = parser.parse_args(argv)
    registry_path = Path(args.output_dir) / "cfb_local_source_registry.json"
    if args.command == "import":
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        print(json.dumps(import_source_graph(registry, args.database), indent=2))
        return 0
    if args.command == "enrich":
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        enrich_machine_endpoints(registry, max_workers=max(args.max_workers, 8),
                                 timeout=args.timeout)
    elif args.command == "render":
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        registry = research_registry(
            args.database, classification=args.classification, days=args.days,
            source_limit=args.sources_per_team, max_workers=args.max_workers,
            timeout=args.timeout,
        )
        if args.discover_native:
            enrich_machine_endpoints(registry, max_workers=max(args.max_workers, 8),
                                     timeout=args.timeout)
    paths = write_deliverables(registry, args.output_dir)
    print(json.dumps({key: str(value) for key, value in paths.items()}, indent=2))
    return 0 if not registry["metadata"]["research_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
