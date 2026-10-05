# Study Runner Admin

Standalone helper for Study Runner installations, old-version setups, cleanup and participant management.

This tool is intentionally separated from the main Study Runner server. It is meant as an operational utility for installation, repair, participant lookup and data cleanup.

## Features

- Install the latest Study Runner release into a dedicated folder
- Install a specific older release version
- Repair or reset a broken installation
- Delete a whole installation directory
- Generate participant IDs
- Search participant data in `saved_results`
- List participant sessions for a study
- Delete participant session folders and keep an archive if requested

## Quick start

Install the package in editable mode:

```bash
cd tools/study-runner-admin
python -m pip install -r requirements.txt
python -m pip install -e .
```

## CLI examples

```bash
study-runner-admin versions --limit 10
study-runner-admin install ~/StudyRunner --version 1.7.0
study-runner-admin repair ~/StudyRunner
study-runner-admin remove ~/StudyRunner --force

study-runner-admin participants generate --study DemoStudy --prefix P
study-runner-admin participants list --study DemoStudy --data-dir ~/StudyRunner/data
study-runner-admin participants find P-abcdef123456 --data-dir ~/StudyRunner/data
study-runner-admin participants delete P-abcdef123456 --data-dir ~/StudyRunner/data --reason "consent withdrawn" --archive-first
```

## Directory assumptions

The participant lookup expects a Study Runner data layout like this:

```text
<DATA_DIR>/
  saved_results/
    MyStudy/
      participants/
        P-abcdef123456/
          sessions/
            20260811T163356Z__study-session-...
```

This is intentionally a lightweight helper and does not replace the main Study Runner server.
