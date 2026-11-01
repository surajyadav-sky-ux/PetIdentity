import os
import sqlite3
import uuid
from functools import wraps

import qrcode
import qrcode.image.pure
from flask import (
    Flask, request, session, redirect, url_for,
    render_template, g, flash, send_from_directory, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "database.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
QR_FOLDER = os.path.join(BASE_DIR, "static", "qrcodes")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key-in-production")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB max upload

# SITE_URL: set this to your real public domain in production, e.g.
#   export SITE_URL="https://yourdomain.com"
# so that QR codes always encode a stable, correct HTTPS URL regardless of
# how the server itself is reached (reverse proxy, load balancer, local
# testing, etc). If unset, the app falls back to building the URL from the
# incoming request, forcing the "https" scheme unless running in debug mode.
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            security_answer_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS pets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            unique_id TEXT UNIQUE NOT NULL,
            pet_name TEXT NOT NULL,
            pet_type TEXT,
            breed TEXT,
            age TEXT,
            gender TEXT,
            color TEXT,
            photo TEXT,
            owner_name TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            additional_info TEXT,
            qr_code TEXT,
            show_address INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    # Migration safety-net: if an older database.db already exists without
    # these columns, add them instead of crashing.
    existing_pet_cols = {row[1] for row in db.execute("PRAGMA table_info(pets)")}
    if "show_address" not in existing_pet_cols:
        db.execute("ALTER TABLE pets ADD COLUMN show_address INTEGER NOT NULL DEFAULT 0")

    existing_user_cols = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "security_answer_hash" not in existing_user_cols:
        db.execute("ALTER TABLE users ADD COLUMN security_answer_hash TEXT")

    db.commit()
    db.close()


SECURITY_QUESTION = "What is your favourite game?"


def normalize_answer(answer):
    """Lowercase + strip so answer matching ignores case and extra spaces."""
    return answer.strip().lower()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def current_user():
    if session.get("user_id") is None:
        return None
    db = get_db()
    return db.execute(
        "SELECT id, name, email FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# Routes: home / auth
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        security_answer = request.form.get("security_answer", "").strip()

        if not name or not email or not password or not security_answer:
            flash("All fields are required.", "danger")
            return render_template("register.html")

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            flash("An account with that email already exists.", "danger")
            return render_template("register.html")

        password_hash = generate_password_hash(password)
        security_answer_hash = generate_password_hash(normalize_answer(security_answer))
        db.execute(
            "INSERT INTO users (name, email, password_hash, security_answer_hash) VALUES (?, ?, ?, ?)",
            (name, email, password_hash, security_answer_hash),
        )
        db.commit()
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", security_question=SECURITY_QUESTION)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["id"]
        flash(f"Welcome back, {user['name']}!", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Forgot password — security question flow (no email/SMS needed)
# ---------------------------------------------------------------------------

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user is None:
            flash("No account found with that email.", "danger")
            return render_template("forgot_password.html")

        session["fp_user_id"] = user["id"]
        session.pop("fp_verified", None)
        return redirect(url_for("forgot_password_question"))

    return render_template("forgot_password.html")


@app.route("/forgot-password/question", methods=["GET", "POST"])
def forgot_password_question():
    user_id = session.get("fp_user_id")
    if not user_id:
        return redirect(url_for("forgot_password"))

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None or not user["security_answer_hash"]:
        flash("No security question is set up for this account.", "danger")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        answer = request.form.get("security_answer", "")
        if check_password_hash(user["security_answer_hash"], normalize_answer(answer)):
            session["fp_verified"] = True
            return redirect(url_for("forgot_password_reset"))
        flash("That answer is incorrect. Please try again.", "danger")
        return render_template("security_question.html", security_question=SECURITY_QUESTION)

    return render_template("security_question.html", security_question=SECURITY_QUESTION)


@app.route("/forgot-password/reset", methods=["GET", "POST"])
def forgot_password_reset():
    user_id = session.get("fp_user_id")
    if not user_id or not session.get("fp_verified"):
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("reset_password.html")

        db = get_db()
        password_hash = generate_password_hash(password)
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        db.commit()

        session.pop("fp_user_id", None)
        session.pop("fp_verified", None)
        flash("Password updated. Please log in with your new password.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html")


# ---------------------------------------------------------------------------
# Routes: dashboard / pet CRUD (private, per-user)
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    pets = db.execute(
        "SELECT * FROM pets WHERE user_id = ? ORDER BY created_at DESC",
        (session["user_id"],),
    ).fetchall()
    public_urls = {pet["id"]: get_public_pet_url(pet["unique_id"]) for pet in pets}
    return render_template("dashboard.html", pets=pets, public_urls=public_urls)


def get_public_pet_url(unique_id):
    """
    Build the plain HTTPS URL encoded in a pet's QR code.

    This URL contains nothing but a standard web address (no app-specific
    payload), so any camera app or QR scanner (Google Lens, Samsung
    scanner, iPhone Camera, etc.) recognizes it as a link and offers to
    open it directly in the phone's browser - no app install required.
    """
    if SITE_URL:
        return f"{SITE_URL}/pet/{unique_id}"
    # Fallback for local/dev use: build from the current request, forcing
    # https unless we're clearly in a local debug session.
    scheme = "http" if app.debug and request.host.startswith(("localhost", "127.0.0.1")) else "https"
    return url_for("pet_public", unique_id=unique_id, _external=True, _scheme=scheme)


def generate_qr_for_pet(unique_id):
    """Generate a QR code PNG that encodes the public profile URL for a pet."""
    public_url = get_public_pet_url(unique_id)
    qr_filename = f"{unique_id}.png"
    qr_path = os.path.join(QR_FOLDER, qr_filename)
    img = qrcode.make(public_url, image_factory=qrcode.image.pure.PyPNGImage)
    img.save(qr_path)
    return qr_filename


@app.route("/pet/add", methods=["GET", "POST"])
@login_required
def add_pet():
    if request.method == "POST":
        pet_name = request.form.get("pet_name", "").strip()
        if not pet_name:
            flash("Pet name is required.", "danger")
            return render_template("add_pet.html")

        photo_filename = None
        photo = request.files.get("photo")
        if photo and photo.filename and allowed_file(photo.filename):
            ext = photo.filename.rsplit(".", 1)[1].lower()
            photo_filename = f"{uuid.uuid4().hex}.{ext}"
            photo.save(os.path.join(app.config["UPLOAD_FOLDER"], photo_filename))

        unique_id = uuid.uuid4().hex[:12]

        show_address = 1 if request.form.get("show_address") else 0

        db = get_db()
        db.execute(
            """
            INSERT INTO pets (
                user_id, unique_id, pet_name, pet_type, breed, age, gender,
                color, photo, owner_name, phone, email, address,
                additional_info, show_address
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["user_id"], unique_id, pet_name,
                request.form.get("pet_type", "").strip(),
                request.form.get("breed", "").strip(),
                request.form.get("age", "").strip(),
                request.form.get("gender", "").strip(),
                request.form.get("color", "").strip(),
                photo_filename,
                request.form.get("owner_name", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("address", "").strip(),
                request.form.get("additional_info", "").strip(),
                show_address,
            ),
        )
        db.commit()

        qr_filename = generate_qr_for_pet(unique_id)
        db.execute(
            "UPDATE pets SET qr_code = ? WHERE unique_id = ?",
            (qr_filename, unique_id),
        )
        db.commit()

        flash(f"{pet_name} was added and a QR code was generated.", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_pet.html")


def get_owned_pet_or_404(pet_id):
    """Fetch a pet, but only if it belongs to the current user. Otherwise 404/abort."""
    db = get_db()
    pet = db.execute("SELECT * FROM pets WHERE id = ?", (pet_id,)).fetchone()
    if pet is None:
        abort(404)
    if pet["user_id"] != session["user_id"]:
        abort(403)
    return pet


@app.route("/pet/edit/<int:pet_id>", methods=["GET", "POST"])
@login_required
def edit_pet(pet_id):
    pet = get_owned_pet_or_404(pet_id)
    db = get_db()

    if request.method == "POST":
        pet_name = request.form.get("pet_name", "").strip()
        if not pet_name:
            flash("Pet name is required.", "danger")
            return render_template("edit_pet.html", pet=pet)

        photo_filename = pet["photo"]
        photo = request.files.get("photo")
        if photo and photo.filename and allowed_file(photo.filename):
            ext = photo.filename.rsplit(".", 1)[1].lower()
            photo_filename = f"{uuid.uuid4().hex}.{ext}"
            photo.save(os.path.join(app.config["UPLOAD_FOLDER"], photo_filename))

        show_address = 1 if request.form.get("show_address") else 0

        db.execute(
            """
            UPDATE pets SET
                pet_name = ?, pet_type = ?, breed = ?, age = ?, gender = ?,
                color = ?, photo = ?, owner_name = ?, phone = ?, email = ?,
                address = ?, additional_info = ?, show_address = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                pet_name,
                request.form.get("pet_type", "").strip(),
                request.form.get("breed", "").strip(),
                request.form.get("age", "").strip(),
                request.form.get("gender", "").strip(),
                request.form.get("color", "").strip(),
                photo_filename,
                request.form.get("owner_name", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("address", "").strip(),
                request.form.get("additional_info", "").strip(),
                show_address,
                pet_id, session["user_id"],
            ),
        )
        db.commit()
        flash(f"{pet_name} was updated.", "success")
        return redirect(url_for("dashboard"))

    return render_template("edit_pet.html", pet=pet)


@app.route("/pet/delete/<int:pet_id>", methods=["POST"])
@login_required
def delete_pet(pet_id):
    pet = get_owned_pet_or_404(pet_id)
    db = get_db()

    # Clean up files on disk (best-effort)
    if pet["photo"]:
        photo_path = os.path.join(app.config["UPLOAD_FOLDER"], pet["photo"])
        if os.path.exists(photo_path):
            os.remove(photo_path)
    if pet["qr_code"]:
        qr_path = os.path.join(QR_FOLDER, pet["qr_code"])
        if os.path.exists(qr_path):
            os.remove(qr_path)

    db.execute("DELETE FROM pets WHERE id = ? AND user_id = ?", (pet_id, session["user_id"]))
    db.commit()
    flash(f"{pet['pet_name']} was deleted.", "info")
    return redirect(url_for("dashboard"))


@app.route("/pet/qr/download/<int:pet_id>")
@login_required
def download_qr(pet_id):
    pet = get_owned_pet_or_404(pet_id)
    if not pet["qr_code"]:
        abort(404)
    return send_from_directory(
        QR_FOLDER, pet["qr_code"], as_attachment=True,
        download_name=f"{pet['pet_name']}_qr.png",
    )


# ---------------------------------------------------------------------------
# Public route: pet profile (no login required)
# ---------------------------------------------------------------------------

PET_TYPE_EMOJI = {
    "cat": "🐱", "dog": "🐶", "bird": "🐦", "rabbit": "🐰",
    "fish": "🐠", "hamster": "🐹", "turtle": "🐢", "horse": "🐴",
}


@app.route("/pet/<unique_id>")
def pet_public(unique_id):
    db = get_db()
    pet = db.execute("SELECT * FROM pets WHERE unique_id = ?", (unique_id,)).fetchone()
    if pet is None:
        abort(404)
    emoji = PET_TYPE_EMOJI.get((pet["pet_type"] or "").strip().lower(), "🐾")
    return render_template("pet_public.html", pet=pet, emoji=emoji)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="You don't have permission to do that."), 403


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
