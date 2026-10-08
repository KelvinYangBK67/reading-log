import io
import zipfile
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app


class ReadingRegisterTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(self.database_path),
                "ATTACHMENT_ROOT": str(Path(self.temporary_directory.name) / "attachments"),
                "SECRET_KEY": "test",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def rows(self):
        with closing(sqlite3.connect(self.database_path)) as database:
            database.row_factory = sqlite3.Row
            return database.execute("SELECT * FROM books ORDER BY id").fetchall()

    def add_book(
        self, title, status="planned", start_date="", finish_date="", record=""
    ):
        return self.client.post(
            "/books/new",
            data={
                "title": title,
                "author": "",
                "edition": "",
                "status": status,
                "start_date": start_date,
                "finish_date": finish_date,
                "record": record,
            },
            follow_redirects=True,
        )

    def test_complete_book_lifecycle(self):
        response = self.add_book("小王子")
        self.assertEqual(response.status_code, 200)
        self.assertIn("小王子", response.get_data(as_text=True))
        self.assertNotIn("None", response.get_data(as_text=True))

        book_id = self.rows()[0]["id"]
        self.client.post(
            f"/books/{book_id}/start",
            data={"start_date": "2026-09-20"},
            follow_redirects=True,
        )
        self.assertEqual(self.rows()[0]["status"], "reading")

        self.client.post(
            f"/books/{book_id}/complete",
            data={"finish_date": "2026-09-25", "record": "很安靜，也很有餘韻。"},
            follow_redirects=True,
        )
        self.assertEqual(self.rows()[0]["status"], "completed")
        self.assertEqual(self.rows()[0]["record"], "很安靜，也很有餘韻。")

        self.client.post(
            f"/books/{book_id}/edit",
            data={
                "title": "小王子（修訂）",
                "author": "聖修伯里",
                "edition": "平裝版",
                "status": "completed",
                "start_date": "2026-09-20",
                "finish_date": "2026-09-25",
                "record": "值得重讀。",
            },
            follow_redirects=True,
        )
        edited = self.rows()[0]
        self.assertEqual(edited["title"], "小王子（修訂）")
        self.assertEqual(edited["author"], "聖修伯里")
        self.assertEqual(edited["record"], "值得重讀。")

        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("閱讀記錄", page)
        self.assertIn("值得重讀。", page)

        self.client.post(f"/books/{book_id}/delete", follow_redirects=True)
        self.assertEqual(self.rows(), [])

    def test_history_sorting_year_groups_and_empty_fields(self):
        self.add_book("較早完成", "completed", "", "2025-12-31")
        self.add_book("本年較早", "completed", "2026-01-01", "2026-01-04")
        self.add_book("本年較新", "completed", "2026-09-20", "2026-09-25")
        self.add_book("沒有日期", "completed")

        page = self.client.get("/").get_data(as_text=True)
        self.assertLess(page.index("本年較新"), page.index("本年較早"))
        self.assertLess(page.index("本年較早"), page.index("較早完成"))
        self.assertLess(page.index("較早完成"), page.index("沒有日期"))
        self.assertIn(">2026<", page)
        self.assertIn(">2025<", page)
        self.assertIn("未標示年份", page)
        self.assertNotIn("None", page)

    def test_validation_is_shown_in_traditional_chinese(self):
        response = self.add_book("")
        self.assertIn("請輸入書名。", response.get_data(as_text=True))
        self.assertEqual(self.rows(), [])

    def test_existing_database_is_upgraded_without_losing_books(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.db"
            with closing(sqlite3.connect(database_path)) as database:
                database.executescript(
                    """
                    CREATE TABLE books (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        author TEXT,
                        edition TEXT,
                        status TEXT NOT NULL,
                        start_date TEXT,
                        finish_date TEXT,
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO books
                        (title, status, created_at)
                    VALUES ('舊資料', 'planned', '2026-01-01T00:00:00+00:00');
                    """
                )

            upgraded_app = create_app(
                {"TESTING": True, "DATABASE": str(database_path), "SECRET_KEY": "test"}
            )
            with upgraded_app.app_context():
                with closing(sqlite3.connect(database_path)) as database:
                    database.row_factory = sqlite3.Row
                    row = database.execute("SELECT * FROM books").fetchone()
                    columns = {
                        column["name"]
                        for column in database.execute("PRAGMA table_info(books)")
                    }

            self.assertIn("record", columns)
            self.assertEqual(row["title"], "舊資料")
            self.assertEqual(row["record"], "")


    def test_planned_order_and_validation(self):
        self.add_book("First")
        self.add_book("Second")
        ids = [row["id"] for row in self.rows()]
        initial = self.client.get("/").get_data(as_text=True)
        self.assertLess(initial.index("First"), initial.index("Second"))
        response = self.client.post("/books/reorder", json={"ids": ids[::-1]})
        self.assertEqual(response.status_code, 200)
        updated = self.client.get("/").get_data(as_text=True)
        self.assertLess(updated.index("Second"), updated.index("First"))
        self.assertEqual(
            self.client.post("/books/reorder", json={"ids": [999]}).status_code, 400
        )

    def test_record_editing_during_reading(self):
        self.add_book("Notes", "reading", start_date="2026-10-01")
        book_id = self.rows()[0]["id"]
        self.client.post(
            f"/books/{book_id}/record", data={"record": "Chapter 1\nChapter 2"}
        )
        self.assertEqual(self.rows()[0]["record"], "Chapter 1\nChapter 2")
        self.assertIn(
            "Chapter 2",
            self.client.get(f"/books/{book_id}/detail").get_data(as_text=True),
        )

    def test_pdf_upload_open_delete_and_backup(self):
        self.add_book("PDF example")
        book_id = self.rows()[0]["id"]
        uploaded = self.client.post(
            f"/books/{book_id}/attachments",
            data={"pdf": (io.BytesIO(b"%PDF-1.7\nsample\n%%EOF"), "reading.pdf")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("reading.pdf", uploaded.get_data(as_text=True))
        with sqlite3.connect(self.database_path) as database:
            attachment_id = database.execute("SELECT id FROM attachments").fetchone()[0]
        opened = self.client.get(f"/books/{book_id}/attachments/{attachment_id}")
        self.assertEqual(opened.status_code, 200)
        self.assertTrue(opened.data.startswith(b"%PDF-"))
        backup = self.client.get("/backup")
        self.assertEqual(backup.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(backup.data)) as archive:
            self.assertIn("reading.db", archive.namelist())
            self.assertTrue(any(name.endswith(".pdf") for name in archive.namelist()))
        invalid = self.client.post(
            f"/books/{book_id}/attachments",
            data={"pdf": (io.BytesIO(b"invalid"), "fake.pdf")},
            content_type="multipart/form-data", follow_redirects=True,
        )
        self.assertIn("僅接受有效的 PDF", invalid.get_data(as_text=True))
        self.client.post(f"/books/{book_id}/attachments/{attachment_id}/delete")
        self.assertEqual(
            self.client.get(f"/books/{book_id}/attachments/{attachment_id}").status_code,
            404,
        )

    def test_dates_when_moving_status(self):
        self.add_book("Old", "completed", "2026-01-01", "2026-01-02")
        book_id = self.rows()[0]["id"]
        self.client.post(f"/books/{book_id}/start", data={"start_date": "2026-10-08"})
        row = self.rows()[0]
        self.assertEqual(row["status"], "reading")
        self.assertIsNone(row["finish_date"])
        invalid = self.client.post(
            f"/books/{book_id}/complete",
            data={"finish_date": "2026-01-01", "record": ""},
        )
        self.assertIn("完成日期不能早於開始日期", invalid.get_data(as_text=True))

    def test_old_planned_order_is_migrated(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = Path(folder) / "legacy.db"
            with sqlite3.connect(legacy) as db:
                db.executescript("""
                    CREATE TABLE books (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL, author TEXT, edition TEXT,
                        status TEXT NOT NULL, start_date TEXT, finish_date TEXT,
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO books (title,status,created_at)
                    VALUES ('first','planned','2026-01-01');
                    INSERT INTO books (title,status,created_at)
                    VALUES ('second','planned','2026-01-02');
                """)
            upgraded = create_app({"TESTING": True, "DATABASE": str(legacy)})
            html = upgraded.test_client().get("/").get_data(as_text=True)
            self.assertLess(html.index("second"), html.index("first"))
            with sqlite3.connect(legacy) as db:
                self.assertEqual(
                    [row[0] for row in db.execute("SELECT sort_order FROM books ORDER BY id")],
                    [1, 0],
                )


if __name__ == "__main__":
    unittest.main()
