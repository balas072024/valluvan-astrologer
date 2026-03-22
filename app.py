"""
Valluvan Astrologer - Vedic Astrology SaaS Application
A Flask-based API for Vedic (Tamil) astrology birth charts, horoscopes, and readings.
"""

import os
import sqlite3
import hashlib
import time
import json
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict

from flask import Flask, request, jsonify, g, render_template
from dotenv import load_dotenv
import bcrypt
import jwt

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
app.config["JWT_SECRET"] = os.getenv("JWT_SECRET", "dev-jwt-secret")
app.config["DATABASE_PATH"] = os.getenv("DATABASE_PATH", "valluvan.db")
app.config["RATE_LIMIT_PER_MINUTE"] = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response


# ---------------------------------------------------------------------------
# Rate Limiting (simple in-memory, per-IP)
# ---------------------------------------------------------------------------

_rate_store: dict[str, list[float]] = defaultdict(list)


@app.before_request
def rate_limit():
    if request.path == "/api/health" or request.method == "OPTIONS":
        return None
    client_ip = request.headers.get('CF-Connecting-IP') or request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr or "unknown"
    now = time.time()
    window = 60.0
    limit = app.config["RATE_LIMIT_PER_MINUTE"]

    timestamps = _rate_store[client_ip]
    # Prune old entries
    _rate_store[client_ip] = [t for t in timestamps if now - t < window]
    if len(_rate_store[client_ip]) >= limit:
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    _rate_store[client_ip].append(now)
    return None


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS birth_charts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            birth_date TEXT NOT NULL,
            birth_time TEXT NOT NULL,
            birth_place TEXT NOT NULL,
            rasi TEXT NOT NULL,
            nakshatra TEXT NOT NULL,
            ruling_planet TEXT NOT NULL,
            chart_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reading_type TEXT NOT NULL,
            rasi TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    db.commit()


with app.app_context():
    init_db()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_token(user_id: int, username: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(hours=24),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, app.config["JWT_SECRET"], algorithm="HS256")


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            return jsonify({"error": "Authentication token is missing"}), 401
        try:
            data = jwt.decode(token, app.config["JWT_SECRET"], algorithms=["HS256"])
            g.current_user_id = data["user_id"]
            g.current_username = data["username"]
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Vedic Astrology Data & Calculations
# ---------------------------------------------------------------------------

RASI_DATA = [
    {
        "name": "Mesham",
        "english": "Aries",
        "ruling_planet": "Sevvai (Mars)",
        "start_month": 4, "start_day": 14,
        "end_month": 5, "end_day": 14,
    },
    {
        "name": "Rishabam",
        "english": "Taurus",
        "ruling_planet": "Sukran (Venus)",
        "start_month": 5, "start_day": 15,
        "end_month": 6, "end_day": 14,
    },
    {
        "name": "Mithunam",
        "english": "Gemini",
        "ruling_planet": "Budhan (Mercury)",
        "start_month": 6, "start_day": 15,
        "end_month": 7, "end_day": 16,
    },
    {
        "name": "Kadagam",
        "english": "Cancer",
        "ruling_planet": "Chandran (Moon)",
        "start_month": 7, "start_day": 17,
        "end_month": 8, "end_day": 16,
    },
    {
        "name": "Simmam",
        "english": "Leo",
        "ruling_planet": "Suryan (Sun)",
        "start_month": 8, "start_day": 17,
        "end_month": 9, "end_day": 16,
    },
    {
        "name": "Kanni",
        "english": "Virgo",
        "ruling_planet": "Budhan (Mercury)",
        "start_month": 9, "start_day": 17,
        "end_month": 10, "end_day": 17,
    },
    {
        "name": "Thulam",
        "english": "Libra",
        "ruling_planet": "Sukran (Venus)",
        "start_month": 10, "start_day": 18,
        "end_month": 11, "end_day": 15,
    },
    {
        "name": "Viruchigam",
        "english": "Scorpio",
        "ruling_planet": "Sevvai (Mars)",
        "start_month": 11, "start_day": 16,
        "end_month": 12, "end_day": 15,
    },
    {
        "name": "Dhanusu",
        "english": "Sagittarius",
        "ruling_planet": "Guru (Jupiter)",
        "start_month": 12, "start_day": 16,
        "end_month": 1, "end_day": 13,
    },
    {
        "name": "Magaram",
        "english": "Capricorn",
        "ruling_planet": "Sani (Saturn)",
        "start_month": 1, "start_day": 14,
        "end_month": 2, "end_day": 12,
    },
    {
        "name": "Kumbam",
        "english": "Aquarius",
        "ruling_planet": "Sani (Saturn)",
        "start_month": 2, "start_day": 13,
        "end_month": 3, "end_day": 14,
    },
    {
        "name": "Meenam",
        "english": "Pisces",
        "ruling_planet": "Guru (Jupiter)",
        "start_month": 3, "start_day": 15,
        "end_month": 4, "end_day": 13,
    },
]

