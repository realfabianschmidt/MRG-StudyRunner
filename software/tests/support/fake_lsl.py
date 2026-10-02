"""A stand-in for pylsl that records what a sensor plugin publishes.

Give it to a plugin's streams with ``adapter._streams.use_backend(FakePylsl())``:
outlets keep every pushed sample with its timestamp, stream infos keep their
desc tree, and ``local_clock`` returns ``clock`` (set it to drive time).
"""
from __future__ import annotations

from typing import Any


class FakeXmlElement:
    def __init__(self, name: str, value: Any = None) -> None:
        self.name = name
        self.value = value
        self.children: list[FakeXmlElement] = []

    def append_child(self, name: str) -> "FakeXmlElement":
        child = FakeXmlElement(name)
        self.children.append(child)
        return child

    def append_child_value(self, name: str, value: Any) -> "FakeXmlElement":
        child = FakeXmlElement(name, value)
        self.children.append(child)
        return child

    def child(self, name: str) -> "FakeXmlElement":
        return next(child for child in self.children if child.name == name)

    def values(self) -> dict[str, Any]:
        """``{name: value}`` of the value children (a flat desc block)."""
        return {child.name: child.value for child in self.children if child.value is not None}


class FakeStreamInfo:
    def __init__(self, *, name, type, channel_count, nominal_srate, channel_format, source_id) -> None:
        self.name = name
        self.type = type
        self.channel_count = channel_count
        self.nominal_srate = nominal_srate
        self.channel_format = channel_format
        self.source_id = source_id
        self._desc = FakeXmlElement("desc")

    def desc(self) -> FakeXmlElement:
        return self._desc

    def channel_labels(self) -> list[str]:
        return [channel.values()["label"] for channel in self._desc.child("channels").children]

    def channel_units(self) -> list[str]:
        return [channel.values()["unit"] for channel in self._desc.child("channels").children]

    def study_runner(self) -> dict[str, Any]:
        return self._desc.child("study_runner").values()


class FakeStreamOutlet:
    def __init__(self, info: FakeStreamInfo) -> None:
        self.info = info
        self.samples: list[tuple[list[Any], float]] = []
        self.fail: Exception | None = None

    def push_sample(self, values, timestamp=0.0, pushthrough=True) -> None:
        if self.fail is not None:
            raise self.fail
        self.samples.append((list(values), float(timestamp)))

    def push_chunk(self, rows, timestamp=0.0, pushthrough=True) -> None:
        if self.fail is not None:
            raise self.fail
        stamps = list(timestamp) if isinstance(timestamp, (list, tuple)) else [float(timestamp)] * len(rows)
        for row, stamp in zip(rows, stamps, strict=True):
            self.samples.append((list(row), float(stamp)))

    @property
    def rows(self) -> list[list[Any]]:
        return [row for row, _ in self.samples]

    @property
    def timestamps(self) -> list[float]:
        return [stamp for _, stamp in self.samples]


class FakePylsl:
    def __init__(self, clock: float = 1000.0) -> None:
        self.clock = clock
        self.outlets: list[FakeStreamOutlet] = []

    def local_clock(self) -> float:
        return self.clock

    def StreamInfo(self, **kwargs) -> FakeStreamInfo:  # noqa: N802 - pylsl's name
        return FakeStreamInfo(**kwargs)

    def StreamOutlet(self, info: FakeStreamInfo) -> FakeStreamOutlet:  # noqa: N802 - pylsl's name
        outlet = FakeStreamOutlet(info)
        self.outlets.append(outlet)
        return outlet

    def outlet(self, source_id: str) -> FakeStreamOutlet:
        """The newest outlet with this source_id."""
        return next(outlet for outlet in reversed(self.outlets) if outlet.info.source_id == source_id)
