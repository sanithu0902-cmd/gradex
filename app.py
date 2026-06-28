import os
import json
import sqlite3
import re
from datetime import datetime
from flask import Flask, request, session, redirect, url_for, render_template_string, send_from_directory
from groq import Groq
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "gradex-secret-key-2024"

# IMPORTANT: Add your Groq API key here
client = Groq(api_key="GROQ_API_KEY")

TEACHER_CODE = "teacher123"
ADMIN_CODE = "admin123"
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "pptx", "docx", "txt"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─── DATABASE ───────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect("gradex.db")
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            bio TEXT DEFAULT '',
            profile_pic TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            lesson TEXT,
            difficulty TEXT,
            questions TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            quiz_id INTEGER,
            score INTEGER,
            total INTEGER,
            pct INTEGER,
            answers TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(quiz_id) REFERENCES quizzes(id)
        );
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            original_name TEXT,
            file_type TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            name TEXT,
            description TEXT,
            icon TEXT,
            unlocked_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id)
        );
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            question TEXT,
            answer TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id)
        );
        CREATE TABLE IF NOT EXISTS quiz_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            quiz_id INTEGER,
            summary TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(quiz_id) REFERENCES quizzes(id)
        );
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            url TEXT NOT NULL,
            subject TEXT DEFAULT 'General',
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS video_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            video_id INTEGER,
            viewed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(video_id) REFERENCES videos(id)
        );
    """)
    db.commit()
    db.close()

init_db()

# ─── HELPERS ────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def check_achievements(student_id, score, total, db):
    pct = round((score / total) * 100) if total else 0
    achievements = []
    count = db.execute("SELECT COUNT(*) as c FROM results WHERE student_id=?", (student_id,)).fetchone()["c"]
    if count == 1:
        achievements.append({"name": "First Quiz", "description": "Completed your first quiz!", "icon": "🎯"})
    if pct == 100:
        achievements.append({"name": "Perfect Score", "description": "Scored 100% on a quiz!", "icon": "⭐"})
    if pct >= 80:
        achievements.append({"name": "Great Performance", "description": "Scored 80% or higher!", "icon": "🔥"})
    if count >= 5:
        achievements.append({"name": "Quiz Master", "description": "Completed 5 quizzes!", "icon": "🏆"})
    if count >= 10:
        achievements.append({"name": "Dedicated Learner", "description": "Completed 10 quizzes!", "icon": "📚"})
    for ach in achievements:
        existing = db.execute("SELECT * FROM achievements WHERE student_id=? AND name=?", (student_id, ach["name"])).fetchone()
        if not existing:
            db.execute("INSERT INTO achievements (student_id, name, description, icon) VALUES (?,?,?,?)",
                      (student_id, ach["name"], ach["description"], ach["icon"]))
            db.commit()

def extract_video_id(url):
    """Extract YouTube video ID from URL"""
    patterns = [
        r'youtube\.com/watch\?v=([^&]+)',
        r'youtu\.be/([^?]+)',
        r'youtube\.com/embed/([^?]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

# ─── STYLES ─────────────────────────────────────────────────────────────────

STYLE = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: #0f0f1a; color: #e0e0e0; min-height: 100vh; }
nav { background: #16162a; border-bottom: 1px solid #2a2a4a; padding: 0 2rem; display: flex; align-items: center; gap: 0.5rem; height: 56px; }
.brand { font-size: 20px; font-weight: 700; color: #7c6fff; margin-right: auto; letter-spacing: 1px; }
nav a { text-decoration: none; color: #aaa; font-size: 14px; padding: 7px 14px; border-radius: 8px; transition: all 0.2s; }
nav a:hover { background: #2a2a4a; color: #fff; }
nav a.active { background: #7c6fff22; color: #7c6fff; font-weight: 600; }
.container { max-width: 800px; margin: 2rem auto; padding: 0 1.5rem; }
h1 { font-size: 26px; font-weight: 700; margin-bottom: 0.4rem; color: #fff; }
h2 { font-size: 18px; font-weight: 600; margin-bottom: 0.75rem; color: #ddd; }
p { color: #aaa; line-height: 1.6; margin-bottom: 0.75rem; font-size: 15px; }
.card { background: #16162a; border: 1px solid #2a2a4a; border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 1rem; }
input[type=password], input[type=text], input[type=email], textarea, select {
  width: 100%; padding: 10px 13px; border: 1px solid #2a2a4a; border-radius: 8px;
  font-size: 14px; margin-bottom: 0.75rem; background: #0f0f1a; color: #e0e0e0;
  font-family: inherit; outline: none; transition: border 0.2s;
}
input:focus, textarea:focus, select:focus { border-color: #7c6fff; }
textarea { height: 140px; resize: vertical; }
button, .btn {
  display: inline-block; padding: 10px 20px; background: #7c6fff; color: #fff;
  border: none; border-radius: 8px; font-size: 14px; cursor: pointer;
  text-decoration: none; font-family: inherit; font-weight: 600; transition: background 0.2s;
}
button:hover, .btn:hover { background: #6355dd; }
.btn-secondary { background: transparent; color: #7c6fff; border: 1px solid #7c6fff; }
.btn-secondary:hover { background: #7c6fff22; }
.btn-danger { background: #e74c3c; }
.btn-danger:hover { background: #c0392b; }
.badge { display: inline-block; font-size: 11px; padding: 3px 9px; border-radius: 20px; margin-bottom: 6px; font-weight: 600; }
.badge-mcq { background: #1a3a6e; color: #7eb3ff; }
.badge-tf  { background: #1a4a2e; color: #7effa0; }
.badge-sa  { background: #4a3a1a; color: #ffca7e; }
.badge-easy { background: #1a4a2e; color: #7effa0; }
.badge-medium { background: #4a3a1a; color: #ffca7e; }
.badge-hard { background: #4a1a1a; color: #ff7e7e; }
.q-num { font-size: 12px; color: #666; margin-bottom: 3px; }
.q-text { font-size: 15px; font-weight: 600; margin-bottom: 8px; color: #fff; }
.option label { display: flex; align-items: center; gap: 8px; font-size: 14px; padding: 5px 0; cursor: pointer; color: #ccc; }
.error { color: #ff7e7e; font-size: 14px; margin-top: 6px; background: #4a1a1a; padding: 8px 12px; border-radius: 8px; }
.success { color: #7effa0; font-size: 14px; margin-top: 6px; background: #1a4a2e; padding: 8px 12px; border-radius: 8px; }
.score-card { background: #1a1a3a; border: 1px solid #7c6fff44; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.25rem; text-align: center; }
.score-num { font-size: 40px; font-weight: 800; color: #7c6fff; }
.score-label { font-size: 15px; color: #aaa; margin-top: 4px; }
.correct { color: #7effa0; font-size: 14px; margin-top: 4px; }
.incorrect { color: #ff7e7e; font-size: 14px; margin-top: 4px; }
.explanation { background: #1a1a2e; border-left: 3px solid #7c6fff; padding: 8px 12px; margin-top: 8px; font-size: 13px; color: #bbb; border-radius: 0 8px 8px 0; }
.btn-row { display: flex; gap: 10px; margin-top: 0.75rem; flex-wrap: wrap; }
.hint { font-size: 13px; color: #666; margin-bottom: 0.5rem; }
.answer-preview { font-size: 13px; color: #666; margin-top: 4px; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
.stat-box { background: #16162a; border: 1px solid #2a2a4a; border-radius: 12px; padding: 1.25rem; text-align: center; }
.stat-num { font-size: 32px; font-weight: 800; color: #7c6fff; }
.stat-label { font-size: 13px; color: #888; margin-top: 4px; }
.file-item { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #2a2a4a; }
.file-item:last-child { border-bottom: none; }
.leaderboard-row { display: flex; align-items: center; gap: 1rem; padding: 10px 0; border-bottom: 1px solid #2a2a4a; }
.leaderboard-row:last-child { border-bottom: none; }
.rank { font-size: 20px; font-weight: 800; color: #7c6fff; width: 36px; }
.rank-1 { color: #ffd700; }
.rank-2 { color: #c0c0c0; }
.rank-3 { color: #cd7f32; }
.loading { display: none; color: #7c6fff; font-size: 14px; margin-top: 10px; }
label.field-label { font-size: 13px; color: #888; display: block; margin-bottom: 5px; }
.tab-bar { display: flex; gap: 6px; margin-bottom: 1.5rem; border-bottom: 1px solid #2a2a4a; padding-bottom: 0; }
.tab-btn { background: none; border: none; color: #888; font-size: 14px; padding: 8px 16px; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; border-radius: 0; font-weight: 500; }
.tab-btn.active { color: #7c6fff; border-bottom-color: #7c6fff; background: none; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.chat-container { max-height: 500px; overflow-y: auto; margin-bottom: 1rem; }
.chat-container::-webkit-scrollbar { width: 6px; }
.chat-container::-webkit-scrollbar-track { background: #0f0f1a; }
.chat-container::-webkit-scrollbar-thumb { background: #7c6fff; border-radius: 10px; }
.video-container { position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; border-radius: 12px; margin-bottom: 1rem; }
.video-container iframe { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }
</style>
"""

