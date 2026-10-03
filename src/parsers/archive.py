"""Zip archives of dive logs, e.g. a Garmin Connect export of FIT files.

Each supported member is parsed by its own parser and the dives are
concatenated. Non-dive members (a hike in a Garmin export, a freedive) are
skipped; the archive only fails if no dives are found at all. Nested zips
(Garmin's full account export) are followed one level deep.

Limits guard against zip bombs: member count, per-member and total
uncompressed size are capped before anything is decompressed.
"""

import os
import tempfile
import zipfile
from pathlib import PurePosixPath

import pandas as pd

from src.parsers.base import DiveLogParser

MAX_MEMBERS = 5000
MAX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_NESTING = 1


class ArchiveParser(DiveLogParser):
    def parse(self, file_path: str) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        skipped: list[str] = []
        with zipfile.ZipFile(file_path) as archive:
            self._parse_archive(archive, frames, skipped, depth=0)
        if not frames:
            detail = f" Skipped: {'; '.join(skipped[:5])}" if skipped else ""
            raise ValueError(
                f"No dives found in {PurePosixPath(file_path).name}.{detail}"
            )
        return _dedupe_dive_numbers(frames)

    def _parse_archive(
        self,
        archive: zipfile.ZipFile,
        frames: list[pd.DataFrame],
        skipped: list[str],
        depth: int,
    ) -> None:
        from src.parsers import get_parser  # registry imports this module

        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) > MAX_MEMBERS:
            raise ValueError(f"Archive has too many files (max {MAX_MEMBERS})")
        if sum(m.file_size for m in members) > MAX_TOTAL_BYTES:
            raise ValueError("Archive is too large when uncompressed")

        for member in sorted(members, key=lambda m: m.filename):
            name = PurePosixPath(member.filename).name
            if name.startswith(".") or member.filename.startswith("__MACOSX/"):
                continue
            if member.file_size > MAX_MEMBER_BYTES:
                skipped.append(f"{name}: too large")
                continue
            if name.lower().endswith(".zip"):
                if depth >= MAX_NESTING:
                    skipped.append(f"{name}: nested too deep")
                    continue
                with archive.open(member) as fh, tempfile.TemporaryFile() as tmp:
                    tmp.write(fh.read())
                    tmp.seek(0)
                    with zipfile.ZipFile(tmp) as nested:
                        self._parse_archive(nested, frames, skipped, depth + 1)
                continue
            try:
                parser = get_parser(name)
            except ValueError:
                continue  # not a dive log format
            suffix = os.path.splitext(name)[1]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(archive.read(member))
                tmp_path = tmp.name
            try:
                frame = parser.parse(tmp_path)
            except Exception as e:
                skipped.append(f"{name}: {e}")
                continue
            finally:
                os.unlink(tmp_path)
            if not frame.empty:
                frames.append(frame.assign(_source=name))

    def supported_extensions(self) -> list[str]:
        return [".zip"]


def _dedupe_dive_numbers(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Keep dives from different files apart when their numbers collide.

    The same dive number can come from two computers, or a counter reset.
    A later file's colliding dive gets the source file's name appended.
    """
    seen: set[str] = set()
    renamed = []
    for frame in frames:
        source = frame["_source"].iloc[0]
        numbers = frame["dive_number"].astype(str)
        clashes = set(numbers.unique()) & seen
        if clashes:
            stem = PurePosixPath(source).stem
            numbers = numbers.where(
                ~numbers.isin(clashes), numbers.str.cat([f"@{stem}"] * len(numbers))
            )
        seen.update(numbers.unique())
        renamed.append(frame.assign(dive_number=numbers).drop(columns="_source"))
    return pd.concat(renamed, ignore_index=True)
