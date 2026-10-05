from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from study_runner_admin.app import (
    DEFAULT_DATA_DIR,
    anonymize_participant_record,
    delete_participant,
    find_participant,
    generate_participant_id_for_study,
    list_participants,
)
from study_runner_admin.core.installer import (
    install_release,
    list_available_releases,
    remove_installation,
    repair_installation,
)


class StudyRunnerAdminGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Study Runner Admin")
        self.geometry("980x620")
        self.minsize(820, 520)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=12)

        self._build_install_tab()
        self._build_participant_tab()

    def _build_install_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Install")

        ttk.Label(frame, text="Install root").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.install_root = tk.StringVar(value=str(Path.home() / "StudyRunner"))
        ttk.Entry(frame, textvariable=self.install_root, width=54).grid(row=0, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(frame, text="Data dir").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.data_dir = tk.StringVar(value=str(DEFAULT_DATA_DIR))
        ttk.Entry(frame, textvariable=self.data_dir, width=54).grid(row=1, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(frame, text="Version").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.version = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.version, width=30).grid(row=2, column=1, sticky="w", padx=8, pady=6)

        self.overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Overwrite existing directory", variable=self.overwrite).grid(row=3, column=1, sticky="w", padx=8, pady=6)

        ttk.Button(frame, text="Install latest", command=self._install_latest).grid(row=4, column=0, padx=8, pady=10, sticky="w")
        ttk.Button(frame, text="Repair", command=self._repair).grid(row=4, column=1, padx=8, pady=10, sticky="w")
        ttk.Button(frame, text="Remove", command=self._remove).grid(row=4, column=2, padx=8, pady=10, sticky="w")
        ttk.Button(frame, text="Refresh versions", command=self._list_versions).grid(row=5, column=0, padx=8, pady=10, sticky="w")

        self.install_output = tk.Text(frame, height=20, state="disabled")
        self.install_output.grid(row=0, column=2, rowspan=7, sticky="nsew", padx=10, pady=8)

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(6, weight=1)

    def _build_participant_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Participants")

        ttk.Label(frame, text="Study ID").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.study_id = tk.StringVar(value="DemoStudy")
        ttk.Entry(frame, textvariable=self.study_id, width=30).grid(row=0, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(frame, text="Participant ID").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.participant_id = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.participant_id, width=30).grid(row=1, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(frame, text="Data dir").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.participant_data_dir = tk.StringVar(value=str(DEFAULT_DATA_DIR))
        ttk.Entry(frame, textvariable=self.participant_data_dir, width=30).grid(row=2, column=1, sticky="ew", padx=8, pady=6)

        row = 3
        ttk.Button(frame, text="Generate ID", command=self._generate_id).grid(row=row, column=0, padx=8, pady=8, sticky="w")
        ttk.Button(frame, text="List study", command=self._list_participants).grid(row=row, column=1, padx=8, pady=8, sticky="w")
        ttk.Button(frame, text="Find", command=self._find_participant).grid(row=row, column=2, padx=8, pady=8, sticky="w")
        row += 1
        ttk.Button(frame, text="Delete", command=self._delete_participant).grid(row=row, column=0, padx=8, pady=8, sticky="w")
        ttk.Button(frame, text="Anonymize", command=self._anonymize_participant).grid(row=row, column=1, padx=8, pady=8, sticky="w")

        self.participant_output = tk.Text(frame, height=20, state="disabled")
        self.participant_output.grid(row=0, column=3, rowspan=8, sticky="nsew", padx=10, pady=8)

        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        frame.rowconfigure(7, weight=1)

    def _print_to_widget(self, widget: tk.Text, payload: object) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, json.dumps(payload, indent=2, ensure_ascii=False))
        widget.configure(state="disabled")

    def _install_latest(self) -> None:
        try:
            result = install_release(
                Path(self.install_root.get()),
                version=(self.version.get() or None),
                data_dir=Path(self.data_dir.get()),
                overwrite=self.overwrite.get(),
            )
            self._print_to_widget(self.install_output, result)
        except Exception as exc:  # pragma: no cover - GUI path
            messagebox.showerror("Install failed", str(exc))

    def _repair(self) -> None:
        try:
            result = repair_installation(Path(self.install_root.get()), data_dir=Path(self.data_dir.get()))
            self._print_to_widget(self.install_output, result)
        except Exception as exc:  # pragma: no cover - GUI path
            messagebox.showerror("Repair failed", str(exc))

    def _remove(self) -> None:
        try:
            result = remove_installation(Path(self.install_root.get()), force=True)
            self._print_to_widget(self.install_output, result)
        except Exception as exc:  # pragma: no cover - GUI path
            messagebox.showerror("Remove failed", str(exc))

    def _list_versions(self) -> None:
        try:
            self._print_to_widget(self.install_output, {"releases": list_available_releases(limit=10)})
        except Exception as exc:  # pragma: no cover - GUI path
            messagebox.showerror("Versions request failed", str(exc))

    def _generate_id(self) -> None:
        try:
            result = {"participant_id": generate_participant_id_for_study(study_id=self.study_id.get(), prefix="P")}
            self._print_to_widget(self.participant_output, result)
        except Exception as exc:  # pragma: no cover - GUI path
            messagebox.showerror("Generate failed", str(exc))

    def _list_participants(self) -> None:
        try:
            result = list_participants(self.study_id.get(), data_dir=self.participant_data_dir.get())
            self._print_to_widget(self.participant_output, result)
        except Exception as exc:  # pragma: no cover - GUI path
            messagebox.showerror("List participants failed", str(exc))

    def _find_participant(self) -> None:
        try:
            result = find_participant(self.participant_id.get(), data_dir=self.participant_data_dir.get())
            self._print_to_widget(self.participant_output, result)
        except Exception as exc:  # pragma: no cover - GUI path
            messagebox.showerror("Find participant failed", str(exc))

    def _delete_participant(self) -> None:
        try:
            result = delete_participant(
                self.participant_id.get(),
                study_id=self.study_id.get(),
                data_dir=self.participant_data_dir.get(),
                reason="user requested deletion",
                archive_first=True,
            )
            self._print_to_widget(self.participant_output, result)
        except Exception as exc:  # pragma: no cover - GUI path
            messagebox.showerror("Delete failed", str(exc))

    def _anonymize_participant(self) -> None:
        try:
            result = anonymize_participant_record(
                self.participant_id.get(),
                study_id=self.study_id.get(),
                data_dir=self.participant_data_dir.get(),
                reason="consent withdrawn",
            )
            self._print_to_widget(self.participant_output, result)
        except Exception as exc:  # pragma: no cover - GUI path
            messagebox.showerror("Anonymize failed", str(exc))


def main() -> int:
    app = StudyRunnerAdminGUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
