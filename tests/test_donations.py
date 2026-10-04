"""Donated logs: people stripped, dives kept."""

import io
import os
import tempfile
import zipfile
from pathlib import Path

import fitdecode
import pytest

from src.parsers import get_parser
from src.storage.sanitize import sanitize

FULL = "tests/fixtures/anonymized_subsurface_export.ssrf"
FIT = "tests/fixtures/garmin_descent_scuba.fit"

UDDF_WITH_PEOPLE = b"""<?xml version="1.0" encoding="utf-8"?>
<uddf xmlns="http://www.streit.cc/uddf/3.2/" version="3.2.0">
  <generator><name>Test</name></generator>
  <diver>
    <owner id="owner"><personal><firstname>Alex</firstname><lastname>Owner</lastname></personal>
      <contact><email>owner@example.com</email></contact>
      <equipment><divecomputer id="dc"><serialnumber>SN-12345</serialnumber></divecomputer></equipment>
    </owner>
    <buddy id="buddy_jane"><personal><firstname>Jane</firstname><lastname>Buddy</lastname></personal></buddy>
  </diver>
  <divesite>
    <divebase id="base"><name>Shop of Bob Smith</name></divebase>
    <site id="site1"><name>Blue Hole</name>
      <geography><latitude>27.5</latitude><longitude>34.1</longitude></geography>
      <notes><para>Bob showed us the arch</para></notes>
    </site>
  </divesite>
  <profiledata><repetitiongroup id="rg">
    <dive id="d1">
      <informationbeforedive>
        <link ref="site1"/><link ref="buddy_jane"/>
        <divenumber>1</divenumber><datetime>2025-01-01T10:00:00</datetime>
      </informationbeforedive>
      <samples>
        <waypoint><depth>0</depth><divetime>0</divetime></waypoint>
        <waypoint><depth>12</depth><divetime>60</divetime></waypoint>
        <waypoint><depth>0</depth><divetime>120</divetime></waypoint>
      </samples>
      <informationafterdive><notes><para>Jane ran low on air</para></notes>
        <anysymptoms><para>headache</para></anysymptoms></informationafterdive>
    </dive>
  </repetitiongroup></profiledata>
</uddf>"""


def _parse(content: bytes, name: str):
    with tempfile.NamedTemporaryFile(
        suffix=os.path.splitext(name)[1], delete=False
    ) as t:
        t.write(content)
        path = t.name
    try:
        return get_parser(name).parse(path).reset_index(drop=True)
    finally:
        os.unlink(path)


def test_subsurface_loses_people_keeps_sites_and_dives():
    raw = Path(FULL).read_bytes()
    clean = sanitize(raw, "log.ssrf")
    text = clean.decode()
    for gone in (
        "<buddy",
        "<divemaster",
        "<notes",
        "<settings",
        "deviceid=",
        "'Serial'",
    ):
        assert gone not in text, gone
    assert raw.decode().count("gps=") == text.count("gps=")
    assert "<site " in text
    assert _parse(raw, "log.ssrf").equals(_parse(clean, "log.ssrf"))


def test_uddf_loses_people_keeps_sites_and_dives():
    clean = sanitize(UDDF_WITH_PEOPLE, "log.uddf")
    text = clean.decode()
    for gone in ("Alex", "Owner", "owner@", "SN-12345", "Jane", "Bob", "headache"):
        assert gone not in text, gone
    assert "Blue Hole" in text and "27.5" in text
    assert 'ref="site1"' in text
    before = _parse(UDDF_WITH_PEOPLE, "log.uddf")
    after = _parse(clean, "log.uddf")
    assert before.equals(after)
    assert after["dive_site_name"].iloc[0] == "Blue Hole"


def _fit_values(content: bytes) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    with fitdecode.FitReader(content, check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                values = {f.name: f.value for f in frame.fields if f.value is not None}
                found.setdefault(frame.name, []).append(values)
    return found


def test_fit_loses_body_data_and_serials_keeps_the_dive():
    raw = Path(FIT).read_bytes()
    before = _fit_values(raw)
    assert any("weight" in m for m in before["user_profile"])  # the fixture has it
    assert any("serial_number" in m for m in before["device_info"])

    clean = sanitize(raw, "dive.fit")
    after = _fit_values(clean)  # CRCs still valid, or this raises
    assert all(
        v is None or isinstance(v, tuple)
        for m in after["user_profile"]
        for v in m.values()
    )
    for name in ("file_id", "device_info"):
        assert not any("serial_number" in m for m in after[name])
    assert _parse(raw, "dive.fit").equals(_parse(clean, "dive.fit"))


def test_zip_keeps_only_logs_and_renames_them():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Jane Buddy dives/jane_smith.ssrf", Path(FULL).read_bytes())
        z.writestr("Jane Buddy dives/garmin-account.json", '{"email": "jane@x"}')
        z.writestr("dive.fit", Path(FIT).read_bytes())
    clean = sanitize(buf.getvalue(), "export.zip")
    with zipfile.ZipFile(io.BytesIO(clean)) as z:
        names = z.namelist()
        assert sorted(names) == ["log-0001.ssrf", "log-0002.fit"]
        assert b"<buddy" not in z.read("log-0001.ssrf")
    assert len(_parse(clean, "export.zip")) == len(_parse(buf.getvalue(), "export.zip"))


def test_unknown_files_are_refused():
    with pytest.raises(ValueError):
        sanitize(b"<html/>", "page.xml")
    with pytest.raises(ValueError):
        sanitize(b"a,b", "log.csv")


# --- Upload route -----------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def donations_dir(tmp_path, monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "DONATIONS_DIR", str(tmp_path))
    return tmp_path


async def _upload(content: bytes, name: str, **form) -> dict:
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/upload", files={"file": (name, content, "application/xml")}, data=form
        )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.anyio
async def test_donated_upload_is_stored_sanitized_under_a_random_name(donations_dir):
    await _upload(UDDF_WITH_PEOPLE, "jane_buddy_log.uddf", donate="true")
    stored = list(donations_dir.iterdir())
    assert len(stored) == 1
    assert "jane" not in stored[0].name
    assert b"Jane" not in stored[0].read_bytes()
    assert b"Blue Hole" in stored[0].read_bytes()


@pytest.mark.anyio
async def test_upload_without_donate_stores_nothing(donations_dir):
    await _upload(UDDF_WITH_PEOPLE, "log.uddf")
    assert list(donations_dir.iterdir()) == []
