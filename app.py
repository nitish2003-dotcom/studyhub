from flask import Flask, render_template, request, redirect, session, url_for
import sqlite3
import os
from dotenv import load_dotenv
from groq import Groq
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from flask import send_file
import io

load_dotenv(".env")

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


app = Flask(__name__)
app.secret_key = "secret123"

UPLOAD_FOLDER = "static/uploads"
ALLOWED_EXTENSIONS = {"pdf"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ---------- HELPERS ----------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_db():
    conn = sqlite3.connect("studyhub.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = sqlite3.connect("studyhub.db")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT,
        password TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name TEXT,
        task TEXT,
        completed INTEGER DEFAULT 0
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS study_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        duration INTEGER,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        semester INTEGER DEFAULT 1
    )
    """)
        # courses table me semester column add karne ke liye
    columns = conn.execute("PRAGMA table_info(courses)").fetchall()
    column_names = [col[1] for col in columns]

    if "semester" not in column_names:
        conn.execute("ALTER TABLE courses ADD COLUMN semester INTEGER DEFAULT 1")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS course_modules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER,
        module_name TEXT,
        video_link TEXT,
        pdf_file TEXT,
        FOREIGN KEY(course_id) REFERENCES courses(id)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS module_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name TEXT,
        module_id INTEGER,
        completed INTEGER DEFAULT 0,
        FOREIGN KEY(module_id) REFERENCES course_modules(id)
    )
    """)

    conn.commit()
    conn.close()


def get_course_progress(user_name, course_id):
    conn = get_db()

    total_modules = conn.execute(
        "SELECT COUNT(*) AS total FROM course_modules WHERE course_id=?",
        (course_id,)
    ).fetchone()["total"]

    if total_modules == 0:
        conn.close()
        return 0

    completed_modules = conn.execute("""
        SELECT COUNT(*) AS completed
        FROM module_progress mp
        JOIN course_modules cm ON mp.module_id = cm.id
        WHERE mp.user_name=? AND cm.course_id=? AND mp.completed=1
    """, (user_name, course_id)).fetchone()["completed"]

    conn.close()
    return int((completed_modules / total_modules) * 100)


init_db()


# ---------- ROUTES ----------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]

        conn = get_db()
        conn.execute(
            "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
            (name, email, password)
        )
        conn.commit()
        conn.close()
        return redirect("/login")

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email=? AND password=?",
            (email, password)
        ).fetchone()
        conn.close()

        if user:
            session["user"] = user["name"]
            return redirect("/dashboard")
        return "Invalid login"

    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")

    conn = get_db()

    total = conn.execute(
        "SELECT SUM(duration) AS total FROM study_sessions WHERE user=?",
        (session["user"],)
    ).fetchone()

    total_time = total["total"] if total["total"] else 0

    data = conn.execute("""
        SELECT DATE(date) AS day, SUM(duration) AS total
        FROM study_sessions
        WHERE user=?
        GROUP BY day
        ORDER BY day DESC
        LIMIT 7
    """, (session["user"],)).fetchall()

    tasks = conn.execute(
        "SELECT * FROM tasks WHERE user_name=? ORDER BY id DESC",
        (session["user"],)
    ).fetchall()

    conn.close()

    days = []
    times = []
    for row in data:
        days.append(row["day"])
        times.append(row["total"] // 60)

    days.reverse()
    times.reverse()

    return render_template(
        "dashboard.html",
        user=session["user"],
        total_time=total_time,
        days=days,
        times=times,
        tasks=tasks
    )


@app.route("/timer")
def timer():
    if "user" not in session:
        return redirect("/login")
    return render_template("timer.html", user=session["user"])


@app.route("/courses")
def courses():
    if "user" not in session:
        return redirect("/login")

    user_name = session["user"]
    conn = get_db()

    all_courses = conn.execute(
        "SELECT * FROM courses ORDER BY semester, title"
    ).fetchall()

    semester_data = {}

    for course in all_courses:
        semester = course["semester"]
        if semester not in semester_data:
            semester_data[semester] = []

        semester_data[semester].append({
            "id": course["id"],
            "title": course["title"],
            "desc": course["description"],
            "semester": course["semester"],
            "icon": "📘",
            "badge": f"Semester {course['semester']}",
            "badge_class": "ongoing",
            "progress": get_course_progress(user_name, course["id"])
        })

    conn.close()

    return render_template(
        "courses.html",
        semester_data=semester_data,
        user=session["user"]
    )


@app.route("/add-course", methods=["POST"])
def add_course():
    if "user" not in session:
        return redirect("/login")

    title = request.form["title"]
    description = request.form["description"]
    semester = request.form["semester"]

    conn = get_db()
    conn.execute(
        "INSERT INTO courses (title, description, semester) VALUES (?, ?, ?)",
        (title, description, semester)
    )
    conn.commit()
    conn.close()

    return redirect("/courses")


@app.route("/course/<int:course_id>")
def course_detail(course_id):
    if "user" not in session:
        return redirect("/login")

    conn = get_db()

    course = conn.execute(
        "SELECT * FROM courses WHERE id=?",
        (course_id,)
    ).fetchone()

    if not course:
        conn.close()
        return "Course not found", 404

    modules = conn.execute("""
        SELECT cm.*,
               COALESCE(mp.completed, 0) AS completed
        FROM course_modules cm
        LEFT JOIN module_progress mp
             ON cm.id = mp.module_id AND mp.user_name = ?
        WHERE cm.course_id = ?
        ORDER BY cm.id
    """, (session["user"], course_id)).fetchall()

    conn.close()

    course_data = {
        "id": course["id"],
        "title": course["title"],
        "description": course["description"],
        "semester": course["semester"],
        "icon": "📘",
        "progress": get_course_progress(session["user"], course_id),
        "modules": modules
    }

    return render_template("course_detail.html", course=course_data)


@app.route("/add-module/<int:course_id>", methods=["POST"])
def add_module(course_id):
    if "user" not in session:
        return redirect("/login")

    module_name = request.form["module_name"]
    video_link = request.form["video_link"]
    pdf = request.files.get("pdf_file")
    pdf_filename = ""

    if pdf and pdf.filename:
        if allowed_file(pdf.filename):
            pdf_filename = secure_filename(pdf.filename)
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], pdf_filename)
            pdf.save(save_path)

    conn = get_db()
    conn.execute(
        "INSERT INTO course_modules (course_id, module_name, video_link, pdf_file) VALUES (?, ?, ?, ?)",
        (course_id, module_name, video_link, pdf_filename)
    )
    conn.commit()
    conn.close()

    return redirect(f"/course/{course_id}")


