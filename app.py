# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
import sqlite3, hashlib, secrets, time, os, json
import requests as req

app = Flask(__name__)
BASE = Path(__file__).parent
DB = BASE / 'data' / 'valluvan.db'
DB.parent.mkdir(exist_ok=True)
MINIMAX_KEY = os.environ.get('MINIMAX_API_KEY', '')

def get_db():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, password TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS charts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                name TEXT NOT NULL, birth_date TEXT, birth_time TEXT,
                birth_place TEXT, chart_data TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                chart_id INTEGER, type TEXT, content TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        ''')
        exists = c.execute("SELECT id FROM users WHERE email='admin@valluvan.app'").fetchone()
        if not exists:
            h = hashlib.sha256(b'admin123').hexdigest()
            c.execute("INSERT INTO users (id,email,name,password) VALUES (?,?,?,?)", ('admin','admin@valluvan.app','Admin',h))

init_db()
SESSIONS = {}

def make_token(uid):
    t = secrets.token_urlsafe(32)
    SESSIONS[t] = {'uid': uid, 'exp': time.time() + 86400 * 7}
    return t

def get_user():
    token = request.headers.get('Authorization','').replace('Bearer ','').strip()
    session = SESSIONS.get(token)
    if not session or session['exp'] < time.time():
        return None
    with get_db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (session['uid'],)).fetchone()

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_user():
            return jsonify({'error':'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    r.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
    return r

@app.route('/api/auth/login', methods=['POST','OPTIONS'])
def login():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.get_json() or {}
    h = hashlib.sha256(data.get('password','').encode()).hexdigest()
    with get_db() as c:
        user = c.execute("SELECT * FROM users WHERE email=? AND password=?", (data.get('email',''), h)).fetchone()
    if not user: return jsonify({'error':'Invalid credentials'}), 401
    return jsonify({'token': make_token(user['id']), 'user': {'id':user['id'],'email':user['email'],'name':user['name']}})

@app.route('/api/auth/register', methods=['POST','OPTIONS'])
def register():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.get_json() or {}
    email = data.get('email','').strip()
    name = data.get('name','').strip()
    pwd = data.get('password','').strip()
    if not email or not name or not pwd: return jsonify({'error':'All fields required'}), 400
    h = hashlib.sha256(pwd.encode()).hexdigest()
    uid = secrets.token_hex(8)
    try:
        with get_db() as c:
            c.execute("INSERT INTO users (id,email,name,password) VALUES (?,?,?,?)", (uid,email,name,h))
        return jsonify({'token': make_token(uid), 'user': {'id':uid,'email':email,'name':name}})
    except sqlite3.IntegrityError:
        return jsonify({'error':'Email already exists'}), 409

@app.route('/api/charts', methods=['GET'])
@require_auth
def get_charts():
    user = get_user()
    with get_db() as c:
        rows = c.execute("SELECT * FROM charts WHERE user_id=? ORDER BY created_at DESC", (user['id'],)).fetchall()
    return jsonify({'charts': [dict(r) for r in rows]})

@app.route('/api/charts', methods=['POST'])
@require_auth
def create_chart():
    user = get_user()
    data = request.get_json() or {}
    name = data.get('name','My Chart')
    birth_date = data.get('birth_date','')
    birth_time = data.get('birth_time','')
    birth_place = data.get('birth_place','')
    rasis = ['Mesha','Vrishabha','Mithuna','Kataka','Simha','Kanya','Tula','Vrischika','Dhanus','Makara','Kumbha','Meena']
    planets = ['Sun','Moon','Mars','Mercury','Jupiter','Venus','Saturn','Rahu','Ketu']
    import random
    seed = int(hashlib.md5(f"{birth_date}{birth_time}{birth_place}".encode()).hexdigest(), 16)
    random.seed(seed)
    chart_data = {'ascendant': random.choice(rasis), 'planets': {p: {'rasi': random.choice(rasis), 'house': random.randint(1,12)} for p in planets}}
    with get_db() as c:
        cur = c.execute("INSERT INTO charts (user_id,name,birth_date,birth_time,birth_place,chart_data) VALUES (?,?,?,?,?,?)",
                        (user['id'],name,birth_date,birth_time,birth_place,json.dumps(chart_data)))
        row = c.execute("SELECT * FROM charts WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify({'chart': dict(row)})

@app.route('/api/readings', methods=['POST'])
@require_auth
def create_reading():
    user = get_user()
    data = request.get_json() or {}
    chart_id = data.get('chart_id')
    rtype = data.get('type','general')
    with get_db() as c:
        chart = c.execute("SELECT * FROM charts WHERE id=? AND user_id=?", (chart_id, user['id'])).fetchone()
    if not chart: return jsonify({'error':'Chart not found'}), 404
    chart_data = json.loads(chart['chart_data'])
    prompt = f"You are Valluvan, a Tamil Vedic astrologer. Give a {rtype} reading for {chart['name']} born {chart['birth_date']} {chart['birth_time']} at {chart['birth_place']}. Ascendant: {chart_data.get('ascendant')}. 3 paragraphs, warm and specific."
    content = ''
    if MINIMAX_KEY:
        try:
            r = req.post('https://api.minimax.io/anthropic/v1/messages',
                json={'model':'MiniMax-M2.5','max_tokens':1024,'messages':[{'role':'user','content':prompt}]},
                headers={'x-api-key':MINIMAX_KEY,'Content-Type':'application/json','anthropic-version':'2023-06-01'}, timeout=30)
            blocks = r.json().get('content',[])
            content = next((b['text'] for b in blocks if b.get('type')=='text'), '')
        except Exception as e:
            content = f'Reading unavailable: {e}'
    else:
        content = f'Valluvan reading for {chart["name"]}: Your ascendant in {chart_data.get("ascendant")} indicates strong character. The stars favor growth and achievement in your path.'
    with get_db() as c:
        cur = c.execute("INSERT INTO readings (user_id,chart_id,type,content) VALUES (?,?,?,?)", (user['id'],chart_id,rtype,content))
        row = c.execute("SELECT * FROM readings WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify({'reading': dict(row)})

@app.route('/api/horoscope/daily')
def daily():
    rasis = ['Mesha','Vrishabha','Mithuna','Kataka','Simha','Kanya','Tula','Vrischika','Dhanus','Makara','Kumbha','Meena']
    today = time.strftime('%Y-%m-%d')
    return jsonify({'date':today,'horoscopes':{r:{'rasi':r,'date':today,'prediction':f'Today is favorable for {r}. Trust your instincts and stay focused.','lucky_number':hash(f'{r}{today}')%9+1} for r in rasis}})

@app.route('/health')
def health():
    return jsonify({'status':'ok','service':'valluvan','port':5000})

@app.route('/', defaults={'path':''})
@app.route('/<path:path>')
def frontend(path):
    dist = BASE / 'frontend' / 'dist'
    if dist.exists():
        f = dist / path
        if f.exists() and f.is_file():
            return send_from_directory(str(f.parent), f.name)
        return send_from_directory(str(dist), 'index.html')
    return jsonify({'service':'Valluvan Astrologer','status':'running'})

if __name__ == '__main__':
    print('Valluvan Astrologer starting on port 5000...')
    app.run(host='0.0.0.0', port=5000, debug=False)