# ─── BASE TEMPLATE ───────────────────────────────────────────────────────────

def base(content, active=""):
    user = session.get("student_name", "")
    nav_right = ""
    if user:
        nav_right = f'<span style="color:#7c6fff;font-size:14px">👤 {user}</span> <a href="/logout" class="btn-secondary btn" style="padding:5px 12px;font-size:13px">Logout</a>'
    elif session.get("teacher"):
        nav_right = '<a href="/logout" class="btn-secondary btn" style="padding:5px 12px;font-size:13px">Logout</a>'
    elif session.get("admin"):
        nav_right = '<a href="/logout" class="btn-secondary btn" style="padding:5px 12px;font-size:13px">Logout</a>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GradeX</title>{STYLE}</head><body>
<nav>
  <span class="brand">⚡ GradeX</span>
  <a href="/" class="{'active' if active=='home' else ''}">Home</a>
  <a href="/student" class="{'active' if active=='student' else ''}">Student</a>
  <a href="/teacher" class="{'active' if active=='teacher' else ''}">Teacher</a>
  <a href="/admin" class="{'active' if active=='admin' else ''}">Admin</a>
  {nav_right}
</nav>
<div class="container">{content}</div>
</body></html>"""

# ─── HOME ────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return base("""
<div style="text-align:center;padding:2.5rem 0 2rem">
  <div style="font-size:52px;margin-bottom:0.5rem">⚡</div>
  <h1 style="font-size:36px;margin-bottom:0.75rem">Welcome to GradeX</h1>
  <p style="font-size:17px;color:#bbb;max-width:480px;margin:0 auto 1.5rem">
    The smart way to learn. Take AI-powered quizzes, access your study materials, and track your progress — all in one place.
  </p>
  <a class="btn" href="/student" style="font-size:16px;padding:13px 32px">Get Started as Student 🎓</a>
</div>
<div style="margin:2rem 0">
  <h2 style="text-align:center;margin-bottom:1.25rem;color:#aaa;font-size:14px;letter-spacing:2px;text-transform:uppercase">What you can do</h2>
  <div class="stat-grid">
    <div class="stat-box"><div style="font-size:28px;margin-bottom:8px">📝</div><div style="font-weight:600;color:#fff;margin-bottom:4px">Take Quizzes</div><div style="font-size:13px;color:#888">AI-generated quizzes on any lesson, at any difficulty level</div></div>
    <div class="stat-box"><div style="font-size:28px;margin-bottom:8px">💡</div><div style="font-weight:600;color:#fff;margin-bottom:4px">Learn from Mistakes</div><div style="font-size:13px;color:#888">Get AI explanations for every wrong answer instantly</div></div>
    <div class="stat-box"><div style="font-size:28px;margin-bottom:8px">📁</div><div style="font-weight:600;color:#fff;margin-bottom:4px">Study Materials</div><div style="font-size:13px;color:#888">Access PDFs, slides and images shared by your teacher</div></div>
    <div class="stat-box"><div style="font-size:28px;margin-bottom:8px">🏆</div><div style="font-weight:600;color:#fff;margin-bottom:4px">Leaderboard</div><div style="font-size:13px;color:#888">See how you rank against your classmates</div></div>
    <div class="stat-box"><div style="font-size:28px;margin-bottom:8px">📊</div><div style="font-weight:600;color:#fff;margin-bottom:4px">Track Progress</div><div style="font-size:13px;color:#888">See your quiz history and scores over time</div></div>
    <div class="stat-box"><div style="font-size:28px;margin-bottom:8px">💬</div><div style="font-weight:600;color:#fff;margin-bottom:4px">AI Chat Tutor</div><div style="font-size:13px;color:#888">Ask questions and get instant AI help</div></div>
    <div class="stat-box"><div style="font-size:28px;margin-bottom:8px">🎥</div><div style="font-weight:600;color:#fff;margin-bottom:4px">Video Lessons</div><div style="font-size:13px;color:#888">Watch educational videos shared by your teacher</div></div>
  </div>
</div>
<div class="card" style="margin-bottom:1rem">
  <h2 style="margin-bottom:1rem">How it works</h2>
  <div style="display:flex;flex-direction:column;gap:12px">
    <div style="display:flex;align-items:center;gap:12px"><div style="background:#7c6fff22;color:#7c6fff;font-weight:800;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0">1</div><span style="color:#ccc">Create your student account for free</span></div>
    <div style="display:flex;align-items:center;gap:12px"><div style="background:#7c6fff22;color:#7c6fff;font-weight:800;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0">2</div><span style="color:#ccc">Your teacher generates quizzes from lesson content using AI</span></div>
    <div style="display:flex;align-items:center;gap:12px"><div style="background:#7c6fff22;color:#7c6fff;font-weight:800;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0">3</div><span style="color:#ccc">Take the quiz, get instant results and AI explanations</span></div>
    <div style="display:flex;align-items:center;gap:12px"><div style="background:#7c6fff22;color:#7c6fff;font-weight:800;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0">4</div><span style="color:#ccc">Watch video lessons and access study materials</span></div>
  </div>
</div>
<div style="text-align:center;padding:1.5rem 0">
  <a class="btn" href="/student" style="font-size:15px;padding:12px 28px">Login or Register as Student 🎓</a>
</div>
<div style="border-top:1px solid #2a2a4a;margin-top:1rem;padding-top:1.5rem">
  <p style="text-align:center;font-size:13px;color:#555;margin-bottom:1rem">Are you a teacher or administrator?</p>
  <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
    <a href="/teacher" class="btn btn-secondary" style="font-size:13px;padding:8px 18px">👩‍🏫 I'm a Teacher</a>
    <a href="/admin" class="btn btn-secondary" style="font-size:13px;padding:8px 18px">🛠️ Admin Panel</a>
  </div>