NAKSHATRAS = [
    {"name": "Ashwini", "ruling_planet": "Ketu"},
    {"name": "Bharani", "ruling_planet": "Sukran (Venus)"},
    {"name": "Krittika", "ruling_planet": "Suryan (Sun)"},
    {"name": "Rohini", "ruling_planet": "Chandran (Moon)"},
    {"name": "Mrigashira", "ruling_planet": "Sevvai (Mars)"},
    {"name": "Ardra", "ruling_planet": "Rahu"},
    {"name": "Punarvasu", "ruling_planet": "Guru (Jupiter)"},
    {"name": "Pushya", "ruling_planet": "Sani (Saturn)"},
    {"name": "Ashlesha", "ruling_planet": "Budhan (Mercury)"},
    {"name": "Magha", "ruling_planet": "Ketu"},
    {"name": "Purva Phalguni", "ruling_planet": "Sukran (Venus)"},
    {"name": "Uttara Phalguni", "ruling_planet": "Suryan (Sun)"},
    {"name": "Hasta", "ruling_planet": "Chandran (Moon)"},
    {"name": "Chitra", "ruling_planet": "Sevvai (Mars)"},
    {"name": "Swati", "ruling_planet": "Rahu"},
    {"name": "Vishakha", "ruling_planet": "Guru (Jupiter)"},
    {"name": "Anuradha", "ruling_planet": "Sani (Saturn)"},
    {"name": "Jyeshtha", "ruling_planet": "Budhan (Mercury)"},
    {"name": "Mula", "ruling_planet": "Ketu"},
    {"name": "Purva Ashadha", "ruling_planet": "Sukran (Venus)"},
    {"name": "Uttara Ashadha", "ruling_planet": "Suryan (Sun)"},
    {"name": "Shravana", "ruling_planet": "Chandran (Moon)"},
    {"name": "Dhanishta", "ruling_planet": "Sevvai (Mars)"},
    {"name": "Shatabhisha", "ruling_planet": "Rahu"},
    {"name": "Purva Bhadrapada", "ruling_planet": "Guru (Jupiter)"},
    {"name": "Uttara Bhadrapada", "ruling_planet": "Sani (Saturn)"},
    {"name": "Revati", "ruling_planet": "Budhan (Mercury)"},
]


def calculate_rasi(birth_date: str) -> dict:
    """Calculate Vedic rasi (sun sign) based on birth date using Tamil Vedic ranges."""
    dt = datetime.strptime(birth_date, "%Y-%m-%d")
    month = dt.month
    day = dt.day

    for rasi in RASI_DATA:
        sm, sd = rasi["start_month"], rasi["start_day"]
        em, ed = rasi["end_month"], rasi["end_day"]

        # Handle wrapping rasi (Dhanusu: Dec 16 - Jan 13)
        if sm > em:
            if (month == sm and day >= sd) or (month == em and day <= ed):
                return rasi
        else:
            if (month == sm and day >= sd) or (month == em and day <= ed):
                return rasi
            if sm < em - 1 and sm < month < em:
                return rasi

    # Fallback (should not happen with correct ranges)
    return RASI_DATA[0]


