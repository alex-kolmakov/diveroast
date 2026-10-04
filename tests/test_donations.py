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
    import src.api.dependencies as deps
    from src.config import settings

    monkeypatch.setattr(settings, "DONATIONS_DIR", str(tmp_path))
    monkeypatch.setattr(deps, "_donation_store", None)
    return tmp_path


async def _post(path: str, **kwargs):
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, **kwargs)


def _donate(**extra) -> dict:
    from src.storage.donations import CONSENT_VERSION

    return {"donate": "true", "consent_version": CONSENT_VERSION, **extra}


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
    result = await _upload(UDDF_WITH_PEOPLE, "jane_buddy_log.uddf", **_donate())
    donation = result["donation"]
    assert donation["status"] == "stored"
    log = donations_dir / f"{donation['id']}.uddf"
    assert sorted(p.name for p in donations_dir.iterdir()) == sorted(
        [log.name, f"{donation['id']}.json"]
    )
    assert b"Jane" not in log.read_bytes()
    assert b"Blue Hole" in log.read_bytes()


@pytest.mark.anyio
async def test_upload_without_donate_stores_nothing(donations_dir):
    await _upload(UDDF_WITH_PEOPLE, "log.uddf")
    assert list(donations_dir.iterdir()) == []


@pytest.mark.anyio
async def test_consent_record_keeps_no_token_or_filename(donations_dir):
    import json

    from src.storage.donations import CONSENT_VERSION

    result = await _upload(UDDF_WITH_PEOPLE, "jane_buddy_log.uddf", **_donate())
    donation = result["donation"]
    record = json.loads((donations_dir / f"{donation['id']}.json").read_text())
    assert record["consent_version"] == CONSENT_VERSION
    assert record["dive_count"] == 1
    assert record["created_at"].endswith("+00:00")
    token = donation["deletion_code"].split(".", 1)[1]
    text = json.dumps(record)
    assert token not in text and "jane" not in text


@pytest.mark.anyio
async def test_outdated_consent_stores_nothing(donations_dir):
    result = await _upload(
        UDDF_WITH_PEOPLE, "log.uddf", donate="true", consent_version="2020-01-01"
    )
    assert result["donation"] is None
    assert list(donations_dir.iterdir()) == []


@pytest.mark.anyio
async def test_donor_deletes_with_their_code(donations_dir):
    donation = (await _upload(UDDF_WITH_PEOPLE, "log.uddf", **_donate()))["donation"]
    donation_id, token = donation["deletion_code"].split(".", 1)

    wrong = await _post("/api/donations/delete", json={"code": f"{donation_id}.nope"})
    assert wrong.status_code == 404
    assert len(list(donations_dir.iterdir())) == 2

    ok = await _post("/api/donations/delete", json={"code": donation["deletion_code"]})
    assert ok.status_code == 204
    assert list(donations_dir.iterdir()) == []
    again = await _post(
        "/api/donations/delete", json={"code": donation["deletion_code"]}
    )
    assert again.status_code == 404