</div>
""", "home")

# ─── STUDENT AUTH ────────────────────────────────────────────────────────────

@app.route("/student", methods=["GET", "POST"])
def student():
    if session.get("student_id"):
        return redirect(url_for("student_dashboard"))
    error = ""
    if request.method == "POST":
        action = request.form.get("action")
        db = get_db()
        if action == "register":
            name = request.form.get("name", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            if not name or not username or not password:
                error = "All fields are required."
            else:
                try:
                    db.execute("INSERT INTO students (name, username, password) VALUES (?,?,?)",
                               (name, username, generate_password_hash(password)))
                    db.commit()
                    row = db.execute("SELECT * FROM students WHERE username=?", (username,)).fetchone()
                    session["student_id"] = row["id"]
                    session["student_name"] = row["name"]
                    db.close()
                    return redirect(url_for("student_dashboard"))
                except:
                    error = "Username already taken. Try another."
        elif action == "login":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            row = db.execute("SELECT * FROM students WHERE username=?", (username,)).fetchone()
            if row and check_password_hash(row["password"], password):
                session["student_id"] = row["id"]
                session["student_name"] = row["name"]
                db.close()
                return redirect(url_for("student_dashboard"))
            else:
                error = "Wrong username or password."
        db.close()

    return base(f"""
<h1>🎓 Student Portal</h1>
<div class="tab-bar">
  <button class="tab-btn active" onclick="showTab('login')">Login</button>
  <button class="tab-btn" onclick="showTab('register')">Register</button>
</div>
<div id="login" class="tab-content active">
  <div class="card">
    <h2>Login</h2>
    <form method="POST">
      <input type="hidden" name="action" value="login">
      <label class="field-label">Username</label>
      <input type="text" name="username" placeholder="Your username" required>
      <label class="field-label">Password</label>
      <input type="password" name="password" placeholder="Your password" required>
      <button type="submit">Login</button>
      {'<div class="error">'+error+'</div>' if error else ''}
    </form>
  </div>
</div>
<div id="register" class="tab-content">
  <div class="card">
    <h2>Create Account</h2>
    <form method="POST">
      <input type="hidden" name="action" value="register">
      <label class="field-label">Full Name</label>
      <input type="text" name="name" placeholder="Your full name" required>
      <label class="field-label">Username</label>
      <input type="text" name="username" placeholder="Choose a username" required>
      <label class="field-label">Password</label>
      <input type="password" name="password" placeholder="Choose a password" required>
      <button type="submit">Register</button>
      {'<div class="error">'+error+'</div>' if error else ''}
    </form>
  </div>
</div>
<script>
function showTab(id) {{
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  event.target.classList.add('active');
}}
</script>
""", "student")

# ─── STUDENT DASHBOARD ───────────────────────────────────────────────────────

@app.route("/student/dashboard")
def student_dashboard():
    if not session.get("student_id"):
        return redirect(url_for("student"))
    db = get_db()
    quizzes = db.execute("SELECT * FROM quizzes ORDER BY created_at DESC").fetchall()
    files = db.execute("SELECT * FROM files ORDER BY uploaded_at DESC").fetchall()
    results = db.execute("SELECT r.*, q.title FROM results r JOIN quizzes q ON r.quiz_id=q.id WHERE r.student_id=? ORDER BY r.created_at DESC LIMIT 5",
                         (session["student_id"],)).fetchall()
    student = db.execute("SELECT xp, level FROM students WHERE id=?", (session["student_id"],)).fetchone()
    
    # Get videos
    videos = db.execute("SELECT * FROM videos ORDER BY uploaded_at DESC").fetchall()
    
    # Get video subjects for filter
    video_subjects = db.execute("SELECT DISTINCT subject FROM videos").fetchall()
    
    db.close()

    quiz_html = ""
    for q in quizzes:
        diff = q["difficulty"]
        quiz_html += f"""<div class="card">
          <span class="badge badge-{diff.lower()}">{diff}</span>
          <div class="q-text">{q['title'] or 'Quiz'}</div>
          <div style="font-size:13px;color:#888;margin-bottom:8px">{q['created_at'][:10]}</div>
          <a class="btn" href="/student/quiz/{q['id']}">Take Quiz</a>
        </div>"""

    if not quiz_html:
        quiz_html = "<p>No quizzes available yet. Check back soon!</p>"

    file_html = ""
    for f in files:
        icon = "📄" if f["file_type"] == "pdf" else "🖼️" if f["file_type"] in ["png","jpg","jpeg","gif"] else "📁"
        file_html += f"""<div class="file-item">
          <span>{icon} {f['original_name']}</span>
          <div style="display:flex;gap:8px;">
            <a class="btn btn-secondary" href="/files/preview/{f['filename']}" target="_blank" style="padding:5px 12px;font-size:13px">👁️ Preview</a>
            <a class="btn btn-secondary" href="/files/{f['filename']}" download style="padding:5px 12px;font-size:13px">⬇️ Download</a>
          </div>
        </div>"""
    if not file_html:
        file_html = "<p>No files uploaded yet.</p>"

    results_html = ""
    for r in results:
        color = "#7effa0" if r["pct"] >= 60 else "#ff7e7e"
        results_html += f"""<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #2a2a4a">
          <span style="color:#ccc">{r['title'] or 'Quiz'}</span>
          <span style="color:{color};font-weight:700">{r['pct']}%</span>
        </div>"""
    if not results_html:
        results_html = "<p>No results yet. Take a quiz!</p>"

    # Videos HTML
    video_html = ""
    for v in videos:
        # Track view
        db = get_db()
        db.execute("INSERT OR IGNORE INTO video_views (student_id, video_id) VALUES (?,?)",
                   (session["student_id"], v["id"]))
        db.commit()
        db.close()
        
        # Get view count
        db2 = get_db()
        views = db2.execute("SELECT COUNT(*) as count FROM video_views WHERE video_id=?", (v["id"],)).fetchone()["count"]
        db2.close()
        
        video_html += f"""<div class="card" style="border-color:#7c6fff44;">
            <div style="display:flex;justify-content:space-between;align-items:start;">
                <div>
                    <span style="font-size:20px;">🎬</span>
                    <strong style="color:#fff;font-size:16px;">{v['title']}</strong>
                    <span class="badge badge-medium" style="margin-left:8px;">📌 {v['subject']}</span>
                    <div style="font-size:13px;color:#888;margin-top:4px;">{v['description'] or 'No description'}</div>
                    <div style="font-size:12px;color:#666;margin-top:4px;">👁️ {views} views</div>
                </div>
            </div>
            <div style="margin-top:10px;">
                <a href="/watch/{v['id']}" class="btn" style="padding:5px 16px;font-size:13px;">▶️ Watch Now</a>
                <a href="{v['url']}" target="_blank" class="btn btn-secondary" style="padding:5px 12px;font-size:13px;">🔗 Open Link</a>
            </div>
        </div>"""
    
    if not video_html:
        video_html = "<p style='color:#888;'>No videos shared yet. Check back later!</p>"

    # Subject filter buttons
    subject_filter_html = ""
    for s in video_subjects:
        subject_filter_html += f'<button class="btn btn-secondary" onclick="filterVideos(\'{s["subject"]}\')" style="font-size:12px;padding:5px 12px;">📌 {s["subject"]}</button>'

    return base(f"""
<h1>👋 Hello, {session['student_name']}!</h1>
<div style="display:flex;gap:16px;margin-bottom:1rem;flex-wrap:wrap;">
  <span style="background:#1a1a3a;border:1px solid #7c6fff44;border-radius:8px;padding:6px 16px;">🏆 Level {student['level']}</span>
  <span style="background:#1a1a3a;border:1px solid #7c6fff44;border-radius:8px;padding:6px 16px;">⭐ {student['xp']} XP</span>
  <a class="btn btn-secondary" href="/portfolio/{session['student_id']}" style="margin-left:auto;">👤 My Profile</a>
