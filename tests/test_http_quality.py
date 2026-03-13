"""Tests for direct-serve HTTP quality features such as gzip compression."""

from fastapi.testclient import TestClient

from app.main import app


def test_openapi_json_is_gzipped_when_client_accepts_gzip():
    with TestClient(app) as client:
        response = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
