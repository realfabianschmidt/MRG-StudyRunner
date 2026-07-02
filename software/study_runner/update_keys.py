"""Trusted public keys for Python-only update verification.

Release builds can replace this list from CI secrets before PyInstaller runs.
Local development can use STUDY_RUNNER_UPDATE_PUBLIC_KEY instead.
"""

TRUSTED_UPDATE_PUBLIC_KEYS: list[str] = []