def calculate_nakshatra(birth_date: str, birth_time: str) -> dict:
    """Calculate nakshatra based on birth date and time (simplified)."""
    dt = datetime.strptime(birth_date, "%Y-%m-%d")
    # Use a deterministic hash of date+time to pick nakshatra
    try:
        t = datetime.strptime(birth_time, "%H:%M")
        minutes = t.hour * 60 + t.minute
    except ValueError:
        minutes = 0
    day_of_year = dt.timetuple().tm_yday
    index = (day_of_year * 27 + minutes) % 27
    return NAKSHATRAS[index]


# Horoscope generation templates
_HOROSCOPE_THEMES = [
    "Career growth and professional achievements shine today.",
    "Relationships deepen as heartfelt conversations bring clarity.",
    "Financial opportunities arise; be mindful of impulsive spending.",
    "Health and wellness demand attention. A walk in nature brings peace.",
    "Creative energy surges; channel it into artistic or intellectual pursuits.",
    "Family bonds strengthen through shared meals and conversations.",
    "A spiritual awakening guides your decisions toward compassion.",
    "Travel plans or new adventures beckon on the horizon.",
    "Education and learning open doors to unexpected opportunities.",
    "Leadership qualities emerge; others look to you for guidance.",
    "Inner peace comes through meditation and self-reflection today.",
    "Social gatherings bring joy and meaningful new connections.",
    "Property or home-related matters move in a favorable direction.",
    "Technology or innovation plays a key role in your success today.",
    "Legal or official matters resolve in your favor with patience.",
    "A long-awaited message or news arrives, bringing relief.",
    "Romantic energies are heightened; express your true feelings.",
    "Business partnerships show promise; trust but verify details.",
    "Ancestral blessings protect and guide your path forward.",
    "Mental clarity improves; tackle complex problems with confidence.",
]

_HOROSCOPE_ADVICE = [
    "Wear red to enhance your energy today.",
    "Chant 'Om Namah Shivaya' for spiritual protection.",
    "Offer prayers at sunrise for maximum benefit.",
    "Avoid making major decisions in the evening hours.",
    "Donate food or clothing to those in need for good karma.",
    "Light a lamp with sesame oil for removing obstacles.",
    "Spend time near water for emotional balance.",
    "Read sacred texts for inner guidance.",
    "Practice gratitude before sleeping tonight.",
    "Avoid arguments; silence is your greatest strength today.",
    "Wear yellow to attract prosperity.",
    "Feed birds or animals for blessings from nature.",
    "Begin new projects in the morning hours for success.",
    "Meditate facing east for spiritual alignment.",
    "Share your blessings with family and friends.",
]


