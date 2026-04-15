"""Tests for conda_resolve.app (Starlette endpoints)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Route

from conda_resolve.app import (
    cache_clear,
    health,
    lifespan,
    solve_environment_yml,
    solve_specs,
)


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
            Route("/cache/clear", cache_clear, methods=["POST"]),
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
        pytest.param(
            ["linux-64", "osx-arm64"],
            id="multi",
            marks=pytest.mark.crossplatform,
        ),
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
@pytest.mark.parametrize(
    "query, expected_count, check_names",
    [
        pytest.param(
            "?platform=linux-64", 1, ["python", "numpy"], id="single"
        ),
        pytest.param(
            "?platform=linux-64&platform=osx-arm64",
            2,
            None,
            id="multi",
            marks=pytest.mark.crossplatform,
        ),
    ],
)
async def test_solve_environment_yml(
    client, environment_yml_bytes, query, expected_count, check_names
):
    resp = await client.post(
        f"/solve/environment-yml{query}",
        content=environment_yml_bytes,
        headers={"content-type": "application/x-yaml"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == expected_count
    if check_names:
        names = [p["name"] for p in data[0]["packages"]]
        for name in check_names:
            assert name in names


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


@pytest.mark.anyio
async def test_cache_clear(client):
    resp = await client.post("/cache/clear")
    assert resp.status_code == 200
    data = resp.json()
    assert "cleared" in data
    assert isinstance(data["cleared"], int)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "endpoint, kwargs",
    [
        pytest.param(
            "/solve",
            {
                "json": {
                    "channels": ["conda-forge"],
                    "dependencies": ["zlib"],
                    "platforms": ["linux-64"],
                }
            },
            id="specs",
        ),
        pytest.param(
            "/solve/environment-yml?platform=linux-64",
            {
                "content": (
                    b"name: test\nchannels:\n"
                    b"  - conda-forge\ndependencies:\n  - zlib\n"
                ),
                "headers": {"content-type": "application/x-yaml"},
            },
            id="env-yml",
        ),
    ],
)
async def test_internal_error(client, monkeypatch, endpoint, kwargs):
    monkeypatch.setattr(
        "conda_resolve.app.solve",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    resp = await client.post(endpoint, **kwargs)
    assert resp.status_code == 500
    assert resp.json()["error"] == "Internal solver error"


@pytest.mark.anyio
async def test_lifespan_initializes(monkeypatch):
    import conda_resolve.app as app_module

    warmup_calls = []

    def fake_warmup(channels, platforms):
        warmup_calls.append((channels, platforms))

    monkeypatch.setattr(app_module, "warmup", fake_warmup)
    dummy_app = Starlette()
    async with lifespan(dummy_app):
        assert app_module.solver_limiter is not None
    assert len(warmup_calls) == 1
