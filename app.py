import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# ---------------------------
# App / storage configuration
# ---------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STORAGE = os.path.join(BASE_DIR, "storage")
os.makedirs(STORAGE, exist_ok=True)

app = Flask(__name__, instance_relative_config=True)
# IMPORTANT: в реальном деплое задавайте SECRET_KEY через переменную окружения
app.secret_key = os.environ.get("DATAHUB_SECRET_KEY", "change-me-in-production")

# Максимальный размер загрузки (по умолчанию без лимита).
# Задайте DATAHUB_MAX_CONTENT_LENGTH (в байтах), например 1073741824 для 1 ГБ.
_max_len = os.environ.get("DATAHUB_MAX_CONTENT_LENGTH")
if _max_len:
    try:
        app.config["MAX_CONTENT_LENGTH"] = int(_max_len)
    except ValueError:
        pass

# ---------------------------
# Auth / lockout configuration
# ---------------------------
# Кол-во неудачных попыток до блокировки
MAX_FAILED_ATTEMPTS = int(os.environ.get("DATAHUB_MAX_FAILED_ATTEMPTS", "5"))
# Окно, в котором считаем попытки (минут)
FAILED_WINDOW_MIN = int(os.environ.get("DATAHUB_FAILED_WINDOW_MIN", "15"))
# Длительность блокировки (минут)
LOCKOUT_MIN = int(os.environ.get("DATAHUB_LOCKOUT_MIN", "15"))

# ---------------------------
# DB helpers (SQLite in instance/)
# ---------------------------
def db_path() -> str:
    os.makedirs(app.instance_path, exist_ok=True)
    return os.path.join(app.instance_path, "datahub.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                ip TEXT NOT NULL,
                ts TEXT NOT NULL,
                success INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_locks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                ip TEXT NOT NULL,
                locked_until TEXT NOT NULL,
                UNIQUE(username, ip)
            )
            """
        )
        conn.commit()


def normalize_username(username: str) -> str:
    return (username or "").strip()


def register_user(username: str, password: str) -> None:
    username = normalize_username(username)
    if not username or not password:
        raise ValueError("Логин и пароль обязательны")

    # Минимальная валидация, чтобы не ломать UX
    if len(username) < 3:
        raise ValueError("Логин слишком короткий (минимум 3 символа)")
    if len(password) < 6:
        raise ValueError("Пароль слишком короткий (минимум 6 символов)")

    pw_hash = generate_password_hash(password)
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, pw_hash, now),
        )
        conn.commit()


def find_user(username: str):
    username = normalize_username(username)
    if not username:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    return row


def record_attempt(username: str, ip: str, success: bool) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO auth_attempts (username, ip, ts, success) VALUES (?, ?, ?, ?)",
            (normalize_username(username), ip, datetime.utcnow().isoformat(timespec="seconds"), 1 if success else 0),
        )
        conn.commit()


def get_lock_until(username: str, ip: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT locked_until FROM auth_locks WHERE username = ? AND ip = ?",
            (normalize_username(username), ip),
        ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["locked_until"])
    except Exception:
        return None


def set_lock(username: str, ip: str, until_dt: datetime) -> None:
    until = until_dt.isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO auth_locks (username, ip, locked_until)
            VALUES (?, ?, ?)
            ON CONFLICT(username, ip) DO UPDATE SET locked_until=excluded.locked_until
            """,
            (normalize_username(username), ip, until),
        )
        conn.commit()


def clear_lock(username: str, ip: str) -> None:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM auth_locks WHERE username = ? AND ip = ?",
            (normalize_username(username), ip),
        )
        conn.commit()


