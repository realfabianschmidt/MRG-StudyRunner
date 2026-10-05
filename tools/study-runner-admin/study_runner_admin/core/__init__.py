from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

from study_runner_admin.core.installer import (
    install_release,
    list_available_releases,
    repair_installation,
    remove_installation,
)
from study_runner_admin.core.participant_registry import (
    ParticipantRegistry,
    find_participant_in_results,
    generate_participant_id,
    list_participants_for_study,
)


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="study-runner-admin",
        description="Standalone helper for Study Runner install, repair, cleanup and participant management.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install or update a Study Runner directory")
    install.add_argument("install_root", type=Path, help="Target directory for the installation")
    install.add_argument("--version", default=None, help="Exact release version, for example 1.7.0 or v1.7.0")
    install.add_argument("--data-dir", type=Path, default=None, help="Optional data directory to associate")
    install.add_argument("--overwrite", action="store_true", help="Delete an existing install directory before installing")

    repair = subparsers.add_parser("repair", help="Repair an installed Study Runner directory")
    repair.add_argument("install_root", type=Path)
    repair.add_argument("--data-dir", type=Path, default=None)

    remove = subparsers.add_parser("remove", help="Delete an installed Study Runner directory")
    remove.add_argument("install_root", type=Path)
    remove.add_argument("--force", action="store_true")

    versions = subparsers.add_parser("versions", help="Show available Study Runner releases")
    versions.add_argument("--limit", type=int, default=10)

    participants = subparsers.add_parser("participants", help="Participant-ID and session management")
    p_sub = participants.add_subparsers(dest="participants_command", required=True)

    generate = p_sub.add_parser("generate", help="Generate a participant ID")
    generate.add_argument("--study", default="default-study")
    generate.add_argument("--prefix", default="P")
    generate.add_argument("--output", type=Path, default=None)

    list_cmd = p_sub.add_parser("list", help="List participant IDs in a study")
    list_cmd.add_argument("--study", required=True)
    list_cmd.add_argument("--data-dir", type=Path, default=None)

    find = p_sub.add_parser("find", help="Find participant data in the saved-results tree")
    find.add_argument("participant_id")
    find.add_argument("--data-dir", type=Path, default=None)

    delete = p_sub.add_parser("delete", help="Delete participant session data and create an audit record")
    delete.add_argument("participant_id")
    delete.add_argument("--study", default=None)
    delete.add_argument("--data-dir", type=Path, default=None)
    delete.add_argument("--reason", default="user requested deletion")
    delete.add_argument("--archive-first", action="store_true")

    status = subparsers.add_parser("status", help="Show a simple status report")
    status.add_argument("install_root", type=Path, nargs="?", default=None)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "install":
        result = install_release(
            install_root=args.install_root,
            version=args.version,
            data_dir=args.data_dir,
            overwrite=args.overwrite,
        )
        _print_json(result)
        return 0

    if args.command == "repair":
        result = repair_installation(args.install_root, data_dir=args.data_dir)
        _print_json(result)
        return 0

    if args.command == "remove":
        result = remove_installation(args.install_root, force=args.force)
        _print_json(result)
        return 0

    if args.command == "versions":
        releases = list_available_releases(limit=args.limit)
        _print_json({"releases": releases})
        return 0

    if args.command == "status":
        install_root = args.install_root.expanduser().resolve() if args.install_root else None
        payload = {"status": "ok"}
        if install_root is not None:
            payload["install_root"] = str(install_root)
            payload["exists"] = install_root.exists()
            payload["state_file_exists"] = (install_root / "study-runner-admin-state.json").exists()
        _print_json(payload)
        return 0

    if args.command == "participants":
        if args.participants_command == "generate":
            pid = generate_participant_id(study_id=args.study, prefix=args.prefix)
            payload = {"participant_id": pid, "study_id": args.study}
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(pid + "\n", encoding="utf-8")
            _print_json(payload)
            return 0

        if args.participants_command == "list":
            _print_json(list_participants_for_study(args.study, data_dir=args.data_dir))
            return 0

        if args.participants_command == "find":
            _print_json(find_participant_in_results(args.participant_id, data_dir=args.data_dir))
            return 0

        if args.participants_command == "delete":
            registry = ParticipantRegistry(data_dir=args.data_dir)
            _print_json(
                registry.delete_participant(
                    participant_id=args.participant_id,
                    study_id=args.study,
                    reason=args.reason,
                    archive_first=args.archive_first,
                )
            )
            return 0

        parser.error(f"Unsupported participant subcommand: {args.participants_command}")

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
