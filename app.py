import os, sqlite3, io, json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from deep_translator import GoogleTranslator
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import openpyxl
import requests

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change_me")

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "krishi.db")

TWILIO_SID = os.getenv("TWILIO_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_FROM", "")
TWILIO_DISABLED = os.getenv("TWILIO_DISABLED", "1")
TEXTBELT_KEY = os.getenv("TEXTBELT_KEY", "textbelt")
OPENWEATHER_KEY = os.getenv("OPENWEATHER_KEY", "")

SUPPORTED_LANGS = ["en","hi","kn","te","ta","mr"]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, crop TEXT, income INTEGER, expense INTEGER, created_at TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS farmers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT, village TEXT, language TEXT, subscribed INTEGER, created_at TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS community (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, body TEXT, created_at TEXT
    )''')
    conn.commit()
    conn.close()

def normalize_phone(phone):
    p = "".join(ch for ch in phone if ch.isdigit() or ch=='+')
    digits = "".join(ch for ch in p if ch.isdigit())
    if len(digits)==10 and not p.startswith('+'):
        return "+91"+digits
    if not p.startswith('+') and digits.startswith('91') and len(digits)==12:
        return "+"+digits
    return p

def translate_text(text, lang):
    lang = (lang or "en").lower()
    if lang not in SUPPORTED_LANGS or lang=='en':
        return text
    try:
        return GoogleTranslator(source='auto', target=lang).translate(text)
    except Exception:
        return text

def send_via_textbelt(number, message):
    url = "https://textbelt.com/text"
    payload = {'phone': number, 'message': message, 'key': TEXTBELT_KEY}
    try:
        r = requests.post(url, data=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"success":False, "error": str(e)}

@app.route("/")
def home():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

# --------- Auth ---------
@app.route("/signup", methods=["GET","POST"])
def signup():
    init_db()
    if request.method=="POST":
        u = request.form['username'].strip()
        p = request.form['password'].strip()
        if not u or not p:
            flash("Enter username and password")
            return redirect(url_for('signup'))
        conn = get_db()
        try:
            conn.execute("INSERT INTO users (username,password) VALUES (?,?)",(u, generate_password_hash(p)))
            conn.commit()
        except Exception as e:
            flash("User exists")
            return redirect(url_for('signup'))
        conn.close()
        flash("Account created. Please login.")
        return redirect(url_for('login'))
    return render_template("signup.html", year=datetime.now().year)

@app.route("/login", methods=["GET","POST"])
def login():
    init_db()
    if request.method=="POST":
        u = request.form['username'].strip()
        p = request.form['password'].strip()
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        conn.close()
        if row and check_password_hash(row['password'], p):
            session['user_id'] = row['id']
            flash("Logged in")
            return redirect(url_for('dashboard'))
        flash("Invalid credentials")
        return redirect(url_for('login'))
    return render_template("login.html", year=datetime.now().year)

@app.route("/logout")
def logout():
    session.pop('user_id', None)
    flash("Logged out")
    return redirect(url_for('login'))

# --------- Dashboard & Data ---------
@app.route("/dashboard")
def dashboard():
    init_db()
    if 'user_id' not in session:
        return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    records = [dict(r) for r in conn.execute("SELECT crop,income,expense FROM records WHERE user_id=?", (uid,)).fetchall()]
    income = sum(r['income'] for r in records) if records else 0
    expense = sum(r['expense'] for r in records) if records else 0
    profit = income - expense
    # AI tips simple rules
    tips = []
    if expense > income * 0.6 and income>0:
        tips.append("💡 Your expenses are high. Try reducing fertilizer costs.")
    if profit>0:
        tips.append("✅ You are in profit. Consider saving for next season.")
    if any(r['crop'].lower()=='wheat' for r in records):
        tips.append("🌾 Wheat prices are good currently. Consider timing your sale.")
    if not tips:
        tips.append("📊 Add crop records to get personalized tips.")

    # market prices dummy
    market_prices = {"Wheat":2200,"Rice":2800,"Tomato":1500,"Onion":1200,"Sugarcane":3000}

    # community questions
    questions = [dict(q) for q in conn.execute("SELECT title,body FROM community ORDER BY id DESC LIMIT 10").fetchall()]

    conn.close()
    return render_template("dashboard.html", income=income, expense=expense, profit=profit, records=records, tips=tips, market_prices=market_prices, questions=questions, year=datetime.now().year)

@app.route("/add", methods=["GET","POST"])
def add_data():
    init_db()
    if 'user_id' not in session:
        return redirect(url_for('login'))
    uid = session['user_id']
    if request.method=="POST":
        crop = request.form['crop'].strip()
        income = int(request.form['income'] or 0)
        expense = int(request.form['expense'] or 0)
        conn = get_db()
        conn.execute("INSERT INTO records (user_id,crop,income,expense,created_at) VALUES (?,?,?,?,?)",(uid,crop,income,expense, datetime.utcnow().isoformat()))
        conn.commit(); conn.close()
        flash("Record added")
        return redirect(url_for('dashboard'))
    return render_template("add_data.html", year=datetime.now().year)

# --------- Export PDF & Excel ---------
@app.route("/export/pdf")
def export_pdf():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    user = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    rows = [dict(r) for r in conn.execute("SELECT crop,income,expense FROM records WHERE user_id=?", (uid,)).fetchall()]
    conn.close()
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setFont("Helvetica-Bold", 16)
    p.drawString(180, 750, "KrishiLabh Farmer Report")
    p.setFont("Helvetica", 12)
    p.drawString(50, 720, f"User: {user['username']}")
    y = 680
    p.drawString(50, y, "Crop"); p.drawString(200, y, "Income"); p.drawString(300, y, "Expense"); p.drawString(380, y, "Profit")
    for r in rows:
        y -= 20
        p.drawString(50, y, r['crop']); p.drawString(200, y, str(r['income'])); p.drawString(300, y, str(r['expense'])); p.drawString(380, y, str(r['income']-r['expense']))
    p.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="KrishiLabh_Report.pdf", mimetype="application/pdf")

@app.route("/export/excel")
def export_excel():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    user = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    rows = [dict(r) for r in conn.execute("SELECT crop,income,expense FROM records WHERE user_id=?", (uid,)).fetchall()]
    conn.close()
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Report"
    ws.append(["User", user['username']]); ws.append([]); ws.append(["Crop","Income","Expense","Profit"])
    for r in rows: ws.append([r['crop'], r['income'], r['expense'], r['income']-r['expense']])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="KrishiLabh_Report.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# --------- Weather & crop suggestions ---------
def suggest_crops(weather_desc):
    w = weather_desc.lower()
    if "rain" in w or "shower" in w: return ["Rice","Sugarcane","Paddy"]
    if "clear" in w or "sun" in w: return ["Wheat","Maize","Cotton"]
    if "cloud" in w: return ["Groundnut","Soybean","Turmeric"]
    return ["Millets","Pulses"]

@app.route("/weather/<village>")
def weather(village):
    init_db()
    try:
        if OPENWEATHER_KEY:
            url = f"http://api.openweathermap.org/data/2.5/weather?q={village}&appid={OPENWEATHER_KEY}&units=metric"
            res = requests.get(url, timeout=8).json()
            desc = res['weather'][0]['description']; temp = res['main']['temp']
        else:
            desc = "sunny"; temp = 30
        crops = suggest_crops(desc)
        return render_template("weather.html", village=village, weather=desc, temp=temp, crops=crops, year=datetime.now().year)
    except Exception as e:
        return f"Weather error: {e}"

# --------- Community ---------
@app.route("/post_question", methods=["POST"])
def post_question():
    init_db()
    title = request.form.get("title","").strip(); body = request.form.get("body","").strip()
    if title:
        conn = get_db(); conn.execute("INSERT INTO community (title,body,created_at) VALUES (?,?,?)",(title,body, datetime.utcnow().isoformat())); conn.commit(); conn.close()
    return redirect(url_for('dashboard'))

# --------- Farmer registration & Admin ---------
@app.route("/register_farmer", methods=["GET","POST"])
def register_farmer():
    init_db()
    msg = None
    if request.method=="POST":
        name = request.form.get("name","").strip(); phone = normalize_phone(request.form.get("phone","").strip())
        village = request.form.get("village","").strip(); lang = request.form.get("language","en").strip()
        subscribed = 1 if request.form.get("subscribed") else 0
        conn = get_db(); conn.execute("INSERT INTO farmers (name,phone,village,language,subscribed,created_at) VALUES (?,?,?,?,?,?)",(name,phone,village,lang,subscribed, datetime.utcnow().isoformat())); conn.commit(); conn.close(); msg="Registered"
    return render_template("register_farmer.html", msg=msg, year=datetime.now().year)

@app.route("/admin", methods=["GET"])
def admin_panel():
    init_db()
    conn = get_db(); farmers = [dict(r) for r in conn.execute("SELECT * FROM farmers ORDER BY id DESC").fetchall()]; conn.close()
    flash_msg = request.args.get("flash","")
    return render_template("admin_panel.html", farmers=farmers, flash=flash_msg, year=datetime.now().year)

# send alerts (uses translation + textbelt fallback)
@app.route("/send_alert", methods=["POST"])
def send_alert():
    init_db()
    message = request.form.get("message","").strip()
    target = request.form.get("target","all")
    selected_ids = [int(x) for x in (request.form.get("selected_ids","") or "").split(",") if x.strip().isdigit()]
    force_lang = (request.form.get("force_lang","") or "").strip().lower()
    if not message:
        return redirect(url_for('admin_panel', flash="Message empty"))
    conn = get_db()
    if target=="selected" and selected_ids:
        placeholders = ",".join("?"*len(selected_ids))
        rows = conn.execute(f"SELECT * FROM farmers WHERE id IN ({placeholders}) AND subscribed=1", selected_ids).fetchall()
    else:
        rows = conn.execute("SELECT * FROM farmers WHERE subscribed=1").fetchall()
    sent = 0
    for r in rows:
        lang = force_lang if force_lang in SUPPORTED_LANGS else r['language'] or 'en'
        text = translate_text(message, lang)
        to = normalize_phone(r['phone'])
        # try textbelt first (no account required for quick tests)
        res = send_via_textbelt(to, text)
        if res.get('success'):
            sent += 1
        else:
            # if textbelt failed and Twilio configured and enabled, try Twilio (not importing Twilio client to avoid mandatory dependency)
            if TWILIO_DISABLED!='1' and TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM:
                try:
                    from twilio.rest import Client
                    client = Client(TWILIO_SID, TWILIO_TOKEN)
                    client.messages.create(body=text, from_=TWILIO_FROM, to=to)
                    sent += 1
                except Exception as e:
                    print("Twilio failed:", e)
    conn.close()
    return redirect(url_for('admin_panel', flash=f"Alert queued for {sent} farmer(s)."))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)