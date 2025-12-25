import os
import shutil
from functools import wraps
from flask import (
    Flask, render_template, request,
    jsonify, send_from_directory,
    redirect, url_for, session
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STORAGE = os.path.join(BASE_DIR, "storage")
os.makedirs(STORAGE, exist_ok=True)

app = Flask(__name__)
app.secret_key = "datahub-secret-key"  
app.config["MAX_CONTENT_LENGTH"] = None

USERNAME = "user"
PASSWORD_HASH = generate_password_hash("1234")


def safe_path(path=""):
    final = os.path.abspath(os.path.join(STORAGE, path))
    if not final.startswith(STORAGE):
        raise ValueError("Bad path")
    return final


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login = request.form.get("login")
        password = request.form.get("password")

        if login == USERNAME and check_password_hash(PASSWORD_HASH, password):
            session["auth"] = True
            return redirect(url_for("index"))

        return render_template("login.html", error="Неверный логин или пароль")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/list")
@login_required
def list_files():
    path = request.args.get("path", "")
    folder = safe_path(path)

    items = []
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        items.append({
            "name": name,
            "is_dir": os.path.isdir(full),
            "path": os.path.join(path, name)
        })
    return jsonify(items)


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    path = request.args.get("path", "")
    folder = safe_path(path)

    for f in request.files.getlist("files"):
        if f.filename:
            f.save(os.path.join(folder, secure_filename(f.filename)))

    return jsonify(ok=True)


@app.route("/api/download")
@login_required
def download():
    path = request.args.get("path")
    full = safe_path(path)
    return send_from_directory(
        os.path.dirname(full),
        os.path.basename(full),
        as_attachment=True
    )


@app.route("/api/create-folder", methods=["POST"])
@login_required
def create_folder():
    data = request.json
    parent = safe_path(data.get("path", ""))
    os.makedirs(os.path.join(parent, data["name"]), exist_ok=True)
    return jsonify(ok=True)


@app.route("/api/delete", methods=["POST"])
@login_required
def delete():
    path = request.json["path"]
    full = safe_path(path)

    if os.path.isdir(full):
        shutil.rmtree(full)
    else:
        os.remove(full)

    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
