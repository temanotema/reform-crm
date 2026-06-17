"""
updater.py — проверка и установка обновлений через GitHub Releases.

Как это работает:
  • GitHub Actions при пуше тега vX.Y.Z собирает установщик .exe и публикует
    его в «Releases» репозитория (config.GITHUB_REPO).
  • Приложение спрашивает у GitHub последний релиз, сравнивает версию с текущей
    (version.__version__). Если новее — в панели появляется кнопка «Обновить».
  • По кнопке: скачиваем установщик во временную папку, запускаем его и
    закрываем приложение, чтобы установщик заменил файлы.

Никаких токенов не нужно — публичные релизы читаются анонимно.
"""

import os
import sys
import tempfile
import threading
import subprocess

import requests

from version import __version__ as CURRENT_VERSION

try:
    from config import GITHUB_REPO
except Exception:
    GITHUB_REPO = "OWNER/reform-crm"

_API = "https://api.github.com/repos/{repo}/releases/latest"


def _to_tuple(v: str):
    v = (v or "").strip().lstrip("vV")
    parts = []
    for chunk in v.split("."):
        num = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def check_for_update(timeout=8):
    """
    Возвращает словарь:
      {"available": bool, "current": "x.y.z", "latest": "x.y.z",
       "url": <ссылка на .exe или None>, "notes": <описание релиза>}
    При любой ошибке/оффлайне — available=False (приложение не падает).
    """
    result = {"available": False, "current": CURRENT_VERSION,
              "latest": CURRENT_VERSION, "url": None, "notes": ""}
    if "OWNER/" in GITHUB_REPO:
        return result  # репозиторий ещё не настроен
    try:
        r = requests.get(_API.format(repo=GITHUB_REPO), timeout=timeout,
                         headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return result
        data = r.json()
        latest = (data.get("tag_name") or "").lstrip("vV")
        if not latest:
            return result
        result["latest"] = latest
        result["notes"] = (data.get("body") or "")[:500]
        # ищем установщик .exe среди вложений релиза
        for asset in data.get("assets", []):
            name = (asset.get("name") or "").lower()
            if name.endswith(".exe"):
                result["url"] = asset.get("browser_download_url")
                break
        result["available"] = _to_tuple(latest) > _to_tuple(CURRENT_VERSION) and bool(result["url"])
    except Exception:
        pass
    return result


def download_and_run(url, on_done=None):
    """
    Скачивает установщик и запускает его, затем закрывает приложение.
    Возвращает True, если запуск начат.
    """
    if not url:
        return False
    try:
        dest = os.path.join(tempfile.gettempdir(), "ReformCRM-Setup.exe")
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        # Запускаем установщик и выходим из приложения, чтобы файлы заменились.
        if sys.platform.startswith("win"):
            os.startfile(dest)  # noqa
        else:
            subprocess.Popen([dest])
        if on_done:
            try:
                on_done()
            except Exception:
                pass
        # даём установщику стартовать и закрываемся
        threading.Timer(1.5, lambda: os._exit(0)).start()
        return True
    except Exception:
        return False
