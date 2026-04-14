"""Simple TUI for converting manga CBZ files to Kindle format."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Log,
    Select,
    Static,
    Switch,
)

from src.converter import (
    KINDLE_PROFILES,
    BatchInfo,
    ConvertSettings,
    convert_batch,
    filter_unconverted_batches,
    find_inputs,
    group_into_batches,
)

CSS = """
Screen {
    layout: vertical;
}

#main {
    height: 1fr;
}

#sidebar {
    width: 36;
    border-right: tall $surface-lighten-2;
    padding: 1;
}

#sidebar Label {
    margin-top: 1;
    color: $text-muted;
}

#content {
    width: 1fr;
    padding: 1;
}

#path-input {
    width: 1fr;
}

#add-btn {
    width: 12;
}

#output-input {
    width: 1fr;
}

#files-table {
    height: 1fr;
    min-height: 8;
}

#batch-info {
    height: 3;
    color: $text-muted;
    padding: 0 1;
}

#log {
    height: 1fr;
    min-height: 6;
}

#bottom-bar {
    height: 3;
    align: right middle;
    padding: 0 1;
}

#convert-btn {
    width: 24;
}

#remove-btn {
    width: 14;
}

#clear-btn {
    width: 14;
}

.row {
    height: auto;
    margin-bottom: 1;
}
"""


class MangaConverterApp(App):
    TITLE = "Kindle Manga Converter"
    CSS = CSS
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+a", "focus_input", "Add files"),
        ("ctrl+r", "remove_selected", "Remove"),
        ("ctrl+enter", "start_convert", "Convert"),
    ]

    def __init__(self):
        super().__init__()
        self.inputs: list[Path] = []
        self.batches: list[BatchInfo] = []

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal(id="main"):
            with Vertical(id="sidebar"):
                yield Label("SETTINGS", classes="row")

                yield Label("Profile")
                yield Select(
                    [(p, p) for p in KINDLE_PROFILES],
                    value="KPW5",
                    id="profile-select",
                )

                yield Label("Title")
                yield Input(placeholder="Manga title...", id="title-input")

                yield Label("Max batch MB")
                yield Input(value="190", id="max-size-input", type="integer")

                yield Label("Output dir")
                yield Input(placeholder="(auto)", id="output-input")

                with Horizontal(classes="row"):
                    yield Label("Manga R-to-L ")
                    yield Switch(value=True, id="manga-switch")

                with Horizontal(classes="row"):
                    yield Label("Upscale     ")
                    yield Switch(value=True, id="upscale-switch")

                with Horizontal(classes="row"):
                    yield Label("Skip done   ")
                    yield Switch(value=True, id="skip-switch")

            with Vertical(id="content"):
                yield Label("Paste path to manga folder or CBZ files:")

                with Horizontal(classes="row"):
                    yield Input(
                        placeholder="/path/to/manga/ or /path/to/file.cbz ...",
                        id="path-input",
                    )
                    yield Button("Add", id="add-btn", variant="primary")

                yield DataTable(id="files-table")
                yield Static("No files added yet.", id="batch-info")

                with Horizontal(id="bottom-bar"):
                    yield Button("Remove", id="remove-btn", variant="error")
                    yield Button("Clear", id="clear-btn", variant="warning")
                    yield Button("Convert", id="convert-btn", variant="success")

                yield Label("Log:")
                yield Log(id="log", auto_scroll=True)

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#files-table", DataTable)
        table.add_columns("Chapter/File", "Size (MB)", "Batch")
        table.cursor_type = "row"

    # --- Actions ---

    def action_focus_input(self) -> None:
        self.query_one("#path-input", Input).focus()

    def action_remove_selected(self) -> None:
        self._remove_selected()

    def action_start_convert(self) -> None:
        self._on_convert()

    # --- Events ---

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "path-input":
            self._add_paths(event.value)
            event.input.value = ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-btn":
            inp = self.query_one("#path-input", Input)
            self._add_paths(inp.value)
            inp.value = ""
            inp.focus()
        elif event.button.id == "remove-btn":
            self._remove_selected()
        elif event.button.id == "clear-btn":
            self._clear_files()
        elif event.button.id == "convert-btn":
            self._on_convert()

    # --- Logic ---

    def _add_paths(self, raw: str) -> None:
        if not raw.strip():
            return

        log = self.query_one("#log", Log)
        paths = [Path(p.strip().strip("'\"")) for p in raw.strip().splitlines() or [raw.strip()]]

        new_inputs = find_inputs(paths)
        if not new_inputs:
            log.write_line(f"No manga found in: {raw.strip()}")
            return

        existing = {f.resolve() for f in self.inputs}
        added = 0
        for f in new_inputs:
            if f.resolve() not in existing:
                self.inputs.append(f)
                existing.add(f.resolve())
                added += 1

        kind = "chapter(s)" if new_inputs[0].is_dir() else "file(s)"
        log.write_line(f"Added {added} {kind}, {len(new_inputs) - added} duplicate(s) skipped")
        self._refresh_table()

    def _remove_selected(self) -> None:
        table = self.query_one("#files-table", DataTable)
        if table.row_count == 0:
            return
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            row_index = table.get_row_index(row_key)
            if 0 <= row_index < len(self.inputs):
                removed = self.inputs.pop(row_index)
                self.query_one("#log", Log).write_line(f"Removed: {removed.name}")
                self._refresh_table()
        except Exception:
            pass

    def _clear_files(self) -> None:
        self.inputs.clear()
        self._refresh_table()
        self.query_one("#log", Log).write_line("Cleared all files")

    def _get_settings(self) -> ConvertSettings:
        return ConvertSettings(
            profile=str(self.query_one("#profile-select", Select).value),
            max_size_mb=int(self.query_one("#max-size-input", Input).value or "190"),
            title=self.query_one("#title-input", Input).value,
            manga_mode=self.query_one("#manga-switch", Switch).value,
            upscale=self.query_one("#upscale-switch", Switch).value,
        )

    def _refresh_table(self) -> None:
        settings = self._get_settings()
        self.batches = group_into_batches(self.inputs, settings.max_size_mb)

        table = self.query_one("#files-table", DataTable)
        table.clear()

        file_to_batch: dict[Path, int] = {}
        for b in self.batches:
            for f in b.files:
                file_to_batch[f] = b.index + 1

        for f in self.inputs:
            from src.converter import _get_size_mb
            size = _get_size_mb(f)
            batch_num = file_to_batch.get(f, "?")
            table.add_row(f.name, f"{size:.1f}", str(batch_num))

        total_mb = sum(_get_size_mb(f) for f in self.inputs)
        info = self.query_one("#batch-info", Static)
        if self.inputs:
            info.update(
                f"{len(self.inputs)} chapters/files | {total_mb:.1f} MB total | "
                f"{len(self.batches)} batch(es) (each < {settings.max_size_mb} MB output)"
            )
        else:
            info.update("No files added yet.")

    def _get_manga_name(self) -> str:
        """Detect manga name from the input paths."""
        if not self.inputs:
            return ""
        parent = self.inputs[0].parent
        # For CBZ files inside a manga dir, parent is the manga dir
        # For chapter dirs (Suwayomi), parent is also the manga dir
        return parent.name

    def _get_output_dir(self) -> Path:
        out_raw = self.query_one("#output-input", Input).value.strip()
        if out_raw:
            return Path(out_raw).resolve()
        manga_name = self._get_manga_name() or "output"
        return Path.home() / "kindle-manga-output" / manga_name

    def _on_convert(self) -> None:
        if not self.inputs:
            self.query_one("#log", Log).write_line("No files to convert!")
            return
        self._run_conversion()

    @work(thread=True)
    def _run_conversion(self) -> None:
        log = self.query_one("#log", Log)
        settings = self._get_settings()
        batches = group_into_batches(self.inputs, settings.max_size_mb)
        output_dir = self._get_output_dir()

        if not settings.title:
            settings.title = self._get_manga_name() or "Manga"

        # Skip already converted batches
        skip = self.query_one("#skip-switch", Switch).value
        if skip:
            original_count = len(batches)
            batches = filter_unconverted_batches(batches, output_dir, settings.title)
            skipped = original_count - len(batches)
            if skipped:
                log.write_line(f"Skipping {skipped} batch(es) already converted")

        if not batches:
            log.write_line("All batches already converted! Nothing to do.")
            return

        self.call_from_thread(
            self.query_one("#convert-btn", Button).set_class, True, "disabled"
        )

        log.write_line(f"\nStarting conversion: {len(batches)} batch(es)")
        log.write_line(f"Output: {output_dir}")
        log.write_line(f"Profile: {settings.profile} | Format: {settings.output_format}")
        log.write_line(f"mozjpeg: ON | gamma: {settings.gamma} | cropping: {settings.cropping}")

        success = 0
        for batch in batches:
            log.write_line(
                f"\n--- Batch {batch.index + 1} "
                f"({len(batch.files)} chapters, "
                f"{batch.input_size_mb:.0f} MB in -> ~{batch.estimated_output_mb:.0f} MB out) ---"
            )
            for f in batch.files:
                log.write_line(f"  + {f.name}")

            result = convert_batch(batch, len(batches), output_dir, settings)
            log.write_line(f"CMD: {result.command}")
            if result.success:
                log.write_line(f"OK: {result.message}")
                success += 1
            else:
                log.write_line(f"FAIL: {result.message}")
                break

        log.write_line(f"\nDone! {success}/{len(batches)} batches converted")
        log.write_line(f"Files in: {output_dir}")

        self.call_from_thread(
            self.query_one("#convert-btn", Button).set_class, False, "disabled"
        )


def main():
    app = MangaConverterApp()
    app.run()


if __name__ == "__main__":
    main()
