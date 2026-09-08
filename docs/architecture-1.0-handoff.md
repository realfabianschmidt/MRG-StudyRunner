# Shared Claude/Codex entry point

The active architecture work is on `feature/architecture-1.0` in `C:\SR-1.0`.
Read that checkout's `docs/architecture-1.0-handoff.md` first, then
`docs/architecture-1.0-umbau.md`. These files are tracked, shared local files;
neither assistant needs access to the other's private memory.

Integrated checkpoint (2026-09-08): `f43cf1f`, repairs R1-R5 complete.
Verification: 776 Python tests passed / 4 skipped, 27 JavaScript tests passed,
25 release/packaging tests passed; structure check and actual Windows fixture
bundle self-check passed. Next package: early preflight (5b).

On a different machine, use the development branch after transferring/pushing
its commits. This checkpoint was saved locally; no remote push is implied.

Before editing, check git status and claim the package in the branch's working
plan. Use separate worktrees for concurrent edits and agree on file ownership.
Update the branch handoff and commit after each completed package. Keep this
main-branch file as a pointer, not an independent progress checklist.

Preserve the existing operator changes in
`software/study_content/settings/study_config.json` and
`software/study_content/studies/Example Sensors Study.study-runner`.
