"""Unit tests for converter module."""

from pathlib import Path

import pytest

from src.converter import (
    BatchInfo,
    ConvertSettings,
    build_kcc_command,
    filter_unconverted_batches,
    find_already_converted,
    find_inputs,
    group_into_batches,
    _batch_output_name,
    _estimate_output,
    _get_size_mb,
    _is_image_dir,
    _is_suwayomi_manga,
)


@pytest.fixture
def manga_dir(tmp_path: Path) -> Path:
    """Create a fake Suwayomi-style manga directory with chapters."""
    manga = tmp_path / "My Manga"
    manga.mkdir()
    for i in range(1, 6):
        ch = manga / f"Chapter {i}"
        ch.mkdir()
        for j in range(1, 4):
            img = ch / f"{j:03d}.jpg"
            img.write_bytes(b"\xff\xd8" + b"\x00" * (1024 * 100 * i))
    return manga


@pytest.fixture
def cbz_dir(tmp_path: Path) -> Path:
    """Create a directory with fake CBZ files."""
    d = tmp_path / "cbz_manga"
    d.mkdir()
    for i in range(1, 12):
        cbz = d / f"Chapter {i}.cbz"
        cbz.write_bytes(b"PK" + b"\x00" * (1024 * 1024 * 20))
    return d


class TestGetSizeMb:
    def test_file_size(self, tmp_path: Path):
        f = tmp_path / "test.jpg"
        f.write_bytes(b"\x00" * (1024 * 1024))
        assert abs(_get_size_mb(f) - 1.0) < 0.01

    def test_dir_size(self, tmp_path: Path):
        d = tmp_path / "chapter"
        d.mkdir()
        for i in range(2):
            (d / f"{i}.jpg").write_bytes(b"\x00" * (1024 * 512))
        assert abs(_get_size_mb(d) - 1.0) < 0.01

    def test_nonexistent(self, tmp_path: Path):
        assert _get_size_mb(tmp_path / "nope") == 0.0


class TestIsImageDir:
    def test_with_images(self, tmp_path: Path):
        (tmp_path / "001.jpg").write_bytes(b"\xff")
        assert _is_image_dir(tmp_path) is True

    def test_without_images(self, tmp_path: Path):
        (tmp_path / "readme.txt").write_text("hi")
        assert _is_image_dir(tmp_path) is False


