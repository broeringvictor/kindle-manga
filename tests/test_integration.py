"""Integration test: full pipeline from Suwayomi-style dir to KCC command generation."""

from pathlib import Path

import pytest

from src.converter import (
    ConvertSettings,
    build_kcc_command,
    filter_unconverted_batches,
    find_inputs,
    group_into_batches,
)


@pytest.fixture
def suwayomi_manga(tmp_path: Path) -> Path:
    """Simulate a Suwayomi download: manga dir with numbered chapter subdirs containing images."""
    manga = tmp_path / "Weeb Central (EN)" / "Grand Blue Dreaming"
    manga.mkdir(parents=True)

    # Create 10 chapters with varying sizes (simulating real manga)
    for i in range(1, 11):
        ch = manga / f"Official_Chapter {i}"
        ch.mkdir()
        # ~15MB per chapter in images
        for page in range(1, 21):
            img = ch / f"{page:03d}.jpeg"
            img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * (1024 * 768))

    return manga


def test_full_pipeline(suwayomi_manga: Path, tmp_path: Path):
    """Test the full flow: discover chapters -> batch -> generate commands."""
    # 1. Discover chapters from Suwayomi dir
    inputs = find_inputs([suwayomi_manga])
    assert len(inputs) == 10
    assert inputs[0].name == "Official_Chapter 1"
    assert inputs[-1].name == "Official_Chapter 10"

    # 2. Group into batches under 50MB for this test
    batches = group_into_batches(inputs, max_size_mb=50)
    assert len(batches) >= 1

    # Verify all chapters are accounted for
    all_files = [f for b in batches for f in b.files]
    assert len(all_files) == 10

    # Verify no batch exceeds the limit (except oversized singles)
    for batch in batches:
        if len(batch.files) > 1:
            assert batch.estimated_output_mb <= 50

    # 3. Generate KCC commands
    output_dir = tmp_path / "kindle-output" / "Grand Blue Dreaming"
    settings = ConvertSettings(title="Grand Blue Dreaming", max_size_mb=50)

    for batch in batches:
        cmd = build_kcc_command(batch, len(batches), output_dir, settings)
        assert cmd[0] == "kcc-c2e"
        assert "--profile" in cmd
        assert "--mozjpeg" in cmd
        assert "--manga-style" in cmd
        assert str(output_dir) in cmd

        # Multi-batch should have volume numbers
        if len(batches) > 1:
            title_idx = cmd.index("--title") + 1
            assert "Vol." in cmd[title_idx]

    # 4. Test skip logic
    output_dir.mkdir(parents=True)
    first_batch_name = f"Grand Blue Dreaming Vol.1.epub" if len(batches) > 1 else "Grand Blue Dreaming.epub"
    (output_dir / first_batch_name).write_bytes(b"fake epub")

    filtered = filter_unconverted_batches(batches, output_dir, "Grand Blue Dreaming")
    assert len(filtered) == len(batches) - 1