def generate_horoscope(rasi_name: str, date: str | None = None) -> dict:
    """Generate a daily horoscope for a given rasi."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    dt = datetime.strptime(date, "%Y-%m-%d")
    day_of_year = dt.timetuple().tm_yday

    # Find rasi index
    rasi_index = 0
    for i, r in enumerate(RASI_DATA):
        if r["name"].lower() == rasi_name.lower():
            rasi_index = i
            break

    seed = day_of_year * 12 + rasi_index
    theme_idx = seed % len(_HOROSCOPE_THEMES)
    advice_idx = (seed * 7 + rasi_index) % len(_HOROSCOPE_ADVICE)
    lucky_number = ((seed * 3 + 7) % 9) + 1
    lucky_colors = ["Red", "Blue", "Green", "Yellow", "White", "Orange",
                    "Purple", "Gold", "Silver", "Pink", "Maroon", "Teal"]
    lucky_color = lucky_colors[(seed * 5 + rasi_index) % len(lucky_colors)]

    compatibility_idx = (rasi_index + day_of_year) % 12
    compatible_rasi = RASI_DATA[compatibility_idx]["name"]

    return {
        "rasi": rasi_name,
        "date": date,
        "prediction": _HOROSCOPE_THEMES[theme_idx],
        "advice": _HOROSCOPE_ADVICE[advice_idx],
        "lucky_number": lucky_number,
        "lucky_color": lucky_color,
        "compatible_rasi": compatible_rasi,
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_registration(data: dict) -> list[str]:
    errors = []
    if not data.get("username") or len(data["username"].strip()) < 3:
        errors.append("Username must be at least 3 characters")
    if not data.get("email") or "@" not in data.get("email", ""):
        errors.append("A valid email is required")
    if not data.get("password") or len(data["password"]) < 6:
        errors.append("Password must be at least 6 characters")
    return errors


def validate_chart_input(data: dict) -> list[str]:
    errors = []
    if not data.get("name") or len(data["name"].strip()) < 1:
        errors.append("Name is required")
    if not data.get("birth_date"):
        errors.append("Birth date is required")
    else:
        try:
            datetime.strptime(data["birth_date"], "%Y-%m-%d")
        except ValueError:
            errors.append("Birth date must be in YYYY-MM-DD format")
    if not data.get("birth_time"):
        errors.append("Birth time is required")
    else:
        try:
            datetime.strptime(data["birth_time"], "%H:%M")
        except ValueError:
            errors.append("Birth time must be in HH:MM format")
    if not data.get("birth_place") or len(data["birth_place"].strip()) < 1:
        errors.append("Birth place is required")
    return errors


# ---------------------------------------------------------------------------
# Routes - Health
# ---------------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "Valluvan Vedic Astrology API",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
    })


# Alias at /health as well
@app.route("/health", methods=["GET"])
def health_alias():
    return health()


# ---------------------------------------------------------------------------
# Routes - Auth
# ---------------------------------------------------------------------------

@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    errors = validate_registration(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    username = data["username"].strip()
    email = data["email"].strip().lower()
    password_hash = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()

    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email, password_hash),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username or email already exists"}), 409

    user = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    token = create_token(user["id"], username)

    return jsonify({
        "message": "Registration successful",
        "token": token,
        "user": {"id": user["id"], "username": username, "email": email},
    }), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not user:
        return jsonify({"error": "Invalid username or password"}), 401

    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"error": "Invalid username or password"}), 401

    token = create_token(user["id"], user["username"])
    return jsonify({
        "message": "Login successful",
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
        },
    })


# ---------------------------------------------------------------------------
# Routes - Birth Chart
# ---------------------------------------------------------------------------

@app.route("/api/chart", methods=["POST"])
@token_required
def create_chart():
    data = request.get_json(silent=True) or {}
    errors = validate_chart_input(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    name = data["name"].strip()
    birth_date = data["birth_date"]
    birth_time = data["birth_time"]
    birth_place = data["birth_place"].strip()

    rasi = calculate_rasi(birth_date)
    nakshatra = calculate_nakshatra(birth_date, birth_time)

    chart_data = {
        "name": name,
        "birth_date": birth_date,
        "birth_time": birth_time,
        "birth_place": birth_place,
        "rasi": {
            "name": rasi["name"],
            "english": rasi["english"],
            "ruling_planet": rasi["ruling_planet"],
        },
        "nakshatra": {
            "name": nakshatra["name"],
            "ruling_planet": nakshatra["ruling_planet"],
        },
    }

    db = get_db()
    cursor = db.execute(
        """INSERT INTO birth_charts
           (user_id, name, birth_date, birth_time, birth_place,
            rasi, nakshatra, ruling_planet, chart_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            g.current_user_id,
            name,
            birth_date,
            birth_time,
            birth_place,
            rasi["name"],
            nakshatra["name"],
            rasi["ruling_planet"],
            json.dumps(chart_data),
        ),
    )
    db.commit()

    chart_data["id"] = cursor.lastrowid
    return jsonify({"message": "Birth chart created", "chart": chart_data}), 201


@app.route("/api/chart", methods=["GET"])
@token_required
def get_charts():
    db = get_db()
    charts = db.execute(
        "SELECT * FROM birth_charts WHERE user_id = ? ORDER BY created_at DESC",
        (g.current_user_id,),
    ).fetchall()
    result = []
    for c in charts:
        result.append({
            "id": c["id"],
            "name": c["name"],
            "birth_date": c["birth_date"],
            "birth_time": c["birth_time"],
            "birth_place": c["birth_place"],
            "rasi": c["rasi"],
            "nakshatra": c["nakshatra"],
            "ruling_planet": c["ruling_planet"],
            "created_at": c["created_at"],
        })
    return jsonify({"charts": result})