class TestIsSuwayomiManga:
    def test_valid_manga_dir(self, manga_dir: Path):
        assert _is_suwayomi_manga(manga_dir) is True

    def test_not_a_dir(self, tmp_path: Path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        assert _is_suwayomi_manga(f) is False

    def test_empty_dir(self, tmp_path: Path):
        assert _is_suwayomi_manga(tmp_path) is False


class TestFindInputs:
    def test_finds_cbz_files(self, cbz_dir: Path):
        result = find_inputs([cbz_dir])
        assert len(result) == 11
        assert all(f.suffix == ".cbz" for f in result)

    def test_natural_sort_cbz(self, cbz_dir: Path):
        result = find_inputs([cbz_dir])
        names = [f.stem for f in result]
        assert names[0] == "Chapter 1"
        assert names[1] == "Chapter 2"
        assert names[-1] == "Chapter 11"

    def test_finds_suwayomi_chapters(self, manga_dir: Path):
        result = find_inputs([manga_dir])
        assert len(result) == 5
        assert all(f.is_dir() for f in result)

    def test_natural_sort_chapters(self, manga_dir: Path):
        result = find_inputs([manga_dir])
        names = [f.name for f in result]
        assert names == ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4", "Chapter 5"]

    def test_single_cbz_file(self, cbz_dir: Path):
        single = cbz_dir / "Chapter 1.cbz"
        result = find_inputs([single])
        assert len(result) == 1
        assert result[0] == single

    def test_empty_dir(self, tmp_path: Path):
        result = find_inputs([tmp_path])
        assert result == []


class TestGroupIntoBatches:
    def test_single_batch(self, tmp_path: Path):
        files = []
        for i in range(3):
            f = tmp_path / f"ch{i}.cbz"
            f.write_bytes(b"\x00" * (1024 * 1024))
            files.append(f)
        batches = group_into_batches(files, max_size_mb=100)
        assert len(batches) == 1
        assert len(batches[0].files) == 3

    def test_splits_when_over_limit(self, cbz_dir: Path):
        files = find_inputs([cbz_dir])
        # Each file is ~20MB, estimate ~11MB output. With 50MB limit -> multiple batches
        batches = group_into_batches(files, max_size_mb=50)
        assert len(batches) > 1
        for batch in batches:
            assert batch.estimated_output_mb <= 50 or len(batch.files) == 1

    def test_empty_input(self):
        batches = group_into_batches([], max_size_mb=100)
        assert batches == []

    def test_oversized_single_file(self, tmp_path: Path):
        big = tmp_path / "huge.cbz"
        big.write_bytes(b"\x00" * (1024 * 1024 * 500))
        batches = group_into_batches([big], max_size_mb=100)
        assert len(batches) == 1
        assert len(batches[0].files) == 1


class TestBuildKccCommand:
    def test_basic_command(self, tmp_path: Path):
        f = tmp_path / "ch1.cbz"
        f.write_bytes(b"\x00" * 1024)
        batch = BatchInfo(index=0, files=[f])
        settings = ConvertSettings(title="Test Manga")
        cmd = build_kcc_command(batch, 1, tmp_path / "out", settings)

        assert "kcc-c2e" == cmd[0]
        assert "--profile" in cmd
        assert "KPW5" in cmd
        assert "--mozjpeg" in cmd
        assert "--manga-style" in cmd
        assert "--upscale" in cmd
        assert "--title" in cmd
        assert "Test Manga" in cmd

    def test_multi_batch_adds_volume(self, tmp_path: Path):
        f = tmp_path / "ch1.cbz"
        f.write_bytes(b"\x00" * 1024)
        batch = BatchInfo(index=2, files=[f])
        settings = ConvertSettings(title="My Manga")
        cmd = build_kcc_command(batch, 5, tmp_path / "out", settings)
        assert "My Manga Vol.3" in cmd

    def test_filefusion_with_multiple_files(self, tmp_path: Path):
        files = []
        for i in range(3):
            f = tmp_path / f"ch{i}.cbz"
            f.write_bytes(b"\x00" * 1024)
            files.append(f)
        batch = BatchInfo(index=0, files=files)
        settings = ConvertSettings()
        cmd = build_kcc_command(batch, 1, tmp_path / "out", settings)
        assert "--filefusion" in cmd

    def test_no_manga_mode(self, tmp_path: Path):
        f = tmp_path / "ch1.cbz"
        f.write_bytes(b"\x00" * 1024)
        batch = BatchInfo(index=0, files=[f])
        settings = ConvertSettings(manga_mode=False)
        cmd = build_kcc_command(batch, 1, tmp_path / "out", settings)
        assert "--manga-style" not in cmd


class TestFilterUnconvertedBatches:
    def test_skips_already_converted(self, tmp_path: Path):
        out = tmp_path / "output"
        out.mkdir()
        (out / "Manga Vol.1.epub").write_bytes(b"fake")

        files = []
        for i in range(2):
            f = tmp_path / f"ch{i}.cbz"
            f.write_bytes(b"\x00" * 1024)
            files.append(f)

        batches = [
            BatchInfo(index=0, files=[files[0]]),
            BatchInfo(index=1, files=[files[1]]),
        ]
        result = filter_unconverted_batches(batches, out, "Manga")
        assert len(result) == 1
        assert result[0].index == 1

    def test_keeps_all_when_none_converted(self, tmp_path: Path):
        out = tmp_path / "output"
        files = []
        for i in range(2):
            f = tmp_path / f"ch{i}.cbz"
            f.write_bytes(b"\x00" * 1024)
            files.append(f)
        batches = [
            BatchInfo(index=0, files=[files[0]]),
            BatchInfo(index=1, files=[files[1]]),
        ]
        result = filter_unconverted_batches(batches, out, "Manga")
        assert len(result) == 2


class TestBatchOutputName:
    def test_single_batch(self):
        assert _batch_output_name("Manga", 0, 1) == "Manga"

    def test_multi_batch(self):
        assert _batch_output_name("Manga", 0, 3) == "Manga Vol.1"
        assert _batch_output_name("Manga", 2, 3) == "Manga Vol.3"