</div>
<p>Welcome to your GradeX dashboard.</p>
<div class="tab-bar" style="margin-top:1rem">
  <button class="tab-btn active" onclick="showTab('quizzes')">📝 Quizzes</button>
  <button class="tab-btn" onclick="showTab('files')">📁 Files</button>
  <button class="tab-btn" onclick="showTab('myresults')">📊 My Results</button>
  <button class="tab-btn" onclick="showTab('leaderboard-tab')">🏆 Leaderboard</button>
  <button class="tab-btn" onclick="showTab('chat-tab')">💬 Chat</button>
  <button class="tab-btn" onclick="showTab('videos-tab')">🎥 Videos</button>
</div>
<div id="quizzes" class="tab-content active">{quiz_html}</div>
<div id="files" class="tab-content"><div class="card">{file_html}</div></div>
<div id="myresults" class="tab-content"><div class="card">{results_html}</div></div>
<div id="leaderboard-tab" class="tab-content">
  <div class="card" id="lb-content"><p>Loading leaderboard...</p></div>
</div>
<div id="chat-tab" class="tab-content">
  <div class="card" style="text-align:center;">
    <p style="margin-bottom:1rem;">💬 Need help? Ask the AI Tutor anything!</p>
    <a class="btn" href="/chat">Open AI Chat Tutor</a>
  </div>
</div>
<div id="videos-tab" class="tab-content">
    <div style="margin-bottom:1rem;display:flex;gap:8px;flex-wrap:wrap;">
        <button class="btn btn-secondary" onclick="filterVideos('all')" style="font-size:12px;padding:5px 12px;">📌 All</button>
        {subject_filter_html}
    </div>
    {video_html}
</div>
<script>
function showTab(id) {{
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  event.target.classList.add('active');
  if(id==='leaderboard-tab') loadLeaderboard();
}}
function loadLeaderboard() {{
  fetch('/api/leaderboard').then(r=>r.json()).then(data=>{{
    let html = '';
    data.forEach((s,i)=>{{
      const medals = ['🥇','🥈','🥉'];
      const medal = i<3 ? medals[i] : (i+1)+'.';
      html += `<div class="leaderboard-row"><span class="rank rank-${{i+1}}">${{medal}}</span><span style="flex:1;color:#fff">${{s.name}}</span><span style="color:#7c6fff;font-weight:700">${{s.avg}}%</span><span style="color:#888;font-size:13px;margin-left:8px">${{s.quizzes}} quizzes</span></div>`;
    }});
    document.getElementById('lb-content').innerHTML = html || '<p>No scores yet!</p>';
  }});
}}
function filterVideos(subject) {{
  const cards = document.querySelectorAll('#videos-tab .card');
  cards.forEach(card => {{
    if (subject === 'all') {{
      card.style.display = 'block';
    }} else {{
      const badge = card.querySelector('.badge');
      if (badge && badge.textContent.includes(subject)) {{
        card.style.display = 'block';
      }} else {{
        card.style.display = 'none';
      }}
    }}
  }});
}}
</script>
""", "student")

# ─── STUDENT QUIZ ────────────────────────────────────────────────────────────

@app.route("/student/quiz/<int:quiz_id>")
def take_quiz(quiz_id):
    if not session.get("student_id"):
        return redirect(url_for("student"))
    db = get_db()
    quiz = db.execute("SELECT * FROM quizzes WHERE id=?", (quiz_id,)).fetchone()
    db.close()
    if not quiz:
        return redirect(url_for("student_dashboard"))

    questions = json.loads(quiz["questions"])
    q_html = ""
    for i, q in enumerate(questions):
        badge = {"mcq": '<span class="badge badge-mcq">MCQ</span>', "tf": '<span class="badge badge-tf">True/False</span>', "sa": '<span class="badge badge-sa">Short Answer</span>'}.get(q["type"], "")
        q_html += f'<div class="card">{badge}<div class="q-num">Question {i+1}</div><div class="q-text">{q["question"]}</div>'
        if q["type"] == "mcq":
            for opt in q["options"]:
                q_html += f'<div class="option"><label><input type="radio" name="q{i}" value="{opt[0]}"> {opt}</label></div>'
        elif q["type"] == "tf":
            q_html += '<div class="option"><label><input type="radio" name="q'+str(i)+'" value="True"> True</label></div>'
            q_html += '<div class="option"><label><input type="radio" name="q'+str(i)+'" value="False"> False</label></div>'
        else:
            q_html += f'<input type="text" name="q{i}" placeholder="Your answer...">'
        q_html += "</div>"

    diff = quiz["difficulty"]
    return base(f"""
<h1>📝 {quiz['title'] or 'Quiz'}</h1>
<span class="badge badge-{diff.lower()}">{diff}</span>
<p style="margin-top:0.5rem">{len(questions)} questions</p>
<form method="POST" action="/student/quiz/{quiz_id}/submit">
  {q_html}
  <button type="submit">Submit Answers</button>
</form>
""", "student")

# ─── STUDENT QUIZ SUBMIT ────────────────────────────────────────────────────

@app.route("/student/quiz/<int:quiz_id>/submit", methods=["POST"])
def submit_quiz(quiz_id):
    if not session.get("student_id"):
        return redirect(url_for("student"))
    db = get_db()
    quiz = db.execute("SELECT * FROM quizzes WHERE id=?", (quiz_id,)).fetchone()
    questions = json.loads(quiz["questions"])

    results = []
    score = 0
    wrong_topics = []
    
    for i, q in enumerate(questions):
        student_ans = request.form.get(f"q{i}", "").strip() or "(no answer)"
        correct = (q["answer"].lower().split()[0] in student_ans.lower()) if q["type"] == "sa" else (student_ans == q["answer"])
        if correct:
            score += 1
        else:
            wrong_topics.append({
                "question": q["question"],
                "correct": q["answer"],
                "your_answer": student_ans            })
        results.append({"question": q["question"], "student": student_ans, "answer": q["answer"], "correct": correct, "explanation": q.get("explanation", "")})

    total = len(questions)
    pct = round((score / total) * 100) if total else 0
    
    # Save result
    db.execute("INSERT INTO results (student_id, quiz_id, score, total, pct, answers) VALUES (?,?,?,?,?,?)",
               (session["student_id"], quiz_id, score, total, pct, json.dumps(results)))
    db.commit()
    
    # Generate AI Summary
    summary_text = ""
    if pct < 100 and wrong_topics:
        try:
            summary_response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                max_tokens=300,
                messages=[{"role": "user", "content": f"""
You are a helpful tutor. Based on the student's quiz performance, generate a brief personalized learning summary (2-3 sentences).

Quiz: {quiz['title']}
Score: {pct}%

Questions the student got wrong:
{json.dumps(wrong_topics, indent=2)}

