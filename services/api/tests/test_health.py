"""P0 acceptance, as a test: the health endpoint answers without a database."""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_describes_the_product():
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "ClimatePass AI"


def test_city_config_loads_abuja():
    """cities.yaml is the single source of truth for every AOI and CRS choice."""
    from core.config import get_city

    abuja = get_city("abuja")
    assert abuja.utm_epsg == 32632
    assert abuja.epsg_metric == "EPSG:32632"

    min_lon, min_lat, max_lon, max_lat = abuja.bbox
    assert min_lon < max_lon and min_lat < max_lat
    # Abuja sits within UTM zone 32N (6°E–12°E), which is why 32632 is correct.
    assert 6.0 < min_lon < 12.0

    # The municipal fallback must contain the demo route (Central Area -> Lugbe).
    m_min_lon, m_min_lat, m_max_lon, m_max_lat = abuja.bbox_for("municipal")
    for name, lat, lon in [("Central Area", 9.0579, 7.4913), ("Lugbe", 8.9816, 7.3736)]:
        assert m_min_lon <= lon <= m_max_lon, f"{name} outside municipal AOI"
        assert m_min_lat <= lat <= m_max_lat, f"{name} outside municipal AOI"
