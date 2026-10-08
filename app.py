import os
import sqlite3
from collections import OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)


BASE_DIR = Path(__file__).resolve().parent
VALID_STATUSES = {"planned", "reading", "completed"}


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("READING_DATABASE", str(BASE_DIR / "reading.db")),
        SECRET_KEY=os.environ.get("READING_SECRET_KEY", "local-reading-register"),
    )
    if test_config:
        app.config.update(test_config)

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
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
        completed = database.execute(
            """
            SELECT * FROM books
            WHERE status = 'completed'
            ORDER BY finish_date IS NULL, finish_date DESC, created_at DESC, id DESC
            """
        ).fetchall()

        history_groups = OrderedDict()
        for book in completed:
            year = book["finish_date"][:4] if book["finish_date"] else "未標示年份"
            history_groups.setdefault(year, []).append(book)

        return render_template(
            "index.html",
            reading=reading,
            planned=planned,
            history_groups=history_groups,
        )

    @app.route("/books/new", methods=("GET", "POST"))
    def add_book():
        values = empty_book()
        if request.method == "POST":
            values = book_values_from_form()
            errors = validate_book(values)
            if not errors:
                database = get_database()
                database.execute(
                    """
                    INSERT INTO books
                        (title, author, edition, status, start_date, finish_date, record, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                database.commit()
                flash("書籍已新增。", "success")
                return redirect(url_for("index"))
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
                        start_date = ?, finish_date = ?, record = ?
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
                        book_id,
                    ),
                )
                database.commit()
                flash("書籍已更新。", "success")
                return redirect(url_for("index"))
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
                    "UPDATE books SET status = 'reading', start_date = ? WHERE id = ?",
                    (start_date, book_id),
                )
                database.commit()
                flash("已開始閱讀。", "success")
                return redirect(url_for("index"))
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
            record = cleaned_value("record")
            error = validate_date(finish_date)
            if error:
                flash(error, "error")
                suggested_date = finish_date
            else:
                database = get_database()
                database.execute(
                    """
                    UPDATE books
                    SET status = 'completed', finish_date = ?, record = ?
                    WHERE id = ?
                    """,
                    (finish_date, record, book_id),
                )
                database.commit()
                flash("已完成閱讀。", "success")
                return redirect(url_for("index"))
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

    @app.post("/books/<int:book_id>/delete")
    def delete_book(book_id):
        get_book(book_id)
        database = get_database()
        database.execute("DELETE FROM books WHERE id = ?", (book_id,))
        database.commit()
        flash("書籍已刪除。", "success")
        return redirect(url_for("index"))

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("not_found.html"), 404

    return app


def get_database():
    if "database" not in g:
        database_path = Path(current_app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        g.database = sqlite3.connect(database_path)
        g.database.row_factory = sqlite3.Row
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
            created_at TEXT NOT NULL
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
    database.commit()


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
    return {
        "title": cleaned_value("title"),
        "author": cleaned_value("author"),
        "edition": cleaned_value("edition"),
        "status": cleaned_value("status"),
        "start_date": cleaned_value("start_date"),
        "finish_date": cleaned_value("finish_date"),
        "record": cleaned_value("record"),
    }


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