Provide specific guidance on what to review and a quick tip for each wrong question.
Format: Start with "📚 Here's what to review:" then give specific advice.
"""}]
            )
            summary_text = summary_response.choices[0].message.content.strip()
            db.execute("INSERT INTO quiz_summaries (student_id, quiz_id, summary) VALUES (?,?,?)",
                       (session["student_id"], quiz_id, summary_text))
            db.commit()
        except Exception as e:
            summary_text = "💡 Review the questions you got wrong and try again!"
    
    # Update XP
    xp_earned = score * 10
    db.execute("UPDATE students SET xp = xp + ? WHERE id = ?", (xp_earned, session["student_id"]))
    student = db.execute("SELECT xp FROM students WHERE id=?", (session["student_id"],)).fetchone()
    new_level = (student["xp"] // 30) + 1
    db.execute("UPDATE students SET level = ? WHERE id = ?", (new_level, session["student_id"]))
    
    check_achievements(session["student_id"], score, total, db)
    db.commit()
    db.close()

    emoji = "🎉" if pct >= 80 else "👍" if pct >= 60 else "📚"
    msg = "Great work!" if pct >= 80 else "Good effort!" if pct >= 60 else "Keep practising!"
    
    results_html = ""
    for i, r in enumerate(results):
        results_html += f"""<div class="card">
          <div class="q-num">Question {i+1}</div>
          <div class="q-text">{r['question']}</div>
          <div style="font-size:14px;color:#ccc">Your answer: <strong>{r['student']}</strong></div>
          {'<div class="correct">✓ Correct</div>' if r['correct'] else f'<div class="incorrect">✗ Incorrect — Answer: {r["answer"]}</div>'}
          {f'<div class="explanation">💡 {r["explanation"]}</div>' if r.get("explanation") and not r["correct"] else ''}
        </div>"""

    return base(f"""
<h1>{emoji} Results</h1>
<div class="score-card">
  <div class="score-num">{score}/{total}</div>
  <div class="score-label">{pct}% — {msg}</div>
  <div style="margin-top:8px;font-size:14px;color:#888">+{xp_earned} XP earned! 🏆</div>
</div>

<div class="card" style="border-color:#7c6fff;">
  <h2>📚 AI Learning Summary</h2>
  <div style="background:#0f0f1a;padding:12px;border-radius:8px;font-size:14px;line-height:1.6;color:#ddd;">
    {summary_text or "Great job! Keep up the good work!"}
  </div>
</div>

{results_html}

<div class="btn-row">
  <a class="btn btn-secondary" href="/student/dashboard">Back to Dashboard</a>
  <a class="btn" href="/chat">💬 Ask AI Tutor</a>
</div>
""", "student")

# ─── TEACHER ─────────────────────────────────────────────────────────────────

@app.route("/teacher", methods=["GET", "POST"])
def teacher():
    if session.get("teacher"):
        return redirect(url_for("teacher_dashboard"))
    error = ""
    if request.method == "POST":
        if request.form.get("code") == TEACHER_CODE:
            session["teacher"] = True
            return redirect(url_for("teacher_dashboard"))
        error = "Wrong teacher code."
    return base(f"""
<h1>👩‍🏫 Teacher Login</h1>
<div class="card">
  <form method="POST">
    <label class="field-label">Teacher Code</label>
    <input type="password" name="code" placeholder="Enter teacher code" autofocus>
    <p class="hint">Default: teacher123</p>
    <button type="submit">Login</button>
    {'<div class="error">'+error+'</div>' if error else ''}
  </form>
</div>
""", "teacher")

@app.route("/teacher/dashboard")
def teacher_dashboard():
    if not session.get("teacher"):
        return redirect(url_for("teacher"))
    db = get_db()
    quizzes = db.execute("SELECT * FROM quizzes ORDER BY created_at DESC").fetchall()
    files = db.execute("SELECT * FROM files ORDER BY uploaded_at DESC").fetchall()
    videos = db.execute("SELECT * FROM videos ORDER BY uploaded_at DESC").fetchall()
    db.close()

    quiz_html = ""
    for q in quizzes:
        diff = q["difficulty"]
        quiz_html += f"""<div class="card">
          <span class="badge badge-{diff.lower()}">{diff}</span>
          <div class="q-text">{q['title'] or 'Quiz'}</div>
          <div style="font-size:13px;color:#888">{q['created_at'][:10]}</div>
        </div>"""
    if not quiz_html:
        quiz_html = "<p>No quizzes yet. Create one!</p>"

    file_html = ""
    for f in files:
        icon = "📄" if f["file_type"] == "pdf" else "🖼️" if f["file_type"] in ["png","jpg","jpeg","gif"] else "📁"
        file_html += f"""<div class="file-item">
          <span>{icon} {f['original_name']}</span>
          <a href="/teacher/delete-file/{f['id']}" class="btn btn-danger" style="padding:4px 10px;font-size:12px" onclick="return confirm('Delete this file?')">Delete</a>
        </div>"""
    if not file_html:
        file_html = "<p>No files uploaded yet.</p>"

    video_html = ""
    for v in videos:
        video_html += f"""<div class="card">
            <div style="display:flex;justify-content:space-between;align-items:start;">
                <div>
                    <span style="font-size:20px;">🎬</span>
                    <strong style="color:#fff;font-size:16px;">{v['title']}</strong>
                    <div style="font-size:13px;color:#888;margin-top:4px;">📌 {v['subject']}</div>
                    <div style="font-size:12px;color:#666;margin-top:4px;">{v['description'][:100] if v['description'] else 'No description'}{'...' if len(v['description'] or '') > 100 else ''}</div>
                </div>
                <a href="/teacher/delete-video/{v['id']}" class="btn btn-danger" style="padding:4px 10px;font-size:12px" onclick="return confirm('Delete this video?')">🗑️</a>
            </div>
            <div style="margin-top:8px;">
                <a href="{v['url']}" target="_blank" class="btn btn-secondary" style="padding:5px 12px;font-size:13px;">▶️ Watch</a>
            </div>
        </div>"""
    
    if not video_html:
        video_html = "<p style='color:#888;'>No videos uploaded yet. Share your first video!</p>"

    return base(f"""
<h1>👩‍🏫 Teacher Dashboard</h1>
<div class="tab-bar">
  <button class="tab-btn active" onclick="showTab('generate')">✨ Generate Quiz</button>
  <button class="tab-btn" onclick="showTab('myquizzes')">📝 My Quizzes</button>
  <button class="tab-btn" onclick="showTab('upload')">📁 Upload Files</button>
  <button class="tab-btn" onclick="showTab('myfiles')">🗂️ My Files</button>
  <button class="tab-btn" onclick="showTab('videos')">🎥 Videos</button>
</div>

<div id="generate" class="tab-content active">
  <div class="card">
    <h2>Generate AI Quiz</h2>
    <form method="POST" action="/teacher/generate" id="genForm">
      <label class="field-label">Quiz Title</label>
      <input type="text" name="title" placeholder="e.g. Chapter 3 Quiz">
      <label class="field-label">Lesson Content</label>
      <textarea name="lesson" placeholder="Paste your lesson content here..." required></textarea>
      <label class="field-label">Difficulty</label>
      <select name="difficulty">
        <option value="Easy">Easy</option>
        <option value="Medium" selected>Medium</option>
        <option value="Hard">Hard</option>
      </select>
      <button type="submit" onclick="document.getElementById('genLoading').style.display='block'">Generate Quiz ✨</button>
      <div id="genLoading" class="loading" style="display:none;margin-top:10px">⏳ Generating questions, please wait...</div>
    </form>
  </div>
</div>

<div id="myquizzes" class="tab-content">{quiz_html}</div>

<div id="upload" class="tab-content">
  <div class="card">
    <h2>Upload File</h2>
    <form method="POST" action="/teacher/upload" enctype="multipart/form-data">
      <label class="field-label">Choose File (PDF, images, PPTX, DOCX, TXT)</label>
      <input type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,.gif,.pptx,.docx,.txt" style="margin-bottom:0.75rem;color:#ccc">
      <button type="submit">Upload File 📁</button>
    </form>
  </div>
</div>

<div id="myfiles" class="tab-content"><div class="card">{file_html}</div></div>

