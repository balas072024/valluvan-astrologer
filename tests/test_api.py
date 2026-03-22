"""
Tests for Valluvan Astrologer API.
"""

import json
import os
import sys
import tempfile

import pytest

# Ensure the app module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app, calculate_rasi, RASI_DATA


@pytest.fixture(autouse=True)
def client(tmp_path):
    """Create a test client with a fresh temporary database for each test."""
    db_path = str(tmp_path / "test_valluvan.db")
    app.config["TESTING"] = True
    app.config["DATABASE_PATH"] = db_path
    # Disable rate limiting for tests by setting high limit
    app.config["RATE_LIMIT_PER_MINUTE"] = 9999

    with app.app_context():
        from app import init_db
        init_db()

    with app.test_client() as client:
        yield client


def register_user(client, username="testuser", email="test@example.com", password="password123"):
    """Helper to register a user and return the response."""
    return client.post(
        "/api/auth/register",
        data=json.dumps({"username": username, "email": email, "password": password}),
        content_type="application/json",
    )


def login_user(client, username="testuser", password="password123"):
    """Helper to login and return the response."""
    return client.post(
        "/api/auth/login",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


def get_token(client):
    """Helper to register and get a JWT token."""
    resp = register_user(client)
    return json.loads(resp.data)["token"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# 1. Health endpoint
# ---------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "healthy"
    assert "Valluvan" in data["service"]


def test_health_alias(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# 2. Registration
# ---------------------------------------------------------------------------

def test_register_success(client):
    resp = register_user(client)
    assert resp.status_code == 201
    data = json.loads(resp.data)
    assert "token" in data
    assert data["user"]["username"] == "testuser"
    assert data["user"]["email"] == "test@example.com"


def test_register_duplicate(client):
    register_user(client)
    resp = register_user(client)
    assert resp.status_code == 409
    data = json.loads(resp.data)
    assert "already exists" in data["error"]


def test_register_validation_short_username(client):
    resp = register_user(client, username="ab", email="x@y.com", password="123456")
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert "details" in data


def test_register_validation_bad_email(client):
    resp = register_user(client, username="gooduser", email="bademail", password="123456")
    assert resp.status_code == 400


def test_register_validation_short_password(client):
    resp = register_user(client, username="gooduser", email="a@b.com", password="12")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 3. Login
# ---------------------------------------------------------------------------

def test_login_success(client):
    register_user(client)
    resp = login_user(client)
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "token" in data
    assert data["user"]["username"] == "testuser"


def test_login_wrong_password(client):
    register_user(client)
    resp = login_user(client, password="wrongpassword")
    assert resp.status_code == 401
    data = json.loads(resp.data)
    assert "Invalid" in data["error"]


def test_login_nonexistent_user(client):
    resp = login_user(client, username="ghost", password="whatever")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 4. Birth Chart
# ---------------------------------------------------------------------------

def test_create_chart(client):
    token = get_token(client)
    resp = client.post(
        "/api/chart",
        data=json.dumps({
            "name": "Arjun",
            "birth_date": "1990-04-20",
            "birth_time": "06:30",
            "birth_place": "Chennai, Tamil Nadu",
        }),
        headers=auth_header(token),
    )
    assert resp.status_code == 201
    data = json.loads(resp.data)
    chart = data["chart"]
    assert chart["rasi"]["name"] == "Mesham"
    assert "nakshatra" in chart
    assert "ruling_planet" in chart["rasi"]


def test_chart_requires_auth(client):
    resp = client.post(
        "/api/chart",
        data=json.dumps({
            "name": "Arjun",
            "birth_date": "1990-04-20",
            "birth_time": "06:30",
            "birth_place": "Chennai",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_chart_input_validation(client):
    token = get_token(client)
    # Missing required fields
    resp = client.post(
        "/api/chart",
        data=json.dumps({"name": ""}),
        headers=auth_header(token),
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert "details" in data
    assert len(data["details"]) >= 3  # name, date, time, place errors


def test_chart_bad_date_format(client):
    token = get_token(client)
    resp = client.post(
        "/api/chart",
        data=json.dumps({
            "name": "Test",
            "birth_date": "20-04-1990",
            "birth_time": "06:30",
            "birth_place": "Chennai",
        }),
        headers=auth_header(token),
    )
    assert resp.status_code == 400


def test_get_charts(client):
    token = get_token(client)
    # Create two charts
    for name in ["Arjun", "Priya"]:
        client.post(
            "/api/chart",
            data=json.dumps({
                "name": name,
                "birth_date": "1990-04-20",
                "birth_time": "06:30",
                "birth_place": "Chennai",
            }),
            headers=auth_header(token),
        )
    resp = client.get("/api/chart", headers=auth_header(token))
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert len(data["charts"]) == 2


# ---------------------------------------------------------------------------
# 5. Horoscope
# ---------------------------------------------------------------------------

def test_get_horoscope_all_rasis(client):
    """Test that horoscopes can be fetched for all 12 rasis."""
    rasi_names = [r["name"] for r in RASI_DATA]
    for name in rasi_names:
        resp = client.get(f"/api/horoscope/{name}")
        assert resp.status_code == 200, f"Failed for rasi: {name}"
        data = json.loads(resp.data)
        h = data["horoscope"]
        assert h["rasi"] == name
        assert "prediction" in h
        assert "advice" in h
        assert "lucky_number" in h
        assert "lucky_color" in h
        assert "compatible_rasi" in h


def test_horoscope_invalid_rasi(client):
    resp = client.get("/api/horoscope/InvalidRasi")
    assert resp.status_code == 400


def test_get_all_horoscopes(client):
    resp = client.get("/api/horoscope")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert len(data["horoscopes"]) == 12


# ---------------------------------------------------------------------------
# 6. Reading History
# ---------------------------------------------------------------------------

def test_reading_history(client):
    token = get_token(client)
    # Fetch a horoscope with auth to create a reading
    client.get("/api/horoscope/Mesham", headers=auth_header(token))
    client.get("/api/horoscope/Simmam", headers=auth_header(token))

    resp = client.get("/api/readings", headers=auth_header(token))
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert len(data["readings"]) == 2


def test_reading_history_requires_auth(client):
    resp = client.get("/api/readings")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 7. Rasi Calculation Verification
# ---------------------------------------------------------------------------

def test_rasi_calculation(client):
    """Verify rasi calculations for specific dates."""
    # Mesham: Apr 14 - May 14 => April 20 should be Mesham
    assert calculate_rasi("2000-04-20")["name"] == "Mesham"

    # Simmam: Aug 17 - Sep 16 => Sept 1 should be Simmam
    assert calculate_rasi("2000-09-01")["name"] == "Simmam"

    # Dhanusu: Dec 16 - Jan 13 => Dec 25 should be Dhanusu
    assert calculate_rasi("2000-12-25")["name"] == "Dhanusu"

    # Dhanusu wraps to Jan => Jan 5 should be Dhanusu
    assert calculate_rasi("2000-01-05")["name"] == "Dhanusu"

    # Magaram: Jan 14 - Feb 12 => Feb 1 should be Magaram
    assert calculate_rasi("2000-02-01")["name"] == "Magaram"

    # Meenam: Mar 15 - Apr 13 => Mar 20 should be Meenam
    assert calculate_rasi("2000-03-20")["name"] == "Meenam"

    # Kanni: Sep 17 - Oct 17 => Oct 10 should be Kanni
    assert calculate_rasi("2000-10-10")["name"] == "Kanni"

    # Rishabam: May 15 - Jun 14 => May 20 should be Rishabam
    assert calculate_rasi("2000-05-20")["name"] == "Rishabam"

    # Kadagam: Jul 17 - Aug 16 => Aug 1 should be Kadagam
    assert calculate_rasi("2000-08-01")["name"] == "Kadagam"

    # Kumbam: Feb 13 - Mar 14 => Mar 1 should be Kumbam
    assert calculate_rasi("2000-03-01")["name"] == "Kumbam"

    # Thulam: Oct 18 - Nov 15 => Nov 1 should be Thulam
    assert calculate_rasi("2000-11-01")["name"] == "Thulam"

    # Viruchigam: Nov 16 - Dec 15 => Dec 1 should be Viruchigam
    assert calculate_rasi("2000-12-01")["name"] == "Viruchigam"


def test_rasi_boundary_dates(client):
    """Test boundary dates for rasi calculations."""
    # First day of Mesham
    assert calculate_rasi("2000-04-14")["name"] == "Mesham"
    # Last day of Mesham
    assert calculate_rasi("2000-05-14")["name"] == "Mesham"
    # First day of Rishabam
    assert calculate_rasi("2000-05-15")["name"] == "Rishabam"
    # Last day of Meenam / day before Mesham
    assert calculate_rasi("2000-04-13")["name"] == "Meenam"


# ---------------------------------------------------------------------------
# 8. Input Validation
# ---------------------------------------------------------------------------

def test_input_validation(client):
    """Test various input validation scenarios."""
    # Register with missing fields
    resp = client.post(
        "/api/auth/register",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400

    # Login with empty body
    resp = client.post(
        "/api/auth/login",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400

    # Bad time format
    token = get_token(client)
    resp = client.post(
        "/api/chart",
        data=json.dumps({
            "name": "Test",
            "birth_date": "1990-04-20",
            "birth_time": "25:99",
            "birth_place": "Chennai",
        }),
        headers=auth_header(token),
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 9. CORS & Misc
# ---------------------------------------------------------------------------

def test_cors_headers(client):
    resp = client.get("/api/health")
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"


def test_rasi_list_endpoint(client):
    resp = client.get("/api/rasis")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert len(data["rasis"]) == 12
    names = [r["name"] for r in data["rasis"]]
    assert "Mesham" in names
    assert "Meenam" in names


def test_frontend_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Valluvan" in resp.data
