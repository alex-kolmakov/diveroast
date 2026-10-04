import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    # Locally there may be no DAN index: either answer is a valid report.
    assert response.status_code in (200, 503)
    assert response.json()["status"] in ("healthy", "unhealthy")


@pytest.mark.anyio
async def test_upload_unsupported_file():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/upload",
            files={"file": ("test.csv", b"some,data", "text/csv")},
        )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


@pytest.mark.anyio
async def test_upload_without_dives_is_rejected():
    """A well-formed log with no dives is a client error, not a crash."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/upload",
            files={"file": ("empty.xml", b"<divelog></divelog>", "text/xml")},
        )
    assert response.status_code == 400
    assert "No dives found" in response.json()["detail"]


@pytest.mark.anyio
async def test_upload_valid_file():
    transport = ASGITransport(app=app)

    # Read actual fixture
    with open("tests/fixtures/anonymized_subsurface_export.ssrf", "rb") as f:
        content = f.read()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/upload",
            files={"file": ("export.ssrf", content, "application/xml")},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["dive_count"] > 0
    assert "session_id" in data
    assert len(data["dive_numbers"]) > 0
