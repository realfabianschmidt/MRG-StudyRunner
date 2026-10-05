# Study Runner Admin

Standalone helper for Study Runner installation, repair, cleanup and participant management. It is intentionally separate from the main Study Runner server.

## Features

- Install the latest Study Runner release
- Install a specific older release version by tag
- Repair a broken installation
- Remove an installation directory safely
- List installed versions
- Generate participant IDs
- Search saved results for a participant
- List participants for a study
- Delete participant data with optional archive creation
- Anonymize participant data instead of deleting it

## Quick start

```bash
cd tools/study-runner-admin
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Examples

```bash
study-runner-admin versions --limit 10
study-runner-admin install ~/StudyRunner --version 1.7.0
study-runner-admin install-old ~/StudyRunner --version 1.5.0
study-runner-admin repair ~/StudyRunner
study-runner-admin installed ~/StudyRunner
study-runner-admin remove ~/StudyRunner --force

study-runner-admin participants generate --study DemoStudy --prefix P
study-runner-admin participants list --study DemoStudy --data-dir ~/StudyRunner/data
study-runner-admin participants find P-abcdef123456 --data-dir ~/StudyRunner/data
study-runner-admin participants delete P-abcdef123456 --data-dir ~/StudyRunner/data --archive-first --reason "GDPR request"
study-runner-admin participants anonymize P-abcdef123456 --data-dir ~/StudyRunner/data --reason "consent withdrawn"
```

## Expected file layout

```text
<DATA_DIR>/
  saved_results/
    DemoStudy/
      participants/
        P-abcdef123456/
          sessions/
            20260811T163356Z__study-session-...
```

This tool is meant as an operational helper and does not replace the Study Runner server or the main app logic.
































































































































