@app.route("/api/chart/<int:chart_id>", methods=["GET"])
@token_required
def get_chart(chart_id):
    db = get_db()
    chart = db.execute(
        "SELECT * FROM birth_charts WHERE id = ? AND user_id = ?",
        (chart_id, g.current_user_id),
    ).fetchone()
    if not chart:
        return jsonify({"error": "Chart not found"}), 404

    chart_data = json.loads(chart["chart_data"]) if chart["chart_data"] else {}
    chart_data["id"] = chart["id"]
    chart_data["created_at"] = chart["created_at"]
    return jsonify({"chart": chart_data})


@app.route("/api/chart/<int:chart_id>", methods=["DELETE"])
@token_required
def delete_chart(chart_id):
    db = get_db()
    chart = db.execute(
        "SELECT id FROM birth_charts WHERE id = ? AND user_id = ?",
        (chart_id, g.current_user_id),
    ).fetchone()
    if not chart:
        return jsonify({"error": "Chart not found"}), 404

    db.execute("DELETE FROM birth_charts WHERE id = ?", (chart_id,))
    db.commit()
    return jsonify({"message": "Chart deleted"})


# ---------------------------------------------------------------------------
# Routes - Horoscope
# ---------------------------------------------------------------------------

@app.route("/api/horoscope/<rasi_name>", methods=["GET"])
def get_horoscope(rasi_name: str):
    # Validate rasi name
    valid_rasis = [r["name"].lower() for r in RASI_DATA]
    if rasi_name.lower() not in valid_rasis:
        return jsonify({"error": f"Invalid rasi: {rasi_name}"}), 400

    # Capitalise correctly
    canonical = None
    for r in RASI_DATA:
        if r["name"].lower() == rasi_name.lower():
            canonical = r["name"]
            break

    date = request.args.get("date")
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Date must be in YYYY-MM-DD format"}), 400

    horoscope = generate_horoscope(canonical, date)

    # If user is authenticated, save as reading
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = jwt.decode(token, app.config["JWT_SECRET"], algorithms=["HS256"])
            db = get_db()
            db.execute(
                "INSERT INTO readings (user_id, reading_type, rasi, content) VALUES (?, ?, ?, ?)",
                (payload["user_id"], "daily_horoscope", canonical, json.dumps(horoscope)),
            )
            db.commit()
        except (jwt.InvalidTokenError, Exception):
            pass  # Unauthenticated access is fine

    return jsonify({"horoscope": horoscope})


@app.route("/api/horoscope", methods=["GET"])
def get_all_horoscopes():
    date = request.args.get("date")
    horoscopes = []
    for r in RASI_DATA:
        horoscopes.append(generate_horoscope(r["name"], date))
    return jsonify({"horoscopes": horoscopes})


# ---------------------------------------------------------------------------
# Routes - Reading History
# ---------------------------------------------------------------------------

@app.route("/api/readings", methods=["GET"])
@token_required
def get_readings():
    db = get_db()
    readings = db.execute(
        "SELECT * FROM readings WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (g.current_user_id,),
    ).fetchall()
    result = []
    for r in readings:
        result.append({
            "id": r["id"],
            "reading_type": r["reading_type"],
            "rasi": r["rasi"],
            "content": json.loads(r["content"]),
            "created_at": r["created_at"],
        })
    return jsonify({"readings": result})


# ---------------------------------------------------------------------------
# Routes - Rasi Info
# ---------------------------------------------------------------------------

@app.route("/api/rasis", methods=["GET"])
def get_rasis():
    rasis = []
    for r in RASI_DATA:
        rasis.append({
            "name": r["name"],
            "english": r["english"],
            "ruling_planet": r["ruling_planet"],
            "period": f"{_month_name(r['start_month'])} {r['start_day']} - {_month_name(r['end_month'])} {r['end_day']}",
        })
    return jsonify({"rasis": rasis})


def _month_name(m: int) -> str:
    return ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1]


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    debug = os.getenv("DEBUG", "false").lower() == "true"
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    app.run(host=host, port=port, debug=debug)