def failed_count_in_window(username: str, ip: str) -> int:
    window_start = datetime.utcnow() - timedelta(minutes=FAILED_WINDOW_MIN)
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM auth_attempts
            WHERE username = ?
              AND ip = ?
              AND success = 0
              AND ts >= ?
            """,
            (normalize_username(username), ip, window_start.isoformat(timespec="seconds")),
        ).fetchone()
    return int(row["cnt"]) if row else 0


# ---------------------------
# Auth decorators
# ---------------------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


# ---------------------------
# Safe path helpers
# ---------------------------
def safe_path(rel_path: str) -> str:
    """
    Преобразует относительный путь (внутри STORAGE) в абсолютный.
    Запрещает выход за пределы STORAGE.
    """
    rel_path = (rel_path or "").lstrip("/").strip()
    full = os.path.abspath(os.path.join(STORAGE, rel_path))
    storage_abs = os.path.abspath(STORAGE)

    if full == storage_abs or full.startswith(storage_abs + os.sep):
        return full
    raise ValueError("Invalid path")


def list_dir(rel_path: str):
    folder = safe_path(rel_path)
    if not os.path.exists(folder):
        return []
    if not os.path.isdir(folder):
        raise ValueError("Not a directory")

    items = []
    for name in os.listdir(folder):
        full = os.path.join(folder, name)
        is_dir = os.path.isdir(full)
        # формируем относительный путь в стиле "a/b"
        item_rel = os.path.relpath(full, STORAGE).replace("\\", "/")
        items.append({"name": name, "path": item_rel, "is_dir": is_dir})

    # сортировка: папки сверху, затем файлы; по имени
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return items


# ---------------------------
# Routes: UI
# ---------------------------
@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    init_db()

    if request.method == "POST":
        username = normalize_username(request.form.get("login"))
        password = request.form.get("password") or ""
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        ip = ip.split(",")[0].strip()

        # Проверяем блокировку
        locked_until = get_lock_until(username, ip)
        if locked_until and locked_until > datetime.utcnow():
            remaining = int((locked_until - datetime.utcnow()).total_seconds() // 60) + 1
            return render_template(
                "login.html",
                error=f"Слишком много попыток. Попробуйте снова через ~{remaining} мин.",
            )
        elif locked_until and locked_until <= datetime.utcnow():
            clear_lock(username, ip)

        user = find_user(username)
        ok = bool(user) and check_password_hash(user["password_hash"], password)

        record_attempt(username, ip, ok)

        if ok:
            session["auth"] = True
            session["user"] = username
            # успешный вход — снимаем блокировку, если была
            clear_lock(username, ip)
            return redirect(url_for("index"))

        # Неудача: считаем попытки и при необходимости блокируем
        fails = failed_count_in_window(username, ip)
        if fails >= MAX_FAILED_ATTEMPTS:
            until_dt = datetime.utcnow() + timedelta(minutes=LOCKOUT_MIN)
            set_lock(username, ip, until_dt)
            return render_template(
                "login.html",
                error=f"Слишком много попыток. Аккаунт временно заблокирован на {LOCKOUT_MIN} мин.",
            )

        left = max(0, MAX_FAILED_ATTEMPTS - fails)
        return render_template("login.html", error=f"Неверный логин или пароль. Осталось попыток: {left}")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    init_db()

    if request.method == "POST":
        username = normalize_username(request.form.get("login"))
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""

        if password != password2:
            return render_template("register.html", error="Пароли не совпадают")

        try:
            register_user(username, password)
        except sqlite3.IntegrityError:
            return render_template("register.html", error="Такой логин уже занят")
        except ValueError as e:
            return render_template("register.html", error=str(e))

        # после регистрации — сразу логиним
        session["auth"] = True
        session["user"] = username
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------
# Routes: API
# ---------------------------
@app.route("/api/list")
@login_required
def api_list():
    rel = request.args.get("path", "") or ""
    try:
        items = list_dir(rel)
        return jsonify(items)
    except ValueError:
        return jsonify(error="bad_path"), 400


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    rel = request.args.get("path", "") or ""
    try:
        folder = safe_path(rel)
    except ValueError:
        return jsonify(error="bad_path"), 400

    if not os.path.isdir(folder):
        return jsonify(error="not_a_folder"), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify(error="no_files"), 400

    saved = 0
    for f in files:
        if not f or not f.filename:
            continue
        filename = secure_filename(f.filename)
        if not filename:
            continue
        dest = os.path.join(folder, filename)
        f.save(dest)
        saved += 1

    return jsonify(ok=True, saved=saved)


@app.route("/api/download")
@login_required
def download():
    rel = request.args.get("path", "") or ""
    try:
        full = safe_path(rel)
    except ValueError:
        return jsonify(error="bad_path"), 400

    if not os.path.isfile(full):
        return jsonify(error="not_a_file"), 404

    folder = os.path.dirname(full)
    filename = os.path.basename(full)
    return send_from_directory(folder, filename, as_attachment=True)


@app.route("/api/create-folder", methods=["POST"])
@login_required
def create_folder():
    data = request.get_json(force=True, silent=True) or {}
    rel = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()

    if not name or name in (".", ".."):
        return jsonify(error="bad_name"), 400
    if any(sep in name for sep in ("/", "\\")) or ".." in name:
        return jsonify(error="bad_name"), 400

    try:
        parent = safe_path(rel)
    except ValueError:
        return jsonify(error="bad_path"), 400

    if not os.path.isdir(parent):
        return jsonify(error="not_a_folder"), 400

    new_dir = os.path.join(parent, name)
    os.makedirs(new_dir, exist_ok=True)
    return jsonify(ok=True)


@app.route("/api/delete", methods=["POST"])
@login_required
def delete():
    data = request.get_json(force=True, silent=True) or {}
    rel = (data.get("path") or "").strip()

    # запрещаем удалять корень storage
    if rel in ("", "/", ".", "./"):
        return jsonify(error="cannot_delete_root"), 400

    try:
        full = safe_path(rel)
    except ValueError:
        return jsonify(error="bad_path"), 400

    if not os.path.exists(full):
        return jsonify(error="not_found"), 404

    if os.path.isdir(full):
        shutil.rmtree(full)
    else:
        os.remove(full)

    return jsonify(ok=True)


# ---------------------------
# Routes: extra (rename/search)
# ---------------------------

@app.route("/api/rename", methods=["POST"])
@login_required
def api_rename():
    data = request.get_json(force=True, silent=True) or {}
    rel = (data.get("path") or "").strip()
    new_name = (data.get("name") or "").strip()

    # нельзя переименовывать пустой путь/корень
    if not rel or rel in ("/", ".", "./"):
        return jsonify(error="bad_path"), 400

    # базовая валидация имени (без слэшей и выходов наверх)
    if not new_name or new_name in (".", ".."):
        return jsonify(error="bad_name"), 400
    if any(sep in new_name for sep in ("/", "\\")) or ".." in new_name:
        return jsonify(error="bad_name"), 400

    try:
        full = safe_path(rel)
    except ValueError:
        return jsonify(error="bad_path"), 400

    if not os.path.exists(full):
        return jsonify(error="not_found"), 404

    parent = os.path.dirname(full)
    dest = os.path.join(parent, new_name)

    if os.path.exists(dest):
        return jsonify(error="already_exists"), 409

    try:
        os.rename(full, dest)
    except OSError:
        return jsonify(error="rename_failed"), 500

    return jsonify(ok=True)


@app.route("/api/search")
@login_required
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])

    q_norm = q.casefold()
    try:
        limit = int(request.args.get("limit") or "200")
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 2000))

    results = []
    storage_abs = os.path.abspath(STORAGE)

    for root, dirs, files in os.walk(storage_abs):
        # папки
        for d in dirs:
            if len(results) >= limit:
                break
            if q_norm in d.casefold():
                full = os.path.join(root, d)
                rel = os.path.relpath(full, STORAGE).replace("\\", "/")
                results.append({"name": d, "path": rel, "is_dir": True})
        if len(results) >= limit:
            break

        # файлы
        for f in files:
            if len(results) >= limit:
                break
            if q_norm in f.casefold():
                full = os.path.join(root, f)
                rel = os.path.relpath(full, STORAGE).replace("\\", "/")
                results.append({"name": f, "path": rel, "is_dir": False})
        if len(results) >= limit:
            break

    results.sort(key=lambda x: (not x["is_dir"], x["path"].lower()))
    return jsonify(results)


if __name__ == "__main__":
    # В проде лучше запускать через gunicorn/uwsgi и debug выключить
    app.run(host="0.0.0.0", port=5000, debug=False)