<div id="videos" class="tab-content">
    <div class="card">
        <h2>📤 Share a Video</h2>
        <form method="POST" action="/teacher/add-video">
            <label class="field-label">Video Title</label>
            <input type="text" name="title" placeholder="e.g., Algebra Basics Explained" required>
            
            <label class="field-label">Description</label>
            <textarea name="description" placeholder="What's this video about?" rows="2"></textarea>
            
            <label class="field-label">Video URL</label>
            <input type="url" name="url" placeholder="YouTube URL or Google Drive link" required>
            <p class="hint">🔗 Supports YouTube, Google Drive, Vimeo, and any direct video link</p>
            
            <label class="field-label">Subject</label>
            <select name="subject">
                <option value="General">📌 General</option>
                <option value="Math">📐 Math</option>
                <option value="Science">🔬 Science</option>
                <option value="History">📜 History</option>
                <option value="English">📖 English</option>
                <option value="Programming">💻 Programming</option>
                <option value="Art">🎨 Art</option>
            </select>
            
            <button type="submit">📤 Share Video</button>
        </form>
    </div>
    
    <h2 style="margin-top:1rem;">📚 Shared Videos</h2>
    {video_html}
</div>

<script>
function showTab(id) {{
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  event.target.classList.add('active');
}}
</script>
""", "teacher")

@app.route("/teacher/generate", methods=["POST"])
def teacher_generate():
    if not session.get("teacher"):
        return redirect(url_for("teacher"))

    lesson = request.form.get("lesson", "").strip()
    title = request.form.get("title", "").strip() or "Quiz"
    difficulty = request.form.get("difficulty", "Medium")

    diff_instructions = {
        "Easy": "Use simple language, straightforward questions suitable for beginners.",
        "Medium": "Use moderate complexity, mix of recall and understanding questions.",
        "Hard": "Use complex questions requiring analysis, application and critical thinking."
    }.get(difficulty, "")

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=1500,
            messages=[{"role": "user", "content": f"""You are a quiz generator. Create exactly 5 quiz questions.
{diff_instructions}

Lesson:
{lesson}

Return ONLY a valid JSON array. No markdown, no explanation, no backticks.
Format:
[
  {{"type":"mcq","question":"...","options":["A. ...","B. ...","C. ...","D. ..."],"answer":"A","explanation":"Why A is correct..."}},
  {{"type":"mcq","question":"...","options":["A. ...","B. ...","C. ...","D. ..."],"answer":"B","explanation":"Why B is correct..."}},
  {{"type":"mcq","question":"...","options":["A. ...","B. ...","C. ...","D. ..."],"answer":"C","explanation":"Why C is correct..."}},
  {{"type":"tf","question":"...","answer":"True","explanation":"Why this is true..."}},
  {{"type":"sa","question":"...","answer":"...","explanation":"Explanation here..."}}
]"""}]
        )

        text = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        questions = json.loads(text)

        db = get_db()
        db.execute("INSERT INTO quizzes (title, lesson, difficulty, questions) VALUES (?,?,?,?)",
                   (title, lesson, difficulty, json.dumps(questions)))
        db.commit()
        db.close()

        q_preview = ""
        for i, q in enumerate(questions):
            badge = {"mcq": '<span class="badge badge-mcq">MCQ</span>', "tf": '<span class="badge badge-tf">True/False</span>', "sa": '<span class="badge badge-sa">Short Answer</span>'}.get(q["type"], "")
            q_preview += f'<div class="card">{badge}<div class="q-num">Q{i+1}</div><div class="q-text">{q["question"]}</div><div class="answer-preview">Answer: {q["answer"]}</div></div>'

        return base(f"""
<h1>✅ Quiz Created!</h1>
<p>"{title}" has been saved and is now available to students.</p>
<span class="badge badge-{difficulty.lower()}">{difficulty}</span>
<div style="margin-top:1rem">{q_preview}</div>
<div class="btn-row">
  <a class="btn btn-secondary" href="/teacher/dashboard">Back to Dashboard</a>
</div>
""", "teacher")

    except Exception as e:
        return base(f"""
<h1>❌ Generation Failed</h1>
<div class="error">{str(e)}</div>
<div class="btn-row" style="margin-top:1rem"><a class="btn btn-secondary" href="/teacher/dashboard">Go Back</a></div>
""", "teacher")

@app.route("/teacher/upload", methods=["POST"])
def teacher_upload():
    if not session.get("teacher"):
        return redirect(url_for("teacher"))
    if "file" not in request.files:
        return redirect(url_for("teacher_dashboard"))
    file = request.files["file"]
    if file and allowed_file(file.filename):
        original = file.filename
        ext = original.rsplit(".", 1)[1].lower()
        safe = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{original}")
        file.save(os.path.join(UPLOAD_FOLDER, safe))
        db = get_db()
        db.execute("INSERT INTO files (filename, original_name, file_type) VALUES (?,?,?)", (safe, original, ext))
        db.commit()
        db.close()
    return redirect(url_for("teacher_dashboard"))

@app.route("/teacher/delete-file/<int:file_id>")
def delete_file(file_id):
    if not session.get("teacher"):
        return redirect(url_for("teacher"))
    db = get_db()
    f = db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if f:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, f["filename"]))
        except:
            pass
        db.execute("DELETE FROM files WHERE id=?", (file_id,))
        db.commit()
    db.close()
    return redirect(url_for("teacher_dashboard"))

# ─── TEACHER VIDEO ROUTES ──────────────────────────────────────────────────

@app.route("/teacher/add-video", methods=["POST"])
def add_video():
    if not session.get("teacher"):
        return redirect(url_for("teacher"))
    
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    url = request.form.get("url", "").strip()
    subject = request.form.get("subject", "General")
    
    if not title or not url:
        return base("""
        <h1>❌ Error</h1>
        <div class="error">Title and URL are required!</div>
        <div class="btn-row"><a class="btn btn-secondary" href="/teacher/dashboard">Go Back</a></div>
        """, "teacher")
    
    db = get_db()
    db.execute("INSERT INTO videos (title, description, url, subject) VALUES (?,?,?,?)",
               (title, description, url, subject))
    db.commit()
    db.close()
    
    return redirect(url_for("teacher_dashboard"))

@app.route("/teacher/delete-video/<int:video_id>")
def delete_video(video_id):
    if not session.get("teacher"):
        return redirect(url_for("teacher"))
    
    db = get_db()
    db.execute("DELETE FROM videos WHERE id=?", (video_id,))
    db.commit()
    db.close()
    
    return redirect(url_for("teacher_dashboard"))

# ─── WATCH VIDEO ────────────────────────────────────────────────────────────

@app.route("/watch/<int:video_id>")
def watch_video(video_id):
    if not session.get("student_id"):
        return redirect(url_for("student"))
    
    db = get_db()
    video = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
    
    if not video:
        return redirect(url_for("student_dashboard"))
    
    # Track view
    db.execute("INSERT OR IGNORE INTO video_views (student_id, video_id) VALUES (?,?)",
               (session["student_id"], video_id))
    db.commit()
    db.close()
    
    # Get embed URL
    video_id_from_url = extract_video_id(video["url"])
    embed_url = f"https://www.youtube.com/embed/{video_id_from_url}" if video_id_from_url else video["url"]
    
    return base(f"""
<div style="margin-bottom:1rem;">
    <a href="/student/dashboard" class="btn btn-secondary" style="padding:5px 12px;font-size:13px;">← Back to Dashboard</a>
</div>

<h1>🎬 {video['title']}</h1>
<div style="display:flex;gap:8px;margin-bottom:1rem;">
    <span class="badge badge-medium">📌 {video['subject']}</span>
    <span style="color:#888;font-size:13px;">📅 {video['uploaded_at'][:10]}</span>
</div>

