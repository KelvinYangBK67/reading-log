import os
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from collections import OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)


BASE_DIR = Path(__file__).resolve().parent
VALID_STATUSES = {"planned", "reading", "completed"}


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("READING_DATABASE", str(BASE_DIR / "reading.db")),
        SECRET_KEY=os.environ.get("READING_SECRET_KEY", "local-reading-register"),
        ATTACHMENT_ROOT=os.environ.get("READING_ATTACHMENTS", str(BASE_DIR / "data" / "attachments")),
        CSRF_TOKEN=secrets.token_urlsafe(32),
        MAX_CONTENT_LENGTH=200 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    @app.context_processor
    def inject_csrf():
        return {"csrf_token": app.config["CSRF_TOKEN"]}

    @app.before_request
    def check_csrf():
        if request.method == "POST" and not app.config.get("TESTING"):
            token = request.form.get("csrf_token") or request.headers.get("X-Reading-Token")
            if not token or not secrets.compare_digest(token, app.config["CSRF_TOKEN"]):
                abort(403)

    @app.teardown_appcontext
    def close_database(_exception=None):
        database = g.pop("database", None)
        if database is not None:
            database.close()

    with app.app_context():
        initialize_database()

    @app.get("/")
    def index():
        database = get_database()
        reading = database.execute(
            """
            SELECT * FROM books
            WHERE status = 'reading'
            ORDER BY start_date IS NULL, start_date DESC, created_at DESC, id DESC
            """
        ).fetchall()
        planned = database.execute(
            """
            SELECT * FROM books
            WHERE status = 'planned'
            ORDER BY sort_order IS NULL, sort_order, created_at DESC, id DESC
            """
        ).fetchall()
        completed = database.execute(
            """
            SELECT * FROM books
            WHERE status = 'completed'
            ORDER BY finish_date IS NULL, finish_date DESC, start_date IS NULL, start_date DESC, id DESC
            """
        ).fetchall()

        history_groups = OrderedDict()
        for book in completed:
            year = book["finish_date"][:4] if book["finish_date"] else "未標示年份"
            history_groups.setdefault(year, []).append(book)

        chosen = request.args.get("book", type=int)
        selected = get_book(chosen) if chosen is not None else None
        if selected is None:
            selected = next(iter(reading or planned or completed), None)
        return render_template(
            "index.html", reading=reading, planned=planned,
            history_groups=history_groups, selected=selected,
            attachments=attachments_for(selected["id"]) if selected else [],
        )

    def attachments_for(book_id):
        return get_database().execute(
            "SELECT * FROM attachments WHERE book_id = ? ORDER BY id", (book_id,)
        ).fetchall()

    @app.get("/books/<int:book_id>/detail")
    def book_detail(book_id):
        return render_template(
            "_detail.html", book=get_book(book_id), attachments=attachments_for(book_id)
        )

    @app.route("/books/new", methods=("GET", "POST"))
    def add_book():
        values = empty_book()
        if request.method == "POST":
            values = book_values_from_form()
            errors = validate_book(values)
            if not errors:
                database = get_database()
                cursor = database.execute(
                    """
                    INSERT INTO books
                        (title, author, edition, status, start_date, finish_date, record, created_at, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        values["title"],
                        values["author"],
                        values["edition"],
                        values["status"],
                        values["start_date"],
                        values["finish_date"],
                        values["record"],
                        datetime.now(timezone.utc).isoformat(),
                        next_planned_order(database) if values["status"] == "planned" else None,
                    ),
                )
                database.commit()
                flash("書籍已新增。", "success")
                return redirect(url_for("index", book=cursor.lastrowid))
            for error in errors:
                flash(error, "error")
        return render_template("form.html", page_title="新增書籍", book=values)

    @app.route("/books/<int:book_id>/edit", methods=("GET", "POST"))
    def edit_book(book_id):
        book = get_book(book_id)
        values = dict(book)
        if request.method == "POST":
            values = book_values_from_form()
            errors = validate_book(values)
            if not errors:
                database = get_database()
                database.execute(
                    """
                    UPDATE books
                    SET title = ?, author = ?, edition = ?, status = ?,
                        start_date = ?, finish_date = ?, record = ?, sort_order = ?
                    WHERE id = ?
                    """,
                    (
                        values["title"],
                        values["author"],
                        values["edition"],
                        values["status"],
                        values["start_date"],
                        values["finish_date"],
                        values["record"],
                        (book["sort_order"] if book["status"] == "planned" else next_planned_order(database)) if values["status"] == "planned" else None,
                        book_id,
                    ),
                )
                database.commit()
                flash("書籍已更新。", "success")
                return redirect(url_for("index", book=book_id))
            for error in errors:
                flash(error, "error")
        return render_template("form.html", page_title="編輯書籍", book=values)

    @app.route("/books/<int:book_id>/start", methods=("GET", "POST"))
    def start_book(book_id):
        book = get_book(book_id)
        suggested_date = book["start_date"] or date.today().isoformat()
        if request.method == "POST":
            start_date = cleaned_value("start_date")
            error = validate_date(start_date)
            if error:
                flash(error, "error")
                suggested_date = start_date
            else:
                database = get_database()
                database.execute(
                    "UPDATE books SET status = 'reading', start_date = ?, finish_date = NULL, sort_order = NULL WHERE id = ?",
                    (start_date, book_id),
                )
                database.commit()
                flash("已開始閱讀。", "success")
                return redirect(url_for("index", book=book_id))
        return render_template(
            "transition.html",
            page_title="開始閱讀",
            book=book,
            field_name="start_date",
            field_label="開始日期",
            field_value=suggested_date,
            submit_label="開始閱讀",
        )

    @app.route("/books/<int:book_id>/complete", methods=("GET", "POST"))
    def complete_book(book_id):
        book = get_book(book_id)
        suggested_date = book["finish_date"] or date.today().isoformat()
        record = book["record"] or ""
        if request.method == "POST":
            finish_date = cleaned_value("finish_date")
            record = request.form.get("record", "")
            error = validate_date(finish_date)
            if not error and book["start_date"] and finish_date and finish_date < book["start_date"]:
                error = "完成日期不能早於開始日期。"
            if error:
                flash(error, "error")
                suggested_date = finish_date
            else:
                database = get_database()
                database.execute(
                    """
                    UPDATE books
                    SET status = 'completed', finish_date = ?, record = ?, sort_order = NULL
                    WHERE id = ?
                    """,
                    (finish_date, record, book_id),
                )
                database.commit()
                flash("已完成閱讀。", "success")
                return redirect(url_for("index", book=book_id))
        return render_template(
            "transition.html",
            page_title="完成閱讀",
            book=book,
            field_name="finish_date",
            field_label="完成日期",
            field_value=suggested_date,
            submit_label="完成閱讀",
            record_value=record,
        )


    @app.post("/books/<int:book_id>/record")
    def update_record(book_id):
        get_book(book_id)
        database = get_database()
        database.execute(
            "UPDATE books SET record = ? WHERE id = ?",
            (request.form.get("record", ""), book_id),
        )
        database.commit()
        flash("閱讀記錄已儲存。", "success")
        return redirect(url_for("index", book=book_id))

    @app.post("/books/reorder")
    def reorder_books():
        payload = request.get_json(silent=True)
        ids = payload.get("ids") if isinstance(payload, dict) else None
        if not isinstance(ids, list) or not all(type(value) is int for value in ids):
            abort(400)
        database = get_database()
        current_ids = {row["id"] for row in database.execute("SELECT id FROM books WHERE status = 'planned'")}
        if len(ids) != len(current_ids) or set(ids) != current_ids:
            abort(400)
        database.executemany(
            "UPDATE books SET sort_order = ? WHERE id = ?",
            [(position, book_id) for position, book_id in enumerate(ids)],
        )
        database.commit()
        return {"ok": True}

    @app.post("/books/<int:book_id>/attachments")
    def add_attachment(book_id):
        get_book(book_id)
        uploaded = request.files.get("pdf")
        if not uploaded or not uploaded.filename:
            flash("請選擇 PDF 檔案。", "error")
            return redirect(url_for("index", book=book_id))
        filename = uploaded.filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        header = uploaded.stream.read(1024)
        uploaded.stream.seek(0)
        if not filename.lower().endswith(".pdf") or b"%PDF-" not in header:
            flash("僅接受有效的 PDF 檔案。", "error")
            return redirect(url_for("index", book=book_id))
        stored_name = uuid4().hex + ".pdf"
        folder = attachment_folder(book_id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / stored_name
        try:
            uploaded.save(target)
            database = get_database()
            database.execute(
                "INSERT INTO attachments (book_id, filename, stored_name, created_at) VALUES (?, ?, ?, ?)",
                (book_id, filename, stored_name, datetime.now(timezone.utc).isoformat()),
            )
            database.commit()
        except Exception:
            target.unlink(missing_ok=True)
            raise
        flash("PDF 已附加。", "success")
        return redirect(url_for("index", book=book_id))

    @app.get("/books/<int:book_id>/attachments/<int:attachment_id>")
    def open_attachment(book_id, attachment_id):
        attachment = get_attachment(book_id, attachment_id)
        return send_from_directory(
            attachment_folder(book_id), attachment["stored_name"],
            mimetype="application/pdf", as_attachment=False,
            download_name=attachment["filename"],
        )

    @app.post("/books/<int:book_id>/attachments/<int:attachment_id>/delete")
    def delete_attachment(book_id, attachment_id):
        attachment = get_attachment(book_id, attachment_id)
        database = get_database()
        database.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        database.commit()
        (attachment_folder(book_id) / attachment["stored_name"]).unlink(missing_ok=True)
        flash("附件已移除。", "success")
        return redirect(url_for("index", book=book_id))

    @app.get("/backup")
    def backup():
        output = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as temporary:
            database_copy = Path(temporary) / "reading.db"
            with sqlite3.connect(database_copy) as destination:
                get_database().backup(destination)
            with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
                archive.write(database_copy, "reading.db")
                root = Path(app.config["ATTACHMENT_ROOT"])
                if root.is_dir():
                    for item in root.rglob("*.pdf"):
                        if item.is_file():
                            archive.write(item, (Path("data") / "attachments" / item.relative_to(root)).as_posix())
        output.seek(0)
        return send_file(
            output, mimetype="application/zip", as_attachment=True,
            download_name=f"reading-backup-{date.today().isoformat()}.zip",
        )

    @app.post("/books/<int:book_id>/delete")
    def delete_book(book_id):
        get_book(book_id)
        database = get_database()
        database.execute("DELETE FROM attachments WHERE book_id = ?", (book_id,))
        database.execute("DELETE FROM books WHERE id = ?", (book_id,))
        database.commit()
        shutil.rmtree(attachment_folder(book_id), ignore_errors=True)
        flash("書籍已刪除。", "success")
        return redirect(url_for("index"))

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("not_found.html"), 404

    @app.errorhandler(413)
    def file_too_large(_error):
        return "PDF 檔案過大（上限 200 MB）。", 413

    return app


def attachment_folder(book_id):
    return Path(current_app.config["ATTACHMENT_ROOT"]) / str(book_id)


def get_attachment(book_id, attachment_id):
    attachment = get_database().execute(
        "SELECT * FROM attachments WHERE id = ? AND book_id = ?",
        (attachment_id, book_id),
    ).fetchone()
    if attachment is None:
        abort(404)
    return attachment


def get_database():
    if "database" not in g:
        database_path = Path(current_app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        g.database = sqlite3.connect(database_path)
        g.database.row_factory = sqlite3.Row
        g.database.execute("PRAGMA foreign_keys = ON")
    return g.database


def initialize_database():
    database = get_database()
    database.execute(
        """
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            edition TEXT,
            status TEXT NOT NULL CHECK (status IN ('planned', 'reading', 'completed')),
            start_date TEXT,
            finish_date TEXT,
            record TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            sort_order INTEGER
        )
        """
    )
    columns = {
        column["name"] for column in database.execute("PRAGMA table_info(books)")
    }
    if "record" not in columns:
        database.execute(
            "ALTER TABLE books ADD COLUMN record TEXT NOT NULL DEFAULT ''"
        )
    if "sort_order" not in columns:
        database.execute("ALTER TABLE books ADD COLUMN sort_order INTEGER")
    database.execute(
        """CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            stored_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )"""
    )
    existing = database.execute(
        """SELECT id FROM books WHERE status = 'planned' AND sort_order IS NULL
           ORDER BY created_at DESC, id DESC"""
    ).fetchall()
    order = next_planned_order(database)
    for row in existing:
        database.execute("UPDATE books SET sort_order = ? WHERE id = ?", (order, row["id"]))
        order += 1
    database.commit()


def next_planned_order(database):
    return database.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM books WHERE status = 'planned'"
    ).fetchone()[0]


def get_book(book_id):
    book = get_database().execute(
        "SELECT * FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    if book is None:
        abort(404)
    return book


def empty_book():
    return {
        "title": "",
        "author": "",
        "edition": "",
        "status": "planned",
        "start_date": "",
        "finish_date": "",
        "record": "",
    }


def cleaned_value(name):
    return request.form.get(name, "").strip()


def book_values_from_form():
    values = {
        "title": cleaned_value("title"),
        "author": cleaned_value("author"),
        "edition": cleaned_value("edition"),
        "status": cleaned_value("status"),
        "start_date": cleaned_value("start_date"),
        "finish_date": cleaned_value("finish_date"),
        "record": request.form.get("record", ""),
    }
    if values["status"] == "planned":
        values["start_date"] = ""
        values["finish_date"] = ""
    elif values["status"] == "reading":
        values["finish_date"] = ""
    return values


def validate_book(values):
    errors = []
    if not values["title"]:
        errors.append("請輸入書名。")
    if values["status"] not in VALID_STATUSES:
        errors.append("請選擇有效的狀態。")
    for value in (values["start_date"], values["finish_date"]):
        error = validate_date(value)
        if error and error not in errors:
            errors.append(error)
    if values["start_date"] and values["finish_date"] and not errors:
        if values["finish_date"] < values["start_date"]:
            errors.append("完成日期不能早於開始日期。")
    return errors


def validate_date(value):
    if not value:
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return "日期格式必須為 YYYY-MM-DD。"
    return None


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("READING_PORT", "5000"))
    app.run(host="127.0.0.1", port=port)
