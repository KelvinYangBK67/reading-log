# 閱讀

一個使用 Flask 與 SQLite 製作的本機個人閱讀記錄工具。

## 啟動

需要 Python 3.10 或更新版本。在 Windows 直接雙擊 run.bat。
也可以執行以下兩條命令：

    python -m pip install -r requirements.txt
    python app.py

run.bat 會在需要時安裝 Flask，尋找 5000–5010 之間可用的連接埠，
並自動開啟瀏覽器。停止時在啟動視窗按 Ctrl+C。

## 使用

左側依序列出「正在閱讀」、「準備閱讀」、「歷史閱讀」。
點擊書名可在右側查看書籍資訊、隨時編輯純文字閱讀記錄、附加或開啟 PDF。
準備閱讀可拖拽排序；歷史閱讀按完成日期由近至遠排列。
左右分隔線可拖動調整寬度，並記住此瀏覽器的設定。
搜尋只比對書名及作者；不需帳號或網路服務。

## 資料與備份

- reading.db：本機 SQLite 資料庫，首次啟動自動建立。
- data/attachments/：以書籍 ID 分目錄保存 PDF 附件。
- 右上角「備份」會下載包含資料庫快照和全部 PDF 附件的 ZIP。
- 手動備份時，請先停止程式，再一起複製 reading.db 和 data/ 目錄。
- 附件只接受 PDF，點擊後由瀏覽器開啟；不含內建閱讀器。

以上個人資料與 static/fonts/ 均被 .gitignore 排除。
字型是本機選用資源：原本的 Libertinus + 尙古字型，可繼續存放在
static/fonts/ 中，而不推送到 GitHub。若沒有相應字型檔，會由瀏覽器
使用系統內建字型替代。

字型目錄不在目前的遠端追蹤清單內。正常 pull 不會動到既有、
未追蹤的本機字型；如果你的本機 checkout 仍來自更早、曾追蹤字型
的 Git 歷史，請先在 repo 外備份 static/fonts/ 再處理歷史分歧。

## 測試

    python -m unittest discover -v
