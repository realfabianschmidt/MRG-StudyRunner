"""Compatibility CLI for the internal camera emotion worker server."""

from study_runner.integrations.camera_emotion.worker.server import *  # noqa: F401,F403


if __name__ == "__main__":
    raise SystemExit(main())
