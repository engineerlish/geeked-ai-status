"""`python -m collector` — take one snapshot and write it into the repository checkout."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .history import read_latest, write_index, write_snapshot
from .schema import load_schema, validate
from .slots import parse
from .snapshot import build_snapshot, commit_message, failure_snapshot, load_config


def run(root: Path, config_path: Path, now: datetime, env: dict[str, str], message_file: Path | None) -> int:
    providers_config = load_config(config_path)
    schema = load_schema()

    previous = read_latest(root)
    if previous is not None and validate(previous, schema):
        # A corrupt or old-format latest.json is not a reason to skip the slot.
        print("warning: latest.json does not match the schema; treating the run as a first snapshot", file=sys.stderr)
        previous = None

    trigger = env.get("GITHUB_EVENT_NAME") or "local"
    if trigger not in {"schedule", "workflow_dispatch"}:
        trigger = "local"
    run_id = env.get("GITHUB_RUN_ID") or None

    snapshot = build_snapshot(
        providers_config, now=now, env=env, previous=previous, trigger=trigger, run_id=run_id
    )
    errors = validate(snapshot, schema)
    if errors:
        print("snapshot failed validation, recording the failure instead:", file=sys.stderr)
        for error in errors[:20]:
            print(f"  - {error}", file=sys.stderr)
        snapshot = failure_snapshot(
            providers_config, now=now, previous=previous, errors=errors, trigger=trigger, run_id=run_id
        )
        fallback_errors = validate(snapshot, schema)
        if fallback_errors:  # cannot happen unless the schema itself is broken; fail loudly
            print("the failure snapshot is invalid too:", *fallback_errors[:10], sep="\n  - ", file=sys.stderr)
            return 1

    path = write_snapshot(root, snapshot)
    write_index(root, snapshot, path)

    message = commit_message(snapshot)
    if message_file is not None:
        message_file.write_text(message + "\n", encoding="utf-8", newline="\n")
    print(message)
    print(f"wrote {path.relative_to(root).as_posix()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collector", description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository checkout to write into")
    parser.add_argument("--config", type=Path, default=None, help="provider list (default: providers.json in root)")
    parser.add_argument("--now", default=None, help="override the clock, e.g. 2026-10-15T18:00:00Z (testing)")
    parser.add_argument("--message-file", type=Path, default=None, help="write the commit message here")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    now = parse(args.now) if args.now else datetime.now(timezone.utc)
    return run(root, args.config or root / "providers.json", now, dict(os.environ), args.message_file)


if __name__ == "__main__":
    raise SystemExit(main())