@app.route("/toggle-module/<int:course_id>/<int:module_id>")
def toggle_module(course_id, module_id):
    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM module_progress WHERE user_name=? AND module_id=?",
        (session["user"], module_id)
    ).fetchone()

    if row:
        new_status = 0 if row["completed"] == 1 else 1
        conn.execute(
            "UPDATE module_progress SET completed=? WHERE user_name=? AND module_id=?",
            (new_status, session["user"], module_id)
        )
    else:
        conn.execute(
            "INSERT INTO module_progress (user_name, module_id, completed) VALUES (?, ?, ?)",
            (session["user"], module_id, 1)
        )

    conn.commit()
    conn.close()

    return redirect(f"/course/{course_id}")


@app.route("/add_task", methods=["POST"])
def add_task():
    if "user" not in session:
        return redirect("/login")

    task = request.form["task"]
    if task.strip():
        conn = get_db()
        conn.execute(
            "INSERT INTO tasks (user_name, task, completed) VALUES (?, ?, ?)",
            (session["user"], task, 0)
        )
        conn.commit()
        conn.close()

    return redirect("/dashboard")


@app.route("/toggle_task/<int:task_id>")
def toggle_task(task_id):
    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    task = conn.execute(
        "SELECT * FROM tasks WHERE id=? AND user_name=?",
        (task_id, session["user"])
    ).fetchone()

    if task:
        new_status = 0 if task["completed"] == 1 else 1
        conn.execute(
            "UPDATE tasks SET completed=? WHERE id=?",
            (new_status, task_id)
        )
        conn.commit()

    conn.close()
    return redirect("/dashboard")


@app.route("/delete_task/<int:task_id>")
def delete_task(task_id):
    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    conn.execute(
        "DELETE FROM tasks WHERE id=? AND user_name=?",
        (task_id, session["user"])
    )
    conn.commit()
    conn.close()

    return redirect("/dashboard")


@app.route("/save-session", methods=["POST"])
def save_session():
    if "user" not in session:
        return "Unauthorized"

    duration = request.form["duration"]

    conn = get_db()
    conn.execute(
        "INSERT INTO study_sessions (user, duration) VALUES (?, ?)",
        (session["user"], duration)
    )
    conn.commit()
    conn.close()

    return "Saved"


@app.route("/ai-help")
def ai_help():
    if "user" not in session:
        return redirect("/login")
    return render_template("ai.html", user=session["user"])