<div class="card" style="padding:0;overflow:hidden;">
    <div class="video-container">
        <iframe src="{embed_url}" frameborder="0" allowfullscreen></iframe>
    </div>
</div>

<div class="card">
    <h2>📝 Description</h2>
    <p style="color:#ccc;line-height:1.6;">{video['description'] or 'No description provided.'}</p>
</div>

<div class="btn-row">
    <a href="{video['url']}" target="_blank" class="btn btn-secondary">🔗 Open in New Tab</a>
    <a href="/student/dashboard" class="btn btn-secondary">📚 Back to Dashboard</a>
</div>
""", "student")

# ─── ADMIN ───────────────────────────────────────────────────────────────────

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if session.get("admin"):
        return redirect(url_for("admin_dashboard"))
    error = ""
    if request.method == "POST":
        if request.form.get("code") == ADMIN_CODE:
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        error = "Wrong admin code."
    return base(f"""
<h1>🛠️ Admin Login</h1>
<div class="card">
  <form method="POST">
    <label class="field-label">Admin Code</label>
    <input type="password" name="code" placeholder="Enter admin code" autofocus>
    <p class="hint">Default: admin123</p>
    <button type="submit">Login</button>
    {'<div class="error">'+error+'</div>' if error else ''}
  </form>
</div>
""", "admin")

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    db = get_db()
    total_students = db.execute("SELECT COUNT(*) as c FROM students").fetchone()["c"]
    total_quizzes = db.execute("SELECT COUNT(*) as c FROM quizzes").fetchone()["c"]
    total_results = db.execute("SELECT COUNT(*) as c FROM results").fetchone()["c"]
    total_videos = db.execute("SELECT COUNT(*) as c FROM videos").fetchone()["c"]
    avg_score = db.execute("SELECT AVG(pct) as a FROM results").fetchone()["a"] or 0

    students = db.execute("""
        SELECT s.name, s.username, s.created_at, s.xp, s.level,
               COUNT(r.id) as quizzes_taken,
               COALESCE(AVG(r.pct),0) as avg_pct
        FROM students s
        LEFT JOIN results r ON r.student_id = s.id
        GROUP BY s.id
        ORDER BY avg_pct DESC
    """).fetchall()
    db.close()

    student_rows = ""
    for s in students:
        color = "#7effa0" if s["avg_pct"] >= 60 else "#ff7e7e"
        student_rows += f"""<tr>
          <td style="padding:10px;color:#fff">{s['name']}</td>
          <td style="padding:10px;color:#888">@{s['username']}</td>
          <td style="padding:10px;color:#888">{s['quizzes_taken']}</td>
          <td style="padding:10px;color:{color};font-weight:700">{round(s['avg_pct'])}%</td>
          <td style="padding:10px;color:#888">⭐ {s['xp']} XP</td>
          <td style="padding:10px;color:#888">🏆 Lv.{s['level']}</td>
          <td style="padding:10px;color:#666;font-size:13px">{s['created_at'][:10]}</td>
        </tr>"""

    if not student_rows:
        student_rows = '<tr><td colspan="7" style="padding:16px;color:#666;text-align:center">No students registered yet.</td></tr>'

    return base(f"""
<h1>🛠️ Admin Panel</h1>
<div class="stat-grid">
  <div class="stat-box"><div class="stat-num">{total_students}</div><div class="stat-label">Students</div></div>
  <div class="stat-box"><div class="stat-num">{total_quizzes}</div><div class="stat-label">Quizzes</div></div>
  <div class="stat-box"><div class="stat-num">{total_results}</div><div class="stat-label">Attempts</div></div>
  <div class="stat-box"><div class="stat-num">{total_videos}</div><div class="stat-label">Videos</div></div>
  <div class="stat-box"><div class="stat-num">{round(avg_score)}%</div><div class="stat-label">Class Average</div></div>
</div>
<h2>Student Performance</h2>
<div class="card" style="overflow-x:auto">
  <table style="width:100%;border-collapse:collapse">
    <thead>
      <tr style="border-bottom:1px solid #2a2a4a">
        <th style="padding:10px;text-align:left;color:#888;font-size:13px">Name</th>
        <th style="padding:10px;text-align:left;color:#888;font-size:13px">Username</th>
        <th style="padding:10px;text-align:left;color:#888;font-size:13px">Quizzes</th>
        <th style="padding:10px;text-align:left;color:#888;font-size:13px">Average</th>
        <th style="padding:10px;text-align:left;color:#888;font-size:13px">XP</th>
        <th style="padding:10px;text-align:left;color:#888;font-size:13px">Level</th>
        <th style="padding:10px;text-align:left;color:#888;font-size:13px">Joined</th>
      </tr>
    </thead>
    <tbody>{student_rows}</tbody>
  </table>
</div>
""", "admin")

# ─── FEATURE 9: AI CHAT TUTOR ──────────────────────────────────────────────

@app.route("/chat", methods=["GET", "POST"])
def chat_tutor():
    if not session.get("student_id"):
        return redirect(url_for("student"))
    
    db = get_db()
    chat_history = db.execute("""
        SELECT * FROM chat_history 
        WHERE student_id = ? 
        ORDER BY created_at DESC LIMIT 20
    """, (session["student_id"],)).fetchall()
    db.close()
    
    chat_html = ""
    for msg in reversed(chat_history):
        chat_html += f"""
        <div style="margin-bottom:12px;">
            <div style="background:#7c6fff22;padding:8px 12px;border-radius:8px;color:#7c6fff;font-size:13px;max-width:80%;">
                <strong>You:</strong> {msg['question']}
            </div>
            <div style="background:#16162a;padding:8px 12px;border-radius:8px;color:#ddd;font-size:14px;max-width:80%;margin-left:20%;">
                <strong>🤖 Tutor:</strong> {msg['answer']}
            </div>
        </div>
        """
    
    if not chat_html:
        chat_html = "<p style='color:#888;text-align:center;padding:2rem;'>Ask me anything about your studies! 📚</p>"
    
    return base(f"""
<h1>💬 AI Chat Tutor</h1>
<p style="margin-bottom:1rem;">Ask any question about your lessons, and I'll help you understand!</p>

<div class="card chat-container">
    {chat_html}
</div>

<div class="card">
    <form method="POST" action="/chat/ask" id="chatForm">
        <label class="field-label">Your Question</label>
        <textarea name="question" placeholder="e.g., Can you explain photosynthesis in simple terms?" required></textarea>
        <button type="submit" onclick="document.getElementById('chatLoading').style.display='block'">Ask Tutor 🤖</button>
        <div id="chatLoading" class="loading" style="display:none;margin-top:10px;">⏳ Thinking...</div>
    </form>
</div>

<div class="btn-row">
    <a class="btn btn-secondary" href="/student/dashboard">Back to Dashboard</a>
</div>
""", "student")

@app.route("/chat/ask", methods=["POST"])
def chat_ask():
    if not session.get("student_id"):
        return redirect(url_for("student"))
    
    question = request.form.get("question", "").strip()
    
    if not question:
        return redirect(url_for("chat_tutor"))
    
    db = get_db()
    results = db.execute("""
        SELECT pct, q.title 
        FROM results r 
        JOIN quizzes q ON r.quiz_id = q.id 
        WHERE r.student_id = ? 
        ORDER BY r.created_at DESC LIMIT 5
    """, (session["student_id"],)).fetchall()
    
    context = "Student's recent quiz scores:\n"
    for r in results:
        context += f"- {r['title']}: {r['pct']}%\n"
    
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=500,
            messages=[{"role": "user", "content": f"""
You are a patient and helpful AI tutor for GradeX students.

Context about the student:
{context}

Student's question: {question}

