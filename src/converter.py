"""Core conversion logic: scan manga sources, batch them, and call KCC."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from natsort import natsorted


KINDLE_PROFILES = [
    "KPW5", "KPW34", "KPW", "KO", "KV", "KS", "K11", "K810",
    "K57", "K34", "K2", "K1", "KDX",
]

OUTPUT_FORMATS = ["EPUB", "MOBI", "CBZ", "PDF"]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@dataclass
class BatchInfo:
    index: int
    files: list[Path]
    input_size_mb: float = 0.0
    estimated_output_mb: float = 0.0

    def __post_init__(self):
        self.input_size_mb = sum(_get_size_mb(f) for f in self.files)
        self.estimated_output_mb = sum(_estimate_output(f) for f in self.files)


@dataclass
class ConvertResult:
    batch_index: int
    success: bool
    message: str
    command: str = ""


@dataclass
class ConvertSettings:
    profile: str = "KPW5"
    max_size_mb: int = 190
    title: str = ""
    output_format: str = "EPUB"
    manga_mode: bool = True
    upscale: bool = True
    gamma: str = "1.0"
    cropping: str = "2"


def _get_size_mb(path: Path) -> float:
    """Get size in MB for a file or directory (recursive)."""
    if path.is_file():
        return path.stat().st_size / (1024 * 1024)
    if path.is_dir():
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return total / (1024 * 1024)
    return 0.0


def _estimate_output(path: Path) -> float:
    """Estimate KCC output size. EPUB with upscale on already-compressed JPEG manga
    is typically ~1.3x of input size due to EPUB overhead and image reprocessing."""
    return _get_size_mb(path) * 1.3


def _is_image_dir(path: Path) -> bool:
    """Check if a directory contains image files (a manga chapter)."""
    return any(f.suffix.lower() in IMAGE_EXTENSIONS for f in path.iterdir() if f.is_file())


def _is_suwayomi_manga(path: Path) -> bool:
    """Check if path is a Suwayomi manga dir (contains chapter subdirs with images)."""
    if not path.is_dir():
        return False
    subdirs = [d for d in natsorted(path.iterdir(), key=lambda d: d.name) if d.is_dir()]
    return any(_is_image_dir(d) for d in subdirs[:3])


def find_inputs(paths: list[Path]) -> list[Path]:
    """Find convertible inputs: CBZ files, image directories, or Suwayomi chapter dirs.

    For a Suwayomi manga dir (dir of chapter dirs), returns each chapter dir individually
    so they can be grouped into batches.
    """
    result = []
    for p in paths:
        if p.is_file() and p.suffix.lower() in {".cbz", ".cbr", ".zip", ".cb7", ".pdf"}:
            result.append(p)
        elif p.is_dir():
            # Check if it's a dir of CBZ files
            cbz = natsorted(p.glob("*.[cC][bB][zZ]"), key=lambda f: f.name.lower())
            if cbz:
                result.extend(cbz)
                continue

            # Check if it's a Suwayomi-style manga dir (subdirs with images)
            if _is_suwayomi_manga(p):
                chapters = natsorted(
                    (d for d in p.iterdir() if d.is_dir() and _is_image_dir(d)),
                    key=lambda d: d.name.lower(),
                )
                result.extend(chapters)
                continue

            # Check if it's a single chapter dir with images
            if _is_image_dir(p):
                result.append(p)

    return result


def find_already_converted(output_dir: Path, title: str) -> set[str]:
    """Find output files already in the output dir to skip re-conversion."""
    converted = set()
    if not output_dir.exists():
        return converted
    for f in output_dir.iterdir():
        if f.suffix.lower() in {".epub", ".mobi", ".cbz", ".pdf"}:
            converted.add(f.stem.lower())
    return converted


def group_into_batches(files: list[Path], max_size_mb: float) -> list[BatchInfo]:
    batches: list[BatchInfo] = []
    current_files: list[Path] = []
    current_size = 0.0

    for f in files:
        estimated = _estimate_output(f)

        if estimated >= max_size_mb:
            if current_files:
                batches.append(BatchInfo(index=len(batches), files=current_files))
                current_files = []
                current_size = 0.0
            batches.append(BatchInfo(index=len(batches), files=[f]))
            continue

        if current_size + estimated > max_size_mb and current_files:
            batches.append(BatchInfo(index=len(batches), files=current_files))
            current_files = []
            current_size = 0.0

        current_files.append(f)
        current_size += estimated

    if current_files:
        batches.append(BatchInfo(index=len(batches), files=current_files))

    return batches


def _batch_output_name(title: str, batch_index: int, total_batches: int) -> str:
    """Generate expected output name for a batch."""
    if total_batches > 1:
        return f"{title} Vol.{batch_index + 1}"
    return title


def filter_unconverted_batches(
    batches: list[BatchInfo],
    output_dir: Path,
    title: str,
) -> list[BatchInfo]:
    """Filter out batches whose output already exists."""
    converted = find_already_converted(output_dir, title)
    if not converted:
        return batches

    result = []
    for batch in batches:
        expected = _batch_output_name(title, batch.index, len(batches)).lower()
        if expected not in converted:
            result.append(batch)
    return result


def build_kcc_command(
    batch: BatchInfo,
    total_batches: int,
    output_dir: Path,
    settings: ConvertSettings,
) -> list[str]:
    cmd = [
        "kcc-c2e",
        "--profile", settings.profile,
        "--format", settings.output_format,
        "--cropping", settings.cropping,
        "-g", settings.gamma,
        "--output", str(output_dir),
        "--mozjpeg",
    ]

    if settings.manga_mode:
        cmd.append("--manga-style")
    if settings.upscale:
        cmd.append("--upscale")

    title = settings.title
    if title and total_batches > 1:
        cmd.extend(["--title", f"{title} Vol.{batch.index + 1}"])
    elif title:
        cmd.extend(["--title", title])

    if len(batch.files) > 1:
        cmd.append("--filefusion")

    cmd.extend(str(f) for f in batch.files)
    return cmd


def _rename_output(output_dir: Path, batch: BatchInfo, total_batches: int, settings: ConvertSettings) -> str | None:
    """Rename KCC output file from its default name to the desired title.
    Returns the new filename or None if no file found to rename."""
    first_input = batch.files[0]
    base = first_input.stem

    # KCC names fused output as "<first_file> [fused].<ext>"
    patterns = [
        f"{base} [fused]",
        f"{base}",
    ]

    target_name = _batch_output_name(settings.title, batch.index, total_batches)

    for pattern in patterns:
        for ext in [".epub", ".mobi", ".cbz", ".pdf"]:
            candidate = output_dir / f"{pattern}{ext}"
            if candidate.exists():
                new_path = output_dir / f"{target_name}{ext}"
                if candidate != new_path:
                    candidate.rename(new_path)
                return new_path.name
    return None


def convert_batch(
    batch: BatchInfo,
    total_batches: int,
    output_dir: Path,
    settings: ConvertSettings,
) -> ConvertResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_kcc_command(batch, total_batches, output_dir, settings)
    cmd_str = " ".join(cmd)

    try:
        subprocess.run(cmd, check=True, text=True, capture_output=True)
        renamed = _rename_output(output_dir, batch, total_batches, settings)
        msg = f"Converted -> {renamed}" if renamed else "Converted successfully"
        return ConvertResult(batch.index, True, msg, cmd_str)
    except FileNotFoundError:
        return ConvertResult(
            batch.index, False,
            "kcc-c2e not found. Install KCC: pip install KindleComicConverter "
            "or download from https://github.com/ciromattia/kcc/releases",
            cmd_str,
        )
    except subprocess.CalledProcessError as e:
        msg = e.stderr[:500] if e.stderr else str(e)
        return ConvertResult(batch.index, False, f"KCC error: {msg}", cmd_str)
