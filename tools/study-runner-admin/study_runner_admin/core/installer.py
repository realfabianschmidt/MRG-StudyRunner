from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from study_runner_admin.core.installer import (
    install_release,
    list_available_releases,
    list_installed_versions,
    remove_installation,
    repair_installation,
)
from study_runner_admin.core.participant_registry import (
    ParticipantRegistry,
    anonymize_participant,
    find_participant_in_results,
    generate_participant_id,
    list_participants_for_study,
)


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="study-runner-admin",
        description="Standalone helper for Study Runner installation, repair, cleanup and participant management.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install the latest Study Runner release")
    install.add_argument("install_root", type=Path)
    install.add_argument("--version", default=None, help="Exact version tag, e.g. 1.7.0 or v1.7.0")
    install.add_argument("--data-dir", type=Path, default=None)
    install.add_argument("--overwrite", action="store_true")

    install_old = subparsers.add_parser("install-old", help="Install a specific older Study Runner release")
    install_old.add_argument("install_root", type=Path)
    install_old.add_argument("--version", required=True, help="Release tag to install")
    install_old.add_argument("--data-dir", type=Path, default=None)
    install_old.add_argument("--overwrite", action="store_true")

    repair = subparsers.add_parser("repair", help="Repair a Study Runner installation")
    repair.add_argument("install_root", type=Path)
    repair.add_argument("--data-dir", type=Path, default=None)

    remove = subparsers.add_parser("remove", help="Delete a Study Runner installation")
    remove.add_argument("install_root", type=Path)
    remove.add_argument("--force", action="store_true")

    versions = subparsers.add_parser("versions", help="Show available release tags")
    versions.add_argument("--limit", type=int, default=10)

    installed = subparsers.add_parser("installed", help="List installed versions in an install root")
    installed.add_argument("install_root", type=Path)

    status = subparsers.add_parser("status", help="Show quick status information")
    status.add_argument("install_root", type=Path, nargs="?", default=None)

    participants = subparsers.add_parser("participants", help="Participant-ID related management")
    p_sub = participants.add_subparsers(dest="participants_command", required=True)

    generate = p_sub.add_parser("generate", help="Generate a participant ID")
    generate.add_argument("--prefix", default="P")
    generate.add_argument("--study", default="default-study")
    generate.add_argument("--output", type=Path, default=None)

    list_cmd = p_sub.add_parser("list", help="List participant IDs for a study")
    list_cmd.add_argument("--study", required=True)
    list_cmd.add_argument("--data-dir", type=Path, default=None)

    find = p_sub.add_parser("find", help="Find participant data in the saved-results tree")
    find.add_argument("participant_id")
    find.add_argument("--data-dir", type=Path, default=None)

    delete = p_sub.add_parser("delete", help="Delete participant data and create an archive if requested")
    delete.add_argument("participant_id")
    delete.add_argument("--study", default=None)
    delete.add_argument("--data-dir", type=Path, default=None)
    delete.add_argument("--reason", default="user requested deletion")
    delete.add_argument("--archive-first", action="store_true")

    anonymize = p_sub.add_parser("anonymize", help="Anonymize participant data instead of deleting it")
    anonymize.add_argument("participant_id")
    anonymize.add_argument("--study", default=None)
    anonymize.add_argument("--data-dir", type=Path, default=None)
    anonymize.add_argument("--reason", default="consent withdrawn")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "install":
        _print_json(
            install_release(
                install_root=args.install_root,
                version=args.version,
                data_dir=args.data_dir,
                overwrite=args.overwrite,
            )
        )
        return 0

    if args.command == "install-old":
        _print_json(
            install_release(
                install_root=args.install_root,
                version=args.version,
                data_dir=args.data_dir,
                overwrite=args.overwrite,
            )
        )
        return 0

    if args.command == "repair":
        _print_json(repair_installation(args.install_root, data_dir=args.data_dir))
        return 0

    if args.command == "remove":
        _print_json(remove_installation(args.install_root, force=args.force))
        return 0

    if args.command == "versions":
        _print_json({"releases": list_available_releases(limit=args.limit)})
        return 0

    if args.command == "installed":
        _print_json({"install_root": str(args.install_root), "installed_versions": list_installed_versions(args.install_root)})
        return 0

    if args.command == "status":
        payload = {"status": "ok"}
        if args.install_root is not None:
            root = args.install_root.expanduser().resolve()
            payload["install_root"] = str(root)
            payload["exists"] = root.exists()
            payload["state_file_exists"] = (root / "study-runner-admin-state.json").exists()
        _print_json(payload)
        return 0

    if args.command == "participants":
        if args.participants_command == "generate":
            participant_id = generate_participant_id(prefix=args.prefix)
            payload = {"participant_id": participant_id, "study_id": args.study}
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(participant_id + "\n", encoding="utf-8")
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

        if args.participants_command == "anonymize":
            _print_json(
                anonymize_participant(
                    participant_id=args.participant_id,
                    study_id=args.study,
                    data_dir=args.data_dir,
                    reason=args.reason,
                )
            )
            return 0

        parser.error(f"Unsupported participant subcommand: {args.participants_command}")

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())



















































































































