Provide a clear, concise, and encouraging answer. Use examples if helpful. Keep it to 2-3 paragraphs.
Start with "Hey! " and be supportive.
"""}]
        )
        answer = response.choices[0].message.content.strip()
        db.execute("INSERT INTO chat_history (student_id, question, answer) VALUES (?,?,?)",
                  (session["student_id"], question, answer))
        db.commit()
        db.close()
    except Exception as e:
        answer = "I'm having trouble thinking right now. Please try again in a moment! 😅"
    
    return redirect(url_for("chat_tutor"))

# ─── FEATURE 11: STUDENT PORTFOLIO ─────────────────────────────────────────

@app.route("/portfolio/<int:student_id>")
def portfolio(student_id):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if not student:
        return redirect(url_for("home"))
    
    achievements = db.execute("SELECT * FROM achievements WHERE student_id=? ORDER BY unlocked_at DESC", (student_id,)).fetchall()
    results = db.execute("""
        SELECT r.*, q.title, q.difficulty 
        FROM results r 
        JOIN quizzes q ON r.quiz_id = q.id 
        WHERE r.student_id = ? 
        ORDER BY r.created_at DESC LIMIT 10
    """, (student_id,)).fetchall()
    stats = db.execute("""
        SELECT 
            COUNT(r.id) as total_quizzes,
            COALESCE(AVG(r.pct),0) as avg_score,
            MAX(r.pct) as best_score,
            SUM(r.score) as total_correct,
            SUM(r.total) as total_questions
        FROM results r
        WHERE r.student_id = ?
    """, (student_id,)).fetchone()
    db.close()
    
    ach_html = ""
    for a in achievements:
        ach_html += f"""
        <div style="display:inline-block;background:#1a1a3a;border:1px solid #7c6fff44;border-radius:8px;padding:8px 14px;margin:4px;">
            <span style="font-size:20px;">{a['icon']}</span>
            <span style="font-size:12px;color:#ddd;margin-left:6px;">{a['name']}</span>
        </div>
        """
    if not ach_html:
        ach_html = "<p style='color:#888;'>No achievements yet. Keep taking quizzes! 🎯</p>"
    
    results_html = ""
    for r in results:
        color = "#7effa0" if r["pct"] >= 60 else "#ff7e7e"
        results_html += f"""
        <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #2a2a4a;">
            <div>
                <span style="color:#ccc;">{r['title']}</span>
                <span class="badge badge-{r['difficulty'].lower()}" style="margin-left:8px;">{r['difficulty']}</span>
            </div>
            <span style="color:{color};font-weight:700;">{r['pct']}%</span>
        </div>
        """
    if not results_html:
        results_html = "<p style='color:#888;'>No quizzes taken yet.</p>"
    
    return base(f"""
<div style="text-align:center;padding:2rem 0;">
    <div style="font-size:64px;margin-bottom:0.5rem;">👤</div>
    <h1 style="font-size:32px;">{student['name']}</h1>
    <p style="color:#888;">@{student['username']}</p>
    <div style="margin:1rem 0;">
        <span style="background:#1a1a3a;border:1px solid #7c6fff44;border-radius:8px;padding:6px 16px;margin:0 8px;">
            🏆 Level {student['level']}
        </span>
        <span style="background:#1a1a3a;border:1px solid #7c6fff44;border-radius:8px;padding:6px 16px;margin:0 8px;">
            ⭐ {student['xp']} XP
        </span>
    </div>
</div>

<div class="stat-grid">
    <div class="stat-box">
        <div class="stat-num">{stats['total_quizzes']}</div>
        <div class="stat-label">Quizzes Taken</div>
    </div>
    <div class="stat-box">
        <div class="stat-num">{round(stats['avg_score'])}%</div>
        <div class="stat-label">Average Score</div>
    </div>
    <div class="stat-box">
        <div class="stat-num">{stats['best_score']}%</div>
        <div class="stat-label">Best Score</div>
    </div>
    <div class="stat-box">
        <div class="stat-num">{stats['total_correct']}/{stats['total_questions']}</div>
        <div class="stat-label">Total Correct</div>
    </div>
</div>

<div class="card">
    <h2>🏅 Achievements</h2>
    <div style="display:flex;flex-wrap:wrap;gap:8px;">
        {ach_html}
    </div>
</div>

<div class="card">
    <h2>📝 Recent Quizzes</h2>
    {results_html}
</div>

<div class="btn-row">
    <a class="btn btn-secondary" href="/student/dashboard">Back to Dashboard</a>
</div>
""", "student")

# ─── FEATURE 13: FILE PREVIEW ──────────────────────────────────────────────

@app.route("/files/preview/<filename>")
def preview_file(filename):
    if not session.get("student_id") and not session.get("teacher"):
        return redirect(url_for("student"))
    
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(file_path):
        return "File not found", 404
    
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    db = get_db()
    file_info = db.execute("SELECT * FROM files WHERE filename=?", (filename,)).fetchone()
    db.close()
    
    if ext in ["png", "jpg", "jpeg", "gif"]:
        return send_from_directory(UPLOAD_FOLDER, filename)
    
    elif ext == "pdf":
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>{file_info['original_name']}</title>
            {STYLE}
            <style>
                body {{ margin: 0; padding: 0; background: #0f0f1a; }}
                .container {{ max-width: 100%; padding: 0; margin: 0; }}
                embed {{ width: 100%; height: 100vh; border: none; }}
                .nav-bar {{ background: #16162a; padding: 10px 20px; border-bottom: 1px solid #2a2a4a; display: flex; align-items: center; gap: 1rem; }}
                .nav-bar a {{ color: #7c6fff; text-decoration: none; }}
            </style>
        </head>
        <body>
            <div class="nav-bar">
                <span style="color:#fff;font-weight:600;">📄 {file_info['original_name']}</span>
                <a href="/files/{filename}" download style="margin-left:auto;">⬇️ Download</a>
                <a href="javascript:history.back()">✕ Close</a>
            </div>
            <embed src="/files/{filename}" type="application/pdf">
        </body>
        </html>
        """
    
    elif ext in ["docx", "txt", "pptx"]:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return base(f"""
            <h1>📄 {file_info['original_name']}</h1>
            <div class="card">
                <pre style="white-space:pre-wrap;font-family:monospace;font-size:14px;color:#ddd;background:#0f0f1a;padding:12px;border-radius:8px;max-height:600px;overflow-y:auto;">
{content[:50000]}{'... (truncated)' if len(content) > 50000 else ''}
                </pre>
            </div>
            <div class="btn-row">
                <a class="btn btn-secondary" href="/files/{filename}" download>⬇️ Download</a>
                <a class="btn btn-secondary" href="javascript:history.back()">← Back</a>
            </div>
            """, "student")
        except:
            return send_from_directory(UPLOAD_FOLDER, filename)
    else:
        return send_from_directory(UPLOAD_FOLDER, filename)

# ─── API ─────────────────────────────────────────────────────────────────────

@app.route("/api/leaderboard")
def leaderboard():
    db = get_db()
    rows = db.execute("""
        SELECT s.name, COUNT(r.id) as quizzes, COALESCE(AVG(r.pct),0) as avg
        FROM students s
        LEFT JOIN results r ON r.student_id=s.id
        GROUP BY s.id
        HAVING quizzes > 0
        ORDER BY avg DESC
        LIMIT 10
    """).fetchall()
    db.close()
    return json.dumps([{"name": r["name"], "quizzes": r["quizzes"], "avg": round(r["avg"])} for r in rows])

# ─── FILES & MISC ─────────────────────────────────────────────────────────────

@app.route("/files/<filename>")
def serve_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True)