"""Tests for direct-serve HTTP quality features such as gzip compression."""

from fastapi.testclient import TestClient

from app.main import app


def test_openapi_json_is_gzipped_when_client_accepts_gzip():
    with TestClient(app) as client:
        response = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"


def test_custom_swagger_docs_page_is_served():
    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 200
    assert "RemoteTerm API" in response.text
    assert "SwaggerUIBundle" in response.text
    assert 'url: "openapi.json"' in response.text
    assert 'href="api/health"' in response.text
    assert "repeating-linear-gradient" not in response.text
    assert "background-size: 32px 32px" not in response.text


def test_openapi_includes_docs_metadata():
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    data = response.json()
    assert data["info"]["description"].startswith("RemoteTerm exposes")
    assert "/docs" not in data["paths"]
    tags = {tag["name"]: tag["description"] for tag in data["tags"]}
    assert tags["messages"].startswith("Message history")
    assert tags["radio"].startswith("Radio configuration")


def test_openapi_documents_common_error_responses():
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    data = response.json()
    responses = data["paths"]["/api/messages/channel"]["post"]["responses"]
    assert responses["400"]["description"] == "Bad request"
    assert responses["423"]["description"] == "Radio unavailable or locked"
    assert responses["500"]["description"] == "Server error"
