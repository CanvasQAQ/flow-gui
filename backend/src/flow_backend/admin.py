from pathlib import Path
import argparse
import json
import uuid

from .config import Settings
from .db import migrate
from .selection import parse_number_ranges
from .services.rollback import RollbackService


def main() -> None:
    parser = argparse.ArgumentParser(description="FlowPilot backend administration")
    parser.add_argument("--database", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    rollback = commands.add_parser("rollback", help="Preview or apply an algorithm decision rollback")
    rollback.add_argument("--run", required=True)
    rollback.add_argument("--corners", required=True, help="For example: 1,2,5-10")
    rollback.add_argument("--stage", required=True)
    rollback.add_argument("--execute", action="store_true")
    rollback.add_argument("--idempotency-key")
    args = parser.parse_args()

    settings = Settings.from_environment()
    database = args.database or settings.database_path
    migrate(database)
    service = RollbackService(database)
    corners = parse_number_ranges(args.corners)
    if args.execute:
        result, replayed = service.execute(
            args.idempotency_key or f"admin-{uuid.uuid4().hex}",
            args.run, corners, args.stage,
        )
        print(json.dumps({**result, "replayed": replayed}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(service.preview(args.run, corners, args.stage), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