@pytest.mark.anyio
async def test_admin_deletes_by_id(donations_dir, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app
    from src.config import settings

    monkeypatch.setattr(settings, "ADMIN_SECRET", "s3cret")
    donation = (await _upload(UDDF_WITH_PEOPLE, "log.uddf", **_donate()))["donation"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        path = f"/api/admin/donations/{donation['id']}"
        assert (await client.delete(path)).status_code == 403
        r = await client.delete(path, headers={"X-Admin-Secret": "s3cret"})
    assert r.status_code == 204
    assert list(donations_dir.iterdir()) == []


@pytest.mark.anyio
async def test_same_log_donated_twice_is_stored_once(donations_dir):
    first = (await _upload(UDDF_WITH_PEOPLE, "a.uddf", **_donate()))["donation"]
    second = (await _upload(UDDF_WITH_PEOPLE, "b.uddf", **_donate()))["donation"]
    assert second == {"id": first["id"], "deletion_code": None, "status": "duplicate"}
    assert len(list(donations_dir.iterdir())) == 2


def test_storage_cap(tmp_path):
    from src.storage.donations import DonationStore

    store = DonationStore(str(tmp_path), max_total_bytes=1500)
    assert store.save(b"x" * 1000, ".ssrf", dive_count=1, consent_version="v")
    assert store.save(b"y" * 1000, ".ssrf", dive_count=1, consent_version="v") is None


def test_bad_ids_never_touch_other_files(tmp_path):
    from src.storage.donations import DonationStore

    (tmp_path / "keep.json").write_text("{}")
    store = DonationStore(str(tmp_path), max_total_bytes=10**6)
    assert not store.delete("../keep")
    assert not store.delete_with_code("*.x")
    assert (tmp_path / "keep.json").exists()


@pytest.mark.anyio
async def test_roast_is_added_to_the_donation_record(donations_dir):
    import json
    from unittest.mock import MagicMock, patch

    import src.api.dependencies as deps

    result = await _upload(UDDF_WITH_PEOPLE, "log.uddf", **_donate())
    donation_id = result["donation"]["id"]
    agent = deps.get_session(result["session_id"])
    assert agent is not None and agent.donation_id == donation_id

    prompt = MagicMock(prompt="p", label="test", version=5, phoenix_version_id=None)

    def answer(message, prompt_ver):
        agent.last_prompt = "test (v5)"
        return "Blue Hole at 12 m and you bolted."

    with (
        patch.object(agent, "_run_turn", side_effect=answer),
        patch("src.agent.conversation.get_active_prompt", return_value=prompt),
    ):
        body = {"message": "roast me", "session_id": result["session_id"]}
        await _post("/api/chat", json=body)
        await _post("/api/chat", json={**body, "message": "and again?"})

    record = json.loads((donations_dir / f"{donation_id}.json").read_text())
    assert record["roast"]["text"] == "Blue Hole at 12 m and you bolted."
    assert record["roast"]["prompt"] == "test (v5)"
    assert record["roast"]["model"]


@pytest.mark.anyio
async def test_undonated_upload_has_no_donation_link(donations_dir):
    import src.api.dependencies as deps

    result = await _upload(UDDF_WITH_PEOPLE, "log.uddf")
    agent = deps.get_session(result["session_id"])
    assert agent is not None and agent.donation_id is None


# --- Retention --------------------------------------------------------------


def test_old_donations_are_purged(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta

    from src.storage.donations import DonationStore

    store = DonationStore(str(tmp_path), max_total_bytes=10**6)
    old = store.save(b"old", ".ssrf", dive_count=1, consent_version="v")
    new = store.save(b"new", ".ssrf", dive_count=1, consent_version="v")
    assert old and new
    record_path = tmp_path / f"{old.id}.json"
    record = json.loads(record_path.read_text())
    record["created_at"] = "2025-01-01T00:00:00+00:00"
    record_path.write_text(json.dumps(record))

    legacy = tmp_path / "20260310T101010_Jane Smith.ssrf"  # raw, pre-records
    legacy.write_bytes(b"<divelog/>")
    year_ago = (datetime.now(UTC) - timedelta(days=400)).timestamp()
    os.utime(legacy, (year_ago, year_ago))

    assert store.purge(365) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [f"{new.id}.ssrf", f"{new.id}.json"]
    )


def test_old_shared_links_are_purged(tmp_path):
    import time

    from src.storage.snapshots import LocalSnapshotStore

    store = LocalSnapshotStore(str(tmp_path))
    (tmp_path / "old.json").write_text("{}")
    (tmp_path / "new.json").write_text("{}")
    long_ago = time.time() - 400 * 86400
    os.utime(tmp_path / "old.json", (long_ago, long_ago))
    assert store.purge(365) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["new.json"]


def test_purge_runs_on_both_stores(donations_dir, tmp_path, monkeypatch):
    from unittest.mock import patch

    from src.storage import retention

    with (
        patch("src.api.dependencies.get_donation_store") as donations,
        patch("src.api.dependencies.get_snapshot_store") as snapshots,
    ):
        retention.purge_once()
    donations.return_value.purge.assert_called_once()
    snapshots.return_value.purge.assert_called_once()
