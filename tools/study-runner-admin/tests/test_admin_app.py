# Study Runner Admin Development Guide

This branch contains a standalone admin application for Study Runner operations. The app intentionally does not run as a web service on port 3000 and is designed to be local-first.

## Architecture

- `study_runner_admin/cli.py`: command-line app entry point
- `study_runner_admin/gui.py`: desktop Tkinter GUI entry point
- `study_runner_admin/app.py`: shared application logic
- `study_runner_admin/core/installer.py`: install/repair/remove logic
- `study_runner_admin/core/participant_registry.py`: participant lookup, archive, delete, anonymize logic

## Why this is standalone

- No dependency on the main Study Runner server runtime
- No port binding to 3000
- No requirement for a background web service
- Works as a local desktop tool or scriptable command-line tool

## Local development

```bash
cd tools/study-runner-admin
python -m pip install -r requirements.txt
python -m pip install -e .
study-runner-admin --help
study-runner-admin-gui
```

## Testing

```bash
cd tools/study-runner-admin
python -m pytest -q
```

## Typical workflows

- Install a release: `study-runner-admin install ~/StudyRunner --version 1.7.0`
- Repair an installation: `study-runner-admin repair ~/StudyRunner`
- Generate a participant ID: `study-runner-admin participants generate --study DemoStudy --prefix P`
- List participants: `study-runner-admin participants list --study DemoStudy --data-dir ~/StudyRunner/data`
- Delete with archive: `study-runner-admin participants delete P-123 --archive-first`
- Anonymize instead of delete: `study-runner-admin participants anonymize P-123`

## File layout expectations

```text
<DATA_DIR>/
  saved_results/
  participant-registry.json
  audit/
  archive/
```

## Update notes

The desktop GUI is optional but intentionally available. The CLI remains the primary mechanism for automation and scripting.
