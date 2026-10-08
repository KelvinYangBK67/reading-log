import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def ensure_flask():
    check = subprocess.run(
        [sys.executable, "-c", "import flask"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode == 0:
        return True

    print("首次執行：正在安裝所需套件……", flush=True)
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        cwd=BASE_DIR,
    )
    if install.returncode != 0:
        print("套件安裝失敗，請檢查網路連線後再試一次。")
        return False
    return True


def find_available_port():
    for port in range(5000, 5011):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return None


def open_browser_when_ready(port):
    for _attempt in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                webbrowser.open(f"http://127.0.0.1:{port}")
                return
        except OSError:
            time.sleep(0.1)


def main():
    if not ensure_flask():
        return 1

    port = find_available_port()
    if port is None:
        print("連接埠 5000 到 5010 都正在使用，無法啟動閱讀應用程式。")
        return 1

    if port == 5000:
        print("連接埠 5000 可用。")
    else:
        print(f"連接埠 5000 已被其他程式使用，將改用 {port}。")

    url = f"http://127.0.0.1:{port}"
    print(f"閱讀應用程式：{url}")
    print("若要停止，請在此視窗按 Ctrl+C。\n", flush=True)

    if not os.environ.get("READING_SKIP_BROWSER"):
        threading.Thread(
            target=open_browser_when_ready, args=(port,), daemon=True
        ).start()

    environment = os.environ.copy()
    environment["READING_PORT"] = str(port)
    try:
        subprocess.run([sys.executable, "app.py"], cwd=BASE_DIR, env=environment)
    except KeyboardInterrupt:
        pass

    print("\n閱讀應用程式已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
