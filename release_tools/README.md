# Release Tools

Dieser Ordner enthaelt die Release-Automation.

Empfohlener Einstieg unter Windows:

```powershell
.\release.ps1 patch
```

Voraussetzung:

```powershell
winget install --id GitHub.cli
gh auth login
gh auth status
```

Direkter Node-Aufruf:

```bash
node release_tools/release-study-runner.mjs release patch
```

Der Release-Befehl erstellt einen Branch, oeffnet einen PR, wartet auf CI, merged bei gruenem Ergebnis, pusht den Release-Tag und prueft den fertigen GitHub Release.
