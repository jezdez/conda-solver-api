"""Tests for conda_resolve.app (Starlette endpoints)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Route

from conda_resolve.app import health, solve_environment_yml, solve_specs


@pytest.fixture()
def test_app():
    return Starlette(
        routes=[
            Route("/solve", solve_specs, methods=["POST"]),
            Route(
                "/solve/environment-yml",
                solve_environment_yml,
                methods=["POST"],
            ),
            Route("/health", health, methods=["GET"]),
        ],
    )


@pytest.fixture()
async def client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as c:
        yield c


@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "platforms",
    [
        pytest.param(["linux-64"], id="single"),
        pytest.param(["linux-64", "osx-arm64"], id="multi"),
    ],
)
async def test_solve_specs(client, platforms):
    resp = await client.post(
        "/solve",
        json={
            "channels": ["conda-forge"],
            "dependencies": ["zlib"],
            "platforms": platforms,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == len(platforms)
    for result, platform in zip(data, platforms):
        assert result["platform"] == platform
        assert result["error"] is None
        names = [p["name"] for p in result["packages"]]
        assert "zlib" in names
        for pkg in result["packages"]:
            assert pkg["sha256"], f"{pkg['name']} missing sha256"
            assert pkg["url"], f"{pkg['name']} missing url"


@pytest.mark.anyio
async def test_solve_specs_unsatisfiable(client):
    resp = await client.post(
        "/solve",
        json={
            "channels": ["conda-forge"],
            "dependencies": ["__nonexistent_package_xyz__"],
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["error"] is not None
    assert data[0]["packages"] == []
    assert "/Users/" not in data[0]["error"]


@pytest.mark.anyio
async def test_solve_specs_defaults(client):
    resp = await client.post(
        "/solve",
        json={"dependencies": ["zlib"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["error"] is None


@pytest.mark.anyio
async def test_solve_environment_yml(client, environment_yml_bytes):
    resp = await client.post(
        "/solve/environment-yml?platform=linux-64",
        content=environment_yml_bytes,
        headers={"content-type": "application/x-yaml"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["platform"] == "linux-64"
    names = [p["name"] for p in data[0]["packages"]]
    assert "python" in names
    assert "numpy" in names


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query, expected_count",
    [
        ("?platform=linux-64", 1),
        ("?platform=linux-64&platform=osx-arm64", 2),
    ],
)
async def test_solve_environment_yml_platform_params(
    client, environment_yml_bytes, query, expected_count
):
    resp = await client.post(
        f"/solve/environment-yml{query}",
        content=environment_yml_bytes,
        headers={"content-type": "application/x-yaml"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == expected_count


@pytest.mark.anyio
async def test_solve_environment_yml_filters_pip_deps(client):
    yml = b"""\
name: mixed
channels:
  - conda-forge
dependencies:
  - python=3.12
  - pip:
    - requests
"""
    resp = await client.post(
        "/solve/environment-yml?platform=linux-64",
        content=yml,
        headers={"content-type": "application/x-yaml"},
    )
    assert resp.status_code == 200
    data = resp.json()
    names = [p["name"] for p in data[0]["packages"]]
    assert "python" in names
    assert "requests" not in names


@pytest.mark.anyio
async def test_solve_specs_invalid_json(client):
    resp = await client.post(
        "/solve",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.json()["error"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"channels": 123},
        {"dependencies": [None]},
        {"platforms": "linux-64"},
        {"dependencies": ["zlib"], "channels": ["ok"], "platforms": [1]},
    ],
)
async def test_solve_specs_invalid_types(client, body):
    resp = await client.post("/solve", json=body)
    assert resp.status_code == 400
    assert "must be a list of strings" in resp.json()["error"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "endpoint",
    [
        pytest.param("/solve", id="solve"),
        pytest.param("/solve/environment-yml", id="env-yml"),
    ],
)
async def test_body_too_large(client, endpoint):
    content_type = (
        "application/json" if endpoint == "/solve" else "application/x-yaml"
    )
    resp = await client.post(
        endpoint,
        content=b"x" * (1_024 * 1_024 + 1),
        headers={"content-type": content_type},
    )
    assert resp.status_code == 413


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body, expected_fragment",
    [
        pytest.param(
            b": [invalid yaml {{", "Invalid YAML", id="invalid-yaml"
        ),
        pytest.param(
            b"- just\n- a\n- list\n",
            "Expected a YAML mapping",
            id="not-mapping",
        ),
    ],
)
async def test_solve_environment_yml_invalid_body(
    client, body, expected_fragment
):
    resp = await client.post(
        "/solve/environment-yml",
        content=body,
        headers={"content-type": "application/x-yaml"},
    )
    assert resp.status_code == 400
    assert expected_fragment in resp.json()["error"]


@pytest.mark.anyio
async def test_solve_specs_invalid_content_length(client):
    resp = await client.post(
        "/solve",
        content=b'{"dependencies": ["zlib"]}',
        headers={
            "content-type": "application/json",
            "content-length": "not-a-number",
        },
    )
    assert resp.status_code == 413
    assert "Content-Length" in resp.json()["error"]


@pytest.mark.anyio
async def test_solve_environment_yml_invalid_channels(client):
    yml = b"""\
name: test
channels:
  - 123
dependencies:
  - zlib
"""
    resp = await client.post(
        "/solve/environment-yml?platform=linux-64",
        content=yml,
        headers={"content-type": "application/x-yaml"},
    )
    assert resp.status_code == 400
    assert "channels" in resp.json()["error"]