@app.route("/settings")
def settings():
    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE name=?",
        (session["user"],)
    ).fetchone()
    conn.close()

    return render_template("settings.html", user=user)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/login")
@app.route("/delete-pdf/<int:course_id>/<int:module_id>")
def delete_pdf(course_id, module_id):
    if "user" not in session:
        return redirect("/login")

    conn = get_db()

    module = conn.execute(
        "SELECT pdf_file FROM course_modules WHERE id=?",
        (module_id,)
    ).fetchone()

    if module and module["pdf_file"]:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], module["pdf_file"])

        # file delete from folder
        if os.path.exists(file_path):
            os.remove(file_path)

        # DB se remove
        conn.execute(
            "UPDATE course_modules SET pdf_file='' WHERE id=?",
            (module_id,)
        )
        conn.commit()

    conn.close()

    return redirect(f"/course/{course_id}")
@app.route("/delete-module/<int:course_id>/<int:module_id>")
def delete_module(course_id, module_id):
    if "user" not in session:
        return redirect("/login")

    conn = get_db()

    # pehle PDF file delete kar
    module = conn.execute(
        "SELECT pdf_file FROM course_modules WHERE id=?",
        (module_id,)
    ).fetchone()

    if module and module["pdf_file"]:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], module["pdf_file"])
        if os.path.exists(file_path):
            os.remove(file_path)

    # module_progress delete
    conn.execute(
        "DELETE FROM module_progress WHERE module_id=?",
        (module_id,)
    )

    # module delete
    conn.execute(
        "DELETE FROM course_modules WHERE id=?",
        (module_id,)
    )

    conn.commit()
    conn.close()

    return redirect(f"/course/{course_id}")
@app.route("/edit-module/<int:course_id>/<int:module_id>", methods=["GET", "POST"])
def edit_module(course_id, module_id):
    if "user" not in session:
        return redirect("/login")

    conn = get_db()

    module = conn.execute(
        "SELECT * FROM course_modules WHERE id=? AND course_id=?",
        (module_id, course_id)
    ).fetchone()

    if not module:
        conn.close()
        return "Module not found", 404

    if request.method == "POST":
        module_name = request.form["module_name"]
        video_link = request.form["video_link"]
        pdf = request.files.get("pdf_file")

        pdf_filename = module["pdf_file"]

        if pdf and pdf.filename:
            if allowed_file(pdf.filename):
                # old pdf delete
                if module["pdf_file"]:
                    old_path = os.path.join(app.config["UPLOAD_FOLDER"], module["pdf_file"])
                    if os.path.exists(old_path):
                        os.remove(old_path)

                pdf_filename = secure_filename(pdf.filename)
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], pdf_filename)
                pdf.save(save_path)

        conn.execute(
            "UPDATE course_modules SET module_name=?, video_link=?, pdf_file=? WHERE id=?",
            (module_name, video_link, pdf_filename, module_id)
        )
        conn.commit()
        conn.close()

        return redirect(f"/course/{course_id}")

    conn.close()
    return render_template("edit_module.html", module=module, course_id=course_id)

@app.route("/download-ai-pdf", methods=["POST"])
def download_ai_pdf():
    if "user" not in session:
        return redirect("/login")

    content = request.form["content"]

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 50
    pdf.setFont("Helvetica", 12)

    for line in content.split("\n"):
        if y < 50:
            pdf.showPage()
            pdf.setFont("Helvetica", 12)
            y = height - 50
        pdf.drawString(40, y, line[:100])
        y -= 20

    pdf.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="studyhub_notes.pdf",
        mimetype="application/pdf"
    )

@app.route("/ask-ai", methods=["POST"])
def ask_ai():
    user_message = request.form["message"]

    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": """
You are StudyHub AI, a smart study assistant for B.Tech students.

First detect the subject automatically from the student's question.
Possible subjects include:
- DSA
- DBMS
- Operating System
- Computer Networks
- Python
- HTML/CSS/JavaScript
- General Programming

Rules:
- Answer in simple Hinglish + English mix.
- Keep answers easy and student-friendly.
- If the question is theory, explain point by point.
- If the question is code-related, give short correct code.
- Mention subject name at the top like: Subject: DSA
- If the user asks for notes, give short notes.
- If the user asks for MCQs, give MCQs with answers.
- If the user asks for viva, give viva style answers.
"""
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            model="llama-3.1-8b-instant"
        )

        reply = chat_completion.choices[0].message.content
        return {"reply": reply}

    except Exception as e:
        return {"reply": f"Error: {str(e)}"}

if __name__ == "__main__":
    app.run(debug=True)