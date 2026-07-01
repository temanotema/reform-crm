"""
admin_web.py — веб-панель Re.form CRM.
"""

import asyncio
import json
import os
import time
import uuid
import hmac
import requests as _requests
from datetime import date, timedelta
from flask import Flask, render_template_string, request, redirect, session, jsonify, Response, send_file
from werkzeug.utils import secure_filename

import config
import database as db
import yclients
from config import (
    ADMIN_PASSWORD, BOT_TOKEN, WEB_PORT, SECRET_KEY, CLINIC_NAME,
    YCLIENTS_COMPANY_ID, YCLIENTS_CLIENT_URL_TEMPLATE, TELEGRAM_PROXY,
    VAPID_PUBLIC_KEY,
)

# Прокси для запросов панели к Telegram (если api.telegram.org недоступен напрямую).
# socks5:// требует пакет PySocks (см. requirements.txt).
_TG_PROXIES = {"http": TELEGRAM_PROXY, "https": TELEGRAM_PROXY} if TELEGRAM_PROXY else None
from templates import get_all_templates_for_ui, PLACEHOLDER_KEYS, EMOJI_MAP

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ── Безопасность сессии и куки ────────────────────────────────────────────────
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,                 # куку нельзя прочитать из JS
    SESSION_COOKIE_SAMESITE="Lax",                # защита от CSRF (межсайтовых запросов)
    # На сервере с HTTPS задай COOKIE_SECURE=True в config_local.py:
    SESSION_COOKIE_SECURE=bool(getattr(config, "COOKIE_SECURE", False)),
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,          # лимит загрузки файла — 50 МБ
)


@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"     # не угадывать тип файла
    resp.headers["X-Frame-Options"] = "DENY"               # запрет встраивания в iframe
    resp.headers["Referrer-Policy"] = "no-referrer"
    if getattr(config, "COOKIE_SECURE", False):            # только при HTTPS
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.route("/assets/duende.ttf")
def _font_duende():
    from flask import send_from_directory
    resp = send_from_directory(BASE_DIR, "duende.ttf")
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.route("/assets/icons/<path:fname>")
def _pwa_icon(fname):
    from flask import send_from_directory
    return send_from_directory(os.path.join(BASE_DIR, "webicons"), fname)


@app.route("/manifest.webmanifest")
def _manifest():
    import json as _json
    data = {
        "name": CLINIC_NAME,
        "short_name": "Re.form",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#57101b",
        "theme_color": "#57101b",
        "icons": [
            {"src": "/assets/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/assets/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return Response(_json.dumps(data, ensure_ascii=False),
                    mimetype="application/manifest+json")


@app.route("/sw.js")
def _service_worker():
    # Service worker: ловит клик по системному уведомлению и открывает
    # нужный чат (/chats/<id>), фокусируя уже открытое окно приложения.
    js = """
self.addEventListener('install', function(e){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });
self.addEventListener('push', function(event){
  var d={}; try{ d = event.data ? event.data.json() : {}; }catch(e){}
  var title = d.title || 'Re.form CRM';
  var opts = {
    body: d.body || 'Новое сообщение',
    icon: '/assets/icons/icon-192.png',
    badge: '/assets/icons/icon-192.png',
    tag: 'crm-push',
    renotify: true,
    data: { url: d.url || '/chats' }
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});
self.addEventListener('notificationclick', function(event){
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || '/chats';
  event.waitUntil(
    self.clients.matchAll({type:'window', includeUncontrolled:true}).then(function(list){
      for(var i=0;i<list.length;i++){
        var c=list[i];
        if('focus' in c){
          if('navigate' in c){ try{ c.navigate(url); }catch(e){} }
          return c.focus();
        }
      }
      if(self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
"""
    resp = Response(js, mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/health")
def _health():
    # Лёгкая проверка «жив ли сервис» для мониторинга (Uptime Kuma и т.п.).
    db_ok = True
    try:
        db.fetchone("SELECT 1 AS ok")
    except Exception:
        db_ok = False
    return jsonify({"status": "ok" if db_ok else "degraded", "db": db_ok}), (200 if db_ok else 503)


# ── Шпаргалка ключей (плавающая панель справа сверху на страницах шаблонов) ────

_KEYS_SHELL = """
<div id="keysFab" class="keys-fab" onclick="ksToggle()"><i class="ti ti-key"></i> Ключи</div>
<div id="keysPanel" class="keys-panel">
  <div class="keys-hd">Подстановки <span class="keys-x" onclick="ksToggle()"><i class="ti ti-x"></i></span></div>
  <div class="keys-sub">Текст — нажми, чтобы скопировать</div>
  __ROWS__
  <div class="keys-sub">Премиум-эмодзи</div>
  __EROWS__
  <div class="keys-note">Работают во всех шаблонах. *звёздочки* делают <b>жирный</b> текст.</div>
</div>
<style>
.keys-fab{position:fixed;top:64px;right:18px;z-index:60;background:var(--blue,#2f7bf6);color:#fff;border-radius:20px;padding:7px 14px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:var(--shadow);user-select:none}
.keys-panel{position:fixed;top:104px;right:18px;z-index:60;width:300px;max-height:72vh;overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;box-shadow:var(--shadow);display:none}
.keys-panel.open{display:block}
.keys-hd{font-weight:700;font-size:14px;margin-bottom:6px}
.keys-x{cursor:pointer;float:right;color:var(--text-sec)}
.keys-sub{font-size:11px;color:var(--text-sec);text-transform:uppercase;letter-spacing:.04em;margin:10px 0 4px}
.ks-row{display:flex;flex-direction:column;gap:1px;padding:6px 8px;border-radius:7px;cursor:pointer}
.ks-row:hover{background:var(--hover)}
.ks-row code{font-size:12px;color:var(--text)}
.ks-row span{font-size:11px;color:var(--text-sec)}
.keys-note{font-size:11px;color:var(--text-sec);margin-top:12px;line-height:1.5;border-top:1px solid var(--border);padding-top:10px}
@media(max-width:767px){
  .keys-fab{top:auto;bottom:calc(84px + env(safe-area-inset-bottom));right:12px;left:auto}
  .keys-panel{left:10px;right:10px;width:auto;top:auto;bottom:calc(132px + env(safe-area-inset-bottom));max-height:55vh}
}
</style>
<script>
function ksToggle(){document.getElementById('keysPanel').classList.toggle('open');}
function ksCopy(el){var k=el.getAttribute('data-k');navigator.clipboard.writeText(k).then(function(){if(window.showToast)showToast('Скопировано: '+k,'ok');});}
</script>
"""


def _keys_cheatsheet_html():
    row = ('<div class="ks-row" onclick="ksCopy(this)" data-k="%s">'
           '<code>%s</code><span>%s</span></div>')
    rows = "".join(row % (ph, ph, desc) for ph, desc in PLACEHOLDER_KEYS)
    erows = ""
    for name, (cid, fb) in EMOJI_MAP.items():
        key = "{эмодзи:" + name + "}"
        erows += row % (key, key, fb + " премиум-эмодзи")
    return _KEYS_SHELL.replace("__ROWS__", rows).replace("__EROWS__", erows)


# Пикер премиум-эмодзи: кнопка справа сверху, грузит пак из /api/emoji_pack,
# клик по эмодзи вставляет {эмодзи:<id>} в последнее активное поле текста.
_EMOJI_PICKER_HTML = """
<div id="epFab" class="ep-fab" onclick="epOpen()"><i class="ti ti-mood-smile"></i> Эмодзи</div>
<div id="epPanel" class="ep-panel">
  <div class="ep-hd">Премиум-эмодзи <span class="ep-x" onclick="document.getElementById('epPanel').classList.remove('open')"><i class="ti ti-x"></i></span></div>
  <div class="ep-sub">Кликните в поле текста, затем по эмодзи</div>
  <div id="epGrid" class="ep-grid"></div>
</div>
<style>
.ep-fab{position:fixed;top:64px;right:124px;z-index:60;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:20px;padding:7px 14px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:var(--shadow);user-select:none}
.ep-panel{position:fixed;top:104px;right:124px;z-index:60;width:330px;max-height:62vh;overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;box-shadow:var(--shadow);display:none}
.ep-panel.open{display:block}
.ep-hd{font-weight:700;font-size:14px;margin-bottom:4px}
.ep-x{cursor:pointer;float:right;color:var(--text-sec)}
.ep-sub{font-size:11px;color:var(--text-sec);margin-bottom:8px}
.ep-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}
.ep-em{width:34px;height:34px;cursor:pointer;border-radius:6px;padding:3px}
.ep-em:hover{background:var(--hover)}
.ep-cell{display:flex;align-items:center;justify-content:center;cursor:pointer}
.ep-ch{display:flex;align-items:center;justify-content:center;font-size:22px;line-height:1}
@media(max-width:767px){
  .ep-fab{top:auto;bottom:calc(84px + env(safe-area-inset-bottom));right:118px;left:auto}
  .ep-panel{left:10px;right:10px;width:auto;top:auto;bottom:calc(132px + env(safe-area-inset-bottom));max-height:55vh}
  .ep-grid{grid-template-columns:repeat(6,1fr)}
}
</style>
<script>
var _epLastTa=null;
document.addEventListener('focusin', function(e){ if(e.target && e.target.tagName==='TEXTAREA') _epLastTa=e.target; });
async function epOpen(){
  var box=document.getElementById('epPanel');
  box.classList.toggle('open');
  if(box.dataset.loaded) return;
  try{
    var r=await fetch('/api/emoji_pack'); var list=await r.json();
    var grid=document.getElementById('epGrid');
    if(!list.length){ grid.innerHTML='<div style=\"grid-column:1/-1;font-size:12px;color:var(--text-sec)\">Пак не загружен. Запустите dump_emoji_pack.py</div>'; }
    else { grid.innerHTML=list.map(function(e){
      var ch=(e.emoji||'★');
      var inner = e.file
        ? '<img class=\"ep-em\" src=\"/emoji_pack/'+e.file+'\" data-ch=\"'+ch+'\" title=\"'+ch+'\" onerror=\"epImgErr(this)\">'
        : '<span class=\"ep-em ep-ch\" title=\"'+ch+'\">'+ch+'</span>';
      return '<span class=\"ep-cell\" onclick=\"epInsert(\\''+e.id+'\\')\">'+inner+'</span>';
    }).join(''); }
    box.dataset.loaded='1';
  }catch(err){ if(window.showToast) showToast('Не удалось загрузить пак','err'); }
}
function epImgErr(img){
  var s=document.createElement('span');
  s.className='ep-em ep-ch'; s.textContent=img.getAttribute('data-ch')||'★';
  img.replaceWith(s);
}
function epInsert(id){
  var ta=_epLastTa;
  if(!ta){ if(window.showToast) showToast('Сначала кликните в поле текста','err'); return; }
  var tok='{эмодзи:'+id+'}';
  var s=ta.selectionStart, e=ta.selectionEnd;
  if(s==null){ s=e=ta.value.length; }
  ta.value=ta.value.slice(0,s)+tok+ta.value.slice(e);
  ta.focus(); ta.selectionStart=ta.selectionEnd=s+tok.length;
}
</script>
"""
# Загрузки храним в пользовательской папке (для установленного приложения папка
# программы только для чтения). Если задан REFORM_DATA_DIR — берём его.
_DATA_DIR = os.environ.get("REFORM_DATA_DIR") or BASE_DIR
UPLOAD_FOLDER = os.path.join(_DATA_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "bmp",
    "mp4", "mov", "avi", "mkv", "webm",
    "pdf", "doc", "docx", "xls", "xlsx", "zip", "rar", "txt", "csv",
}


def _media_kind(filename, mimetype=None):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"jpg", "jpeg", "png", "gif", "webp", "bmp"}:
        return "photo"
    if ext in {"mp4", "mov", "avi", "mkv", "webm"}:
        return "video"
    if mimetype:
        if mimetype.startswith("image/"):
            return "photo"
        if mimetype.startswith("video/"):
            return "video"
    return "document"


# ── TG helper (через HTTP, без asyncio) ────────────────────────────────────────

def _tg_api(method, data=None, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if files:
        resp = _requests.post(url, data=data, files=files, timeout=60, proxies=_TG_PROXIES)
    else:
        resp = _requests.post(url, json=data, timeout=30, proxies=_TG_PROXIES)
    result = resp.json()
    if not result.get("ok"):
        raise Exception(result.get("description", "Telegram API error"))
    return result


def _send_tg(tg_id: int, text: str, reply_markup=None):
    payload = {"chat_id": tg_id, "text": text}
    if reply_markup:
        if hasattr(reply_markup, "model_dump"):
            payload["reply_markup"] = reply_markup.model_dump(exclude_none=True)
        elif hasattr(reply_markup, "dict"):
            payload["reply_markup"] = reply_markup.dict(exclude_none=True)
        else:
            payload["reply_markup"] = reply_markup
    _tg_api("sendMessage", payload)


def _send_tg_media(tg_id: int, media_type: str, file_path: str, caption: str = "", filename: str = None):
    fname = filename or os.path.basename(file_path)
    data = {"chat_id": tg_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "Markdown"
    with open(file_path, "rb") as f:
        if media_type == "photo":
            files = {"photo": (fname, f)}
            return _tg_api("sendPhoto", data=data, files=files)
        if media_type == "video":
            files = {"video": (fname, f)}
            return _tg_api("sendVideo", data=data, files=files)
        files = {"document": (fname, f)}
        return _tg_api("sendDocument", data=data, files=files)


def _tg_file_url(file_id: str):
    info = _tg_api("getFile", {"file_id": file_id})
    path = info["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"


def _message_to_json(m):
    return {
        "id": m["id"],
        "direction": m["direction"],
        "text": m["text"],
        "created_at": m["created_at"].strftime("%d.%m %H:%M"),
        "media_type": m.get("media_type"),
        "media_filename": m.get("media_filename"),
        "sent_by": m.get("sent_by"),
        "has_media": bool(m.get("media_type") or m.get("media_file_id") or m.get("media_local_path")),
    }


# ── Auth decorator ─────────────────────────────────────────────────────────────

def _session_admin_ok():
    """Сессия валидна, только если аккаунт активен и токен совпадает (force-logout/смена пароля
    аннулируют все его сессии). Старые сессии без admin_id считаем недействительными."""
    if not session.get("admin"):
        return False
    aid = session.get("admin_id")
    return bool(aid) and db.admin_token_valid(aid, session.get("admin_token"))


def require_auth(f):
    from functools import wraps
    @wraps(f)
    def dec(*args, **kwargs):
        if not _session_admin_ok():
            session.clear()
            return redirect("/login")
        return f(*args, **kwargs)
    return dec


def require_super(f):
    from functools import wraps
    @wraps(f)
    def dec(*args, **kwargs):
        if not _session_admin_ok():
            session.clear()
            return redirect("/login")
        if not session.get("is_super"):
            return redirect("/chats")
        return f(*args, **kwargs)
    return dec


# ── Web Push: подписка браузера админа на уведомления ──────────────────────────

@app.route("/api/push/vapid_public")
@require_auth
def api_push_vapid_public():
    return jsonify({"key": VAPID_PUBLIC_KEY})


@app.route("/api/push/subscribe", methods=["POST"])
@require_auth
def api_push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth):
        return jsonify({"ok": False, "error": "bad subscription"}), 400
    db.add_push_subscription(endpoint, p256dh, auth, admin_id=session.get("admin_id"))
    return jsonify({"ok": True})


# ── YClients webhook: мгновенное уведомление клиента о новой записи ────────────

def _parse_yc_dt(value):
    """'2026-07-01T14:00:00+03:00' / '2026-07-01 14:00:00' → naive datetime (МСК)."""
    from datetime import datetime as _dt
    if not value:
        return None
    s = str(value).split("+")[0].strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return _dt.strptime(s[:len(fmt) + 2].strip(), fmt)
        except Exception:
            pass
    return None


@app.route("/yclients/webhook", methods=["GET", "POST"])
def yclients_webhook():
    """YClients дёргает этот URL при событиях по записям → мгновенное подтверждение
    клиенту о созданной записи. Публичный (YClients шлёт без авторизации).
    Всегда отвечаем 200, чтобы YClients не уходил в бесконечные ретраи."""
    from datetime import datetime as _dt
    from templates import render_template

    if request.method == "GET":
        # Проверка «живости» из браузера. Реальные события приходят через POST.
        return jsonify({"ok": True, "info": "YClients webhook endpoint — принимает POST"})

    payload = request.get_json(force=True, silent=True)
    if payload is None:
        app.logger.warning("YClients webhook: непарсимый payload: %s",
                           request.get_data(as_text=True)[:500])
        return jsonify({"ok": True})

    events = payload if isinstance(payload, list) else [payload]
    app.logger.info("YClients webhook: событий %s", len(events))  # формат видно в journalctl

    sent = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if (ev.get("resource") or "").lower() != "record":
            continue
        if (ev.get("status") or "").lower() not in ("create", "created"):
            continue
        data = ev.get("data") or {}
        comp = ev.get("company_id") or data.get("company_id")
        if comp is not None and str(comp) != str(YCLIENTS_COMPANY_ID):
            continue
        rid = ev.get("resource_id") or data.get("id")
        if not rid or db.yc_booking_notified(rid):
            continue

        cl = data.get("client") or {}
        phone = cl.get("phone") or data.get("client_phone") or data.get("phone") or ""
        dt = _parse_yc_dt(data.get("datetime") or data.get("date"))
        staff = data.get("staff") or {}
        master = (staff.get("name") or data.get("staff_name") or "").strip()
        low = master.lower()
        is_queue = (not master) or ("очеред" in low) or ("лист ожидан" in low)

        # Очередь/лист ожидания/прошедшее время — не уведомляем, только помечаем.
        if is_queue or not dt or dt < _dt.now():
            db.mark_yc_booking_notified(rid)
            continue

        # Тихие часы: сейчас НЕ шлём и НЕ помечаем — страховочный опрос бота отправит
        # подтверждение, когда наступит рабочее время (после 10:00).
        if config.in_quiet_hours():
            continue

        client = db.get_client_by_phone(phone) if phone else None
        if client and client.get("tg_id") and client["tg_id"] > 0:
            first = client.get("reg_first_name") or client.get("first_name") or "Уважаемый гость"
            text = render_template("booking_created", **{
                "ИМЯ": first, "ДАТА": dt.strftime("%d.%m.%Y"),
                "ВРЕМЯ": dt.strftime("%H:%M"), "ВРАЧ": master,
            })
            try:
                _tg_api("sendMessage", {"chat_id": client["tg_id"], "text": text,
                                        "parse_mode": "HTML"})
                db.save_message(client["id"], "out", text)
                sent += 1
            except Exception as e:
                app.logger.warning("YClients webhook: не отправить (record %s): %s", rid, e)
        db.mark_yc_booking_notified(rid)

    if sent:
        app.logger.info("YClients webhook: подтверждений отправлено %s", sent)
    return jsonify({"ok": True})


# ── Base template ─────────────────────────────────────────────────────────────

BASE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<script>(function(){document.documentElement.setAttribute('data-theme','{{ session.get("theme") or "light" }}');window.CRM_WP='{{ session.get("wallpaper") or "default" }}';window.CRM_NOTIFY={{ 'true' if session.get('notify', True) else 'false' }};})();</script>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="/assets/icons/icon-192.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="{{ clinic }}">
<meta name="theme-color" content="#57101b">
<link rel="apple-touch-icon" href="/assets/icons/icon-180.png">
<title>{{ title }} — {{ clinic }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preload" href="/assets/duende.ttf" as="font" type="font/ttf" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=optional" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@2.47.0/tabler-icons.min.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Great+Vibes&display=optional" rel="stylesheet">
<style>
:root,[data-theme="light"]{
  --accent:#8c1d2b;--accent-h:#74121f;--accent-soft:#ecdcdd;
  --gold:#a8824c;--gold-soft:#e7dcc8;--badge:#d24a3a;
  --bg:#e7e0d3;--card:#f1ebe0;--sidebar-bg:#ebe4d7;--border:#dbd1c1;
  --text:#2c2122;--text-sec:#766b61;--sidebar:300px;
  --chat-bg:#e2dacb;--bubble-in:#fbf7ef;--bubble-out:#8c1d2b;
  --bubble-out-text:#f7e8da;--bubble-in-border:#ddd3c2;--hover:#e5dccc;--input-bg:#eae3d6;
  --shadow:0 1px 2px rgba(60,30,22,.05);--shadow-lg:0 10px 30px rgba(60,25,20,.11);
  --green:#3f9d52;--red:#c83b3b;--orange:#bf7e2c;
  --overlay:rgba(40,18,20,.4);--scroll-thumb:#c9bca7;
  --pink:var(--accent);--pink-d:var(--accent-h);--pink-l:var(--accent-soft);--pink-ll:var(--hover);
  --muted:var(--text-sec);
}
[data-theme="dark"]{
  --accent:#c75b6b;--accent-h:#b34a5b;--accent-soft:#3a1f25;
  --gold:#c2a878;--gold-soft:#2a2018;--badge:#ff6f61;
  --bg:#140d0d;--card:#1f1614;--sidebar-bg:#191110;--border:#382825;
  --text:#f0e7dc;--text-sec:#a89a8f;--chat-bg:#140d0d;
  --bubble-in:#2e2421;--bubble-out:#7e2230;--bubble-out-text:#f6e7d9;
  --bubble-in-border:#33231f;
  --hover:#241917;--input-bg:#1f1614;--shadow:0 1px 2px rgba(0,0,0,.4);
  --shadow-lg:0 14px 44px rgba(0,0,0,.55);--overlay:rgba(0,0,0,.62);
  --scroll-thumb:#3d2c28;
  --green:#5cc08a;--red:#f0707f;--orange:#e0a44a;
  --pink:var(--accent);--pink-d:var(--accent-h);--pink-l:var(--accent-soft);--pink-ll:var(--hover);
  --muted:var(--text-sec);
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:'Inter','Segoe UI',system-ui,sans-serif;background:var(--bg);
  color:var(--text);font-size:14px;transition:background .3s,color .3s}

/* ── Sidebar ── */
.sidebar{position:fixed;top:0;left:0;width:var(--sidebar);height:100vh;
  background:var(--sidebar-bg);border-right:1px solid var(--border);
  display:flex;flex-direction:column;z-index:200;transition:transform .3s ease,background .3s}
@font-face{font-family:'Duende';src:url('/assets/duende.ttf') format('truetype');font-display:optional}
.sidebar .logo{padding:16px 18px 14px;font-family:'Duende','Great Vibes',cursive;
  font-weight:400;font-size:50px;line-height:1.1;color:#57101b;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:10px}
.sidebar .logo i{font-size:20px;color:var(--gold)}
[data-theme="dark"] .sidebar .logo{color:#f6e7d9}
.nav{padding:10px 8px}
.nav a{display:flex;align-items:center;gap:12px;padding:11px 14px;margin:2px 0;
  color:var(--text-sec);text-decoration:none;font-weight:500;transition:all .2s ease;
  font-size:14px;border-radius:10px}
.nav a:hover{background:var(--hover);color:var(--text);transform:translateX(2px)}
.nav a.active{background:var(--accent-soft);color:var(--accent);font-weight:600;box-shadow:inset 3px 0 0 var(--gold)}
.nav a i{font-size:18px;width:20px;text-align:center;flex-shrink:0}
.nav-unread{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;
  padding:0 5px;border-radius:10px;background:var(--badge);color:#fff;font-size:11px;font-weight:700;line-height:1}
.mob-bar .nav-unread{position:absolute;top:0;right:8px;min-width:16px;height:16px;font-size:10px}
.sidebar-foot{margin-top:auto;padding:12px 14px;border-top:1px solid var(--border);
  display:flex;flex-direction:column;gap:6px}
.sidebar-foot a,.theme-btn{color:var(--text-sec);text-decoration:none;font-size:13px;
  display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;
  transition:all .2s;background:none;border:none;cursor:pointer;font-family:inherit;width:100%}
.sidebar-foot a:hover,.theme-btn:hover{background:var(--hover);color:var(--text)}

/* ── Mobile nav ── */
.mob-bar{display:none;position:fixed;bottom:0;left:0;right:0;
  background:var(--card);border-top:1px solid var(--border);z-index:300;
  justify-content:space-between;
  padding:6px max(10px,env(safe-area-inset-left)) calc(4px + env(safe-area-inset-bottom,0)) max(10px,env(safe-area-inset-right));
  transition:background .3s}
.mob-bar a{display:flex;flex-direction:column;align-items:center;gap:2px;flex:1;
  color:var(--text-sec);text-decoration:none;font-size:10px;padding:4px 2px;
  border-radius:8px;min-width:0;text-align:center;transition:color .2s}
.mob-bar a.active{color:var(--accent)}
.mob-bar a .icon{font-size:21px;line-height:1}

/* ── Hamburger ── */
.hamburger{display:none;position:fixed;top:calc(12px + env(safe-area-inset-top));left:12px;z-index:400;
  background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:7px 10px;cursor:pointer;font-size:18px;line-height:1;color:var(--accent);
  box-shadow:var(--shadow);transition:all .2s}
.theme-fab{display:none;position:fixed;top:calc(12px + env(safe-area-inset-top));right:12px;z-index:400;
  background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:7px 10px;cursor:pointer;line-height:1;color:var(--accent);font-size:18px;
  box-shadow:var(--shadow);align-items:center;justify-content:center}
.notify-fab{display:none;position:fixed;top:calc(12px + env(safe-area-inset-top));right:56px;z-index:400;
  background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:7px 10px;cursor:pointer;line-height:1;color:var(--accent);font-size:18px;
  box-shadow:var(--shadow);align-items:center;justify-content:center}
.notify-fab.off{color:var(--text-sec)}
.sidebar-overlay{display:none;position:fixed;inset:0;background:var(--overlay);z-index:199;
  opacity:0;transition:opacity .3s}
.sidebar-overlay.open{display:block;opacity:1}

/* ── Main ── */
.main{margin-left:var(--sidebar);height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* ── Buttons ── */
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:10px;
  border:none;cursor:pointer;font-weight:600;font-size:13px;transition:all .2s ease;text-decoration:none}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent-h);transform:translateY(-1px)}
.btn-primary:active{transform:translateY(0)}
.btn-sm{padding:5px 11px;font-size:12px;border-radius:8px;background:var(--accent);
  color:#fff;cursor:pointer;border:none;text-decoration:none;display:inline-flex;
  align-items:center;gap:4px;font-weight:500;transition:all .2s}
.btn-sm:hover{background:var(--accent-h)}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text-sec)}
.btn-ghost:hover{background:var(--hover);color:var(--accent);border-color:var(--accent)}
.btn-warn{background:rgba(232,163,23,.12);border:1px solid var(--orange);color:var(--orange)}
.btn-warn:hover{background:rgba(232,163,23,.2)}
.btn-danger{background:rgba(229,57,53,.1);border:1px solid var(--red);color:var(--red)}
.btn-danger:hover{background:rgba(229,57,53,.18)}
.btn-icon{width:40px;height:40px;padding:0;border-radius:50%;display:inline-flex;
  align-items:center;justify-content:center;background:transparent;border:none;
  color:var(--text-sec);cursor:pointer;transition:all .2s;flex-shrink:0}
.btn-icon:hover{background:var(--hover);color:var(--accent)}
.btn-send{width:44px;height:44px;border-radius:50%;background:var(--accent);color:#fff;
  border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;
  transition:all .2s;flex-shrink:0}
.btn-send:hover{background:var(--accent-h);transform:scale(1.05)}
.btn-send:disabled{opacity:.5;cursor:not-allowed;transform:none}

/* ── Inputs ── */
input,textarea,select{
  padding:10px 14px;border:1px solid var(--border);border-radius:10px;
  font-family:inherit;font-size:14px;outline:none;background:var(--input-bg);
  transition:all .2s;color:var(--text)}
input:focus,textarea:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(51,144,236,.15)}
input[type=color]{padding:3px 6px;height:36px;width:52px;cursor:pointer}

/* ── Modal ── */
.modal{display:none;position:fixed;inset:0;background:var(--overlay);
  z-index:1000;justify-content:center;align-items:center;padding:16px;
  opacity:0;transition:opacity .25s}
.modal.open{display:flex;opacity:1}
.modal-box{background:var(--card);border-radius:16px;padding:24px;
  max-width:520px;width:100%;max-height:88vh;overflow-y:auto;
  box-shadow:var(--shadow-lg);transform:scale(.96);transition:transform .25s ease}
.modal.open .modal-box{transform:scale(1)}
.modal-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
.modal-head h3{font-size:16px;color:var(--text);font-weight:600}
.modal-close{cursor:pointer;font-size:22px;color:var(--text-sec);line-height:1;
  background:none;border:none;padding:2px 6px;transition:color .2s;border-radius:6px}
.modal-close:hover{color:var(--text);background:var(--hover)}
.form-row{display:flex;flex-direction:column;gap:6px;margin-bottom:14px}
.form-row label{font-size:12px;font-weight:600;color:var(--text-sec);text-transform:uppercase;letter-spacing:.4px}
.form-row input,.form-row select,.form-row textarea{width:100%}

/* ── Tag/category pill ── */
.tag{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border-radius:20px;
  font-size:11px;font-weight:600;color:#fff;margin:2px}

/* ── Chat layout ── */
.chat-layout{display:flex;flex:1;overflow:hidden;background:var(--card)}
.client-list{width:340px;border-right:1px solid var(--border);display:flex;flex-direction:column;
  background:var(--card);flex-shrink:0;transition:background .3s}
.client-list-hdr{padding:14px 16px;font-weight:600;font-size:15px;color:var(--text);
  border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.client-search{padding:10px 12px;border-bottom:1px solid var(--border)}
.client-search input{width:100%;font-size:13px;background:var(--hover);border:1px solid transparent;border-radius:20px;padding:9px 14px;outline:none;transition:background .15s,border-color .15s}
.client-search input:focus{background:var(--input-bg);border-color:var(--accent);box-shadow:none}
.client-items{overflow-y:auto;flex:1}
.client-items::-webkit-scrollbar,.msgs::-webkit-scrollbar{width:6px}
.client-items::-webkit-scrollbar-thumb,.msgs::-webkit-scrollbar-thumb{background:var(--scroll-thumb);border-radius:3px}
.ci{padding:10px 14px;cursor:pointer;display:flex;gap:12px;transition:background .15s ease;align-items:center}
.ci:hover{background:var(--hover)}
.ci.active{background:var(--accent-soft)}
.ci .av{width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,var(--accent),var(--accent-h));
  color:#fff;display:flex;align-items:center;justify-content:center;font-weight:600;flex-shrink:0;font-size:18px}
.ci .info{flex:1;min-width:0}
.ci .cname{font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text)}
.ci .cprev{font-size:13px;color:var(--text-sec);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.ci .meta{display:flex;flex-direction:column;align-items:flex-end;flex-shrink:0;gap:4px}
.ci .ctime{font-size:12px;color:var(--text-sec)}
.unread{background:var(--badge);color:#fff;border-radius:12px;min-width:20px;height:20px;
  padding:0 6px;font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}

/* Кнопка-фильтр «только непрочитанные» */
.dlg-filter{background:none;border:1px solid var(--border);color:var(--text-sec);cursor:pointer;
  width:30px;height:30px;border-radius:9px;display:inline-flex;align-items:center;justify-content:center;
  font-size:16px;transition:all .15s}
.dlg-filter:hover{background:var(--hover);color:var(--text)}
.dlg-filter.active{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
/* Иконка-кнопка в шапке чата */
.hdr-btn{background:none;border:none;color:var(--text-sec);cursor:pointer;width:34px;height:34px;
  border-radius:9px;display:inline-flex;align-items:center;justify-content:center;font-size:18px;transition:all .15s}
.hdr-btn:hover{background:var(--hover);color:var(--text)}
/* Выпадающее меню «три точки»: поиск + обои чата */
.hdr-menu{position:relative}
.hdr-dropdown{position:absolute;right:0;top:100%;min-width:190px;z-index:60;display:none;
  background:var(--card);border:1px solid var(--border);border-radius:12px;
  box-shadow:var(--shadow-lg);overflow:hidden}
.hdr-menu:hover .hdr-dropdown,.hdr-menu.open .hdr-dropdown{display:block}
.hdr-dropdown .hdr-item{display:flex;align-items:center;gap:9px;width:100%;border:none;cursor:pointer;
  padding:9px 12px;font-size:13px;color:var(--text);font-family:inherit;text-align:left;background:none}
.hdr-dropdown .hdr-item:hover{background:var(--hover)}
.hdr-dropdown .hdr-item i{font-size:16px;color:var(--text-sec)}
.hdr-sep{height:1px;background:var(--border);margin:2px 0}
.hdr-cap{font-size:11px;color:var(--text-sec);text-transform:uppercase;letter-spacing:.04em;padding:8px 12px 4px}
.wp-row{display:flex;gap:8px;padding:4px 12px 12px}
.wp-opt{padding:3px;border:2px solid transparent;border-radius:10px;background:none;cursor:pointer;line-height:0}
.wp-opt.active{border-color:var(--accent)}
.wp-sw{display:block;width:30px;height:30px;border-radius:7px;border:1px solid var(--border)}
.sw-light{background:#e7ddc9}
.sw-dark{background:#15100f}
.sw-photo{background:#cdbfae url('/assets/icons/chat-wallpaper.jpg') center/cover}
/* Обои области сообщений */
.msgs.wp-light{background:#e7ddc9}
.msgs.wp-dark{background:#15100f}
.msgs.wp-photo{background-color:#cdbfae;
  background-image:linear-gradient(rgba(20,10,10,.20),rgba(20,10,10,.20)),url('/assets/icons/chat-wallpaper.jpg');
  background-size:cover;background-position:center}
/* Окно выбора обоев (по центру, как в ТГ) */
.wp-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.wp-card{display:flex;flex-direction:column;align-items:center;gap:8px;padding:10px 6px;cursor:pointer;
  border:2px solid var(--border);border-radius:12px;background:none;transition:border-color .15s,background .15s}
.wp-card:hover{background:var(--hover)}
.wp-card.active{border-color:var(--accent)}
.wp-prev{display:block;width:100%;height:64px;border-radius:8px;border:1px solid var(--border)}
.wp-name{font-size:12px;color:var(--text)}
/* Карточка клиента (по тапу в шапке чата) */
.cc-phone{font-size:13px;color:var(--text-sec);margin-bottom:12px}
.cc-stats{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.cc-stat{background:var(--bg);border-radius:10px;padding:10px 12px}
.cc-stat .v{font-size:17px;font-weight:600;color:var(--text);line-height:1.1;word-break:break-word}
.cc-stat .l{font-size:11px;color:var(--text-sec);margin-top:3px}
.cc-vip{display:inline-block;background:var(--gold-soft);color:var(--gold);font-size:11px;font-weight:700;
  padding:2px 12px;border-radius:10px;margin-bottom:12px}
.cc-link{margin-bottom:14px}
.cc-link a{color:var(--accent);font-size:12px;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.cc-notes-label{font-size:12px;color:var(--text-sec);display:block;margin-bottom:6px}
.cc-notes{width:100%;min-height:84px;resize:vertical;border:1px solid var(--border);border-radius:10px;
  padding:10px 12px;font-size:14px;background:var(--input-bg);color:var(--text);font-family:inherit;outline:none}
.cc-empty{color:var(--text-sec);font-size:13px;padding:8px 0}
/* Строка поиска по переписке */
.chat-search-bar{display:flex;align-items:center;gap:8px;padding:8px 14px;background:var(--card);
  border-bottom:1px solid var(--border)}
.chat-search-bar i{color:var(--text-sec);font-size:16px}
.chat-search-bar input{flex:1;border:none;background:var(--input-bg);border-radius:18px;padding:8px 14px;
  font-size:14px;color:var(--text);outline:none}
.chat-search-bar button{background:none;border:none;color:var(--text-sec);cursor:pointer;font-size:16px;
  display:inline-flex;align-items:center}
.chat-srch-info{font-size:12px;color:var(--text-sec);white-space:nowrap;flex-shrink:0}
.chat-srch-info.empty{color:var(--badge)}
.msg mark{background:rgba(176,138,82,.38);color:inherit;border-radius:3px;padding:0 1px}
/* Плавающая кнопка «вниз» */
.scroll-fab{position:absolute;right:20px;bottom:92px;z-index:5;width:40px;height:40px;border-radius:50%;
  background:var(--card);border:1px solid var(--border);color:var(--accent);cursor:pointer;font-size:20px;
  display:none;align-items:center;justify-content:center;box-shadow:var(--shadow-lg);
  opacity:0;transform:translateY(8px);transition:opacity .2s,transform .2s}
.scroll-fab.show{display:flex;opacity:1;transform:translateY(0)}
/* Подсветка зоны перетаскивания файла */
.chat-win.drag{outline:2px dashed var(--accent);outline-offset:-10px;background:var(--accent-soft)}

/* ── Chat window ── */
.chat-win{flex:1;display:flex;flex-direction:column;background:var(--chat-bg);min-width:0;
  transition:background .3s}
.chat-hdr{padding:10px 16px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:12px;background:var(--card);flex-wrap:wrap;
  box-shadow:var(--shadow);z-index:1}
.chat-hdr .info{flex:1;min-width:0}
.chat-hdr .cname{font-weight:600;font-size:15px}
.chat-hdr .cphone{font-size:13px;color:var(--text-sec)}
.msgs{flex:1;overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:4px;
  background:var(--chat-bg);transition:background .3s}
.msg-wrap{display:flex;flex-direction:column;width:fit-content;max-width:min(420px,72%);flex-shrink:0}
.msg-wrap.out{align-self:flex-end;align-items:flex-end}
.msg-wrap.in{align-self:flex-start;align-items:flex-start}
.msg-wrap.system{align-self:center;max-width:85%;align-items:center}
.msg-wrap.msg-new{animation:msgIn .22s cubic-bezier(.22,.61,.36,1)}
@keyframes msgIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
/* Разделители дат */
.date-sep{align-self:center;margin:12px 0 8px;pointer-events:none}
.date-sep span{background:var(--hover);color:var(--text-sec);font-size:11px;font-weight:600;
  padding:4px 14px;border-radius:14px;border:1px solid var(--border);letter-spacing:.02em}
/* Скелетоны загрузки */
@keyframes skShimmer{0%{background-position:-300px 0}100%{background-position:300px 0}}
.sk{border-radius:10px;background:linear-gradient(90deg,var(--hover) 25%,var(--border) 37%,var(--hover) 63%);
  background-size:600px 100%;animation:skShimmer 1.3s linear infinite}
.sk-row{display:flex;gap:12px;align-items:center;padding:10px 14px}
.sk-av{width:48px;height:48px;border-radius:50%;flex-shrink:0}
.sk-line{height:11px;border-radius:6px}
.sk-msg{height:38px;border-radius:12px;margin:5px 0;max-width:60%}
.sk-msg.out{align-self:flex-end;max-width:48%}
.msg{padding:8px 12px 6px;border-radius:12px;line-height:1.45;word-break:break-word;font-size:14px;
  white-space:normal;position:relative;width:fit-content;max-width:100%;box-shadow:var(--shadow)}
.msg .mtext{white-space:pre-wrap}
.msg.in{background:var(--bubble-in);color:var(--text);border-radius:4px 12px 12px 12px;
  border:1px solid var(--bubble-in-border,#e0e4e8)}
[data-theme="dark"] .msg.in{border-color:var(--border)}
.msg.out{background:var(--bubble-out);color:var(--bubble-out-text);border-radius:12px 12px 4px 12px}
.msg.system{background:var(--hover);border:1px dashed var(--border);font-size:12px;
  color:var(--text-sec);text-align:center}
.msg .mtime{font-size:11px;opacity:.72;margin-top:4px;display:block;text-align:right}
.msg .msig{font-weight:600;opacity:.95}
.msg-media{margin-bottom:6px;border-radius:8px;overflow:hidden;max-width:280px;line-height:0}
.msg-media img,.msg-media video{display:block;max-width:280px;max-height:360px;width:auto;height:auto;
  border-radius:8px;cursor:pointer;object-fit:contain}
/* Видеокружок — круглый, как в Telegram */
.msg-media.round{border-radius:50%;width:200px;height:200px;max-width:200px}
.msg-media.round video{width:200px;height:200px;max-width:200px;max-height:200px;
  border-radius:50%;object-fit:cover}
/* Голосовое — компактный плеер с волной (как в Telegram) */
.voice{display:flex;align-items:center;gap:9px;max-width:260px;min-width:190px;padding:3px 0;line-height:1}
.v-play{flex:0 0 auto;width:34px;height:34px;border-radius:50%;border:none;background:var(--accent);
  display:flex;align-items:center;justify-content:center;cursor:pointer;padding:0}
.v-play svg{fill:#fff}
.v-wave{flex:1;display:flex;align-items:center;gap:2px;height:28px;cursor:pointer}
.v-wave i{flex:1 1 0;min-width:2px;background:var(--text-sec);opacity:.4;border-radius:2px;transition:opacity .12s,background .12s}
.v-wave i.on{opacity:1;background:var(--accent)}
.v-time{flex:0 0 auto;font-size:11px;color:var(--text-sec);min-width:30px;text-align:right;font-variant-numeric:tabular-nums}
/* Видеокружок — круг с кольцом прогресса и тап-плеем */
.circle{position:relative;width:200px;height:200px;margin-bottom:6px}
.circle .c-vid{width:200px;height:200px;border-radius:50%;object-fit:cover;display:block;background:#000;cursor:pointer}
.circle .c-ring{position:absolute;inset:0;width:200px;height:200px;transform:rotate(-90deg);pointer-events:none}
.circle .c-ring circle{fill:none;stroke-width:3}
.circle .c-bg{stroke:rgba(255,255,255,.25)}
.circle .c-fg{stroke:var(--accent);transition:stroke-dashoffset .15s linear}
.circle .c-play{position:absolute;inset:0;margin:auto;width:54px;height:54px;border-radius:50%;border:none;
  background:rgba(0,0,0,.42);display:flex;align-items:center;justify-content:center;cursor:pointer;padding:0}
.circle.playing .c-play{display:none}
/* Лайтбокс: фото во весь экран, клик вне фото — закрыть */
.lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.86);z-index:10000;
  align-items:center;justify-content:center;padding:20px;cursor:zoom-out}
.lightbox.open{display:flex}
.lightbox img{max-width:95vw;max-height:95vh;border-radius:6px;cursor:default;
  box-shadow:0 10px 60px rgba(0,0,0,.5)}
.msg-file{display:flex;align-items:center;gap:10px;padding:10px 12px;background:rgba(0,0,0,.04);
  border-radius:8px;text-decoration:none;color:inherit;margin-bottom:4px;transition:background .2s}
[data-theme="dark"] .msg-file{background:rgba(255,255,255,.06)}
.msg-file:hover{background:rgba(51,144,236,.12)}
.msg-file .file-icon{font-size:28px;line-height:1}
.msg-file .file-name{font-size:13px;font-weight:500;word-break:break-all}
.chat-compose{border-top:1px solid var(--border);background:var(--card);padding:8px 12px 12px}
.attach-preview{display:none;flex-wrap:wrap;gap:8px;padding:8px 4px 4px}
.attach-preview.has-files{display:flex}
.attach-item{position:relative;border-radius:10px;overflow:hidden;background:var(--hover);
  border:1px solid var(--border);animation:msgIn .2s ease}
.attach-item img,.attach-item video{width:72px;height:72px;object-fit:cover;display:block}
.attach-item .file-thumb{width:72px;height:72px;display:flex;align-items:center;justify-content:center;font-size:28px}
.attach-item .remove{position:absolute;top:2px;right:2px;width:20px;height:20px;border-radius:50%;
  background:rgba(0,0,0,.55);color:#fff;border:none;cursor:pointer;font-size:12px;line-height:1;
  display:flex;align-items:center;justify-content:center}
.chat-inp{display:flex;gap:8px;align-items:flex-end}
.chat-inp textarea{flex:1;resize:none;min-height:42px;max-height:140px;font-size:14px;
  border-radius:20px;padding:10px 16px;line-height:1.4;background:var(--input-bg)}
.chat-inp input[type=file]{display:none}
.emoji-panel{display:none;flex-wrap:wrap;gap:2px;max-height:180px;overflow-y:auto;padding:8px 4px;
  border:1px solid var(--border);border-radius:12px;margin-bottom:8px;background:var(--input-bg)}
.emoji-panel.open{display:flex}
.emoji-panel button{background:none;border:none;cursor:pointer;font-size:22px;line-height:1;padding:4px 6px;border-radius:8px}
.emoji-panel button:hover{background:var(--hover)}

/* ── Table ── */
.tbl-wrap{background:var(--card);border-radius:12px;overflow:auto;border:1px solid var(--border);
  box-shadow:var(--shadow);transition:background .3s}
table{width:100%;border-collapse:collapse;min-width:500px}
thead tr{background:var(--hover)}
th{padding:12px 14px;text-align:left;font-size:11px;text-transform:uppercase;
  letter-spacing:.5px;color:var(--text-sec);font-weight:600;white-space:nowrap}
td{padding:11px 14px;border-top:1px solid var(--border);font-size:13px;vertical-align:middle}
tr:hover td{background:var(--hover)}

/* ── Toast ── */
.toast{position:fixed;bottom:24px;right:24px;background:var(--card);color:var(--text);
  padding:13px 18px;border-radius:14px;z-index:9999;font-size:13px;font-weight:500;
  border:1px solid var(--border);border-left:3px solid var(--gold);
  box-shadow:var(--shadow-lg);opacity:0;transform:translateY(12px) scale(.98);
  transition:opacity .28s ease,transform .28s cubic-bezier(.22,.61,.36,1);
  pointer-events:none;max-width:calc(100vw - 48px)}
.toast.show{opacity:1;transform:translateY(0) scale(1)}
.toast.ok{border-left-color:var(--green)}
.toast.err{border-left-color:var(--red)}

/* ── Всплывающие пуш-уведомления (на любой странице) ── */
.push-stack{position:fixed;left:14px;bottom:90px;z-index:6000;display:flex;flex-direction:column;gap:8px;
  width:290px;max-width:calc(100vw - 28px);pointer-events:none}
.push-card{pointer-events:auto;background:var(--card);border:1px solid var(--border);border-left:3px solid var(--accent);
  border-radius:12px;box-shadow:var(--shadow-lg);padding:10px 13px;cursor:pointer;animation:pushIn .25s ease;
  transition:opacity .3s,transform .3s}
.push-card.hide{opacity:0;transform:translateX(-14px)}
.push-card .pf{font-weight:600;font-size:13px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.push-card .pt{font-size:12px;color:var(--text-sec);margin-top:3px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
@keyframes pushIn{from{opacity:0;transform:translateX(-16px)}to{opacity:1;transform:translateX(0)}}
@media(max-width:767px){.push-stack{left:10px;right:10px;width:auto;bottom:auto;top:calc(env(safe-area-inset-top) + 10px)}}

/* ── Misc ── */
.page-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:10px}
.page-hdr h2{font-size:20px;color:var(--text);font-weight:600}
.empty{padding:48px 24px;text-align:center;color:var(--text-sec);font-size:14px}
.scroll-page{flex:1;overflow-y:auto;padding:24px}
.login-card{background:var(--card)!important;box-shadow:var(--shadow-lg)!important}
/* Строки в модалке деталей клиента */
.cd-row{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid var(--border);font-size:14px}
.cd-row:last-child{border-bottom:none}
.cd-l{color:var(--text-sec);flex-shrink:0}
.cd-v{font-weight:600;text-align:right;word-break:break-word}

/* ── Responsive ── */
@media(max-width:767px){
  .sidebar{transform:translateX(-100%);height:100dvh;overflow-y:auto;-webkit-overflow-scrolling:touch}
  .sidebar.mob-open{transform:translateX(0)}
  .hamburger{display:block}
  .main{margin-left:0;padding-top:calc(52px + env(safe-area-inset-top));padding-bottom:60px;height:100dvh}
  /* в открытом диалоге: прячем нижнюю навигацию, гамбургер и тему — наверху кнопка «Назад» */
  body.chat-open .mob-bar{display:none}
  body.chat-open .hamburger,body.chat-open .theme-fab,body.chat-open .notify-fab{display:none}
  body.chat-open .main{padding-top:0;padding-bottom:0}
  body.chat-open .chat-hdr{padding-top:calc(10px + env(safe-area-inset-top))}
  body.chat-open .chat-compose{padding-bottom:calc(12px + env(safe-area-inset-bottom))}
  .sidebar .logo{padding-top:calc(16px + env(safe-area-inset-top))}
  .mob-bar{display:flex}
  .chat-layout{flex-direction:column}
  .client-list{width:100%;height:100%;border-right:none;border-bottom:1px solid var(--border)}
  .chat-win{height:100%}
  .client-list.mob-hidden{display:none}
  .chat-win.mob-hidden{display:none}
  .back-btn{display:inline-flex!important}
  .msgs{padding:12px;gap:6px}
  .msg-wrap{max-width:86%}
  .msg{font-size:15px;padding:9px 13px 7px;line-height:1.5}
  .msg-media,.msg-media img,.msg-media video{max-width:74vw;max-height:60vh}
  .page-hdr h2{font-size:18px}
  table{min-width:420px;-webkit-overflow-scrolling:touch}
  /* поля ≥16px — iOS не делает авто-зум при фокусе (#rtxt/#srch перебиваем по id) */
  input,textarea,select{font-size:16px}
  #rtxt,#srch{font-size:16px!important}
  .btn{padding:10px 16px}
  /* Плавающие кнопки темы/уведомлений убраны — теперь они в левой панели (она прокручивается) */
  .scroll-page{padding:16px;-webkit-overflow-scrolling:touch}
  /* (1) Подвал сайдбара: приподнять над нижней панелью, чтобы кнопки не срезались */
  .sidebar-foot{padding-bottom:calc(70px + env(safe-area-inset-bottom))}
  /* (2) Кнопка «вниз» в чате — выше панели быстрых ответов, чтобы не наезжала */
  .scroll-fab{bottom:158px}
  /* (3) Вкладка «Клиенты»: таблица → карточки. Строка = flex: инфо слева (2 строки),
     категория/статус справа по центру. */
  .cli-tbl{min-width:0;width:100%}
  .cli-tbl thead{display:none}
  .cli-tbl,.cli-tbl tbody{display:block;width:auto}
  .cli-tbl tr{display:flex;align-items:center;justify-content:space-between;gap:10px;
    border:1px solid var(--border);border-radius:14px;margin-bottom:10px;
    padding:11px 13px;background:var(--card);cursor:pointer}
  .cli-tbl td{display:block;border:none!important;padding:0;white-space:normal}
  .cli-tbl td:first-child{flex:1;min-width:0}          /* инфо клиента, тянется на всю ширину */
  .cli-tbl td.col-hide-m,.cli-tbl td[data-actions]{display:none}
  .cli-tbl td .av{display:none}                        /* без букв-аватара, как в YClients */
  .cli-tbl td:nth-child(5){flex-shrink:0;max-width:42%}/* колонка категорий/статуса — справа */
  .cli-tbl td:nth-child(5) > div{justify-content:flex-end;align-items:center}
}
@media(min-width:768px){
  .back-btn{display:none!important}
}
</style>
</head>
<body>
{% if session.get('admin') %}
<div class="sidebar-overlay" id="sideOverlay" onclick="closeSidebar()"></div>
<button class="hamburger" onclick="toggleSidebar()" aria-label="Меню"><i class="ti ti-menu-2"></i></button>
<button class="theme-fab" onclick="toggleTheme()" aria-label="Сменить тему"><i class="ti ti-brightness-2"></i></button>
<button class="notify-fab" id="notifyFab" onclick="toggleNotify()" aria-label="Уведомления"><i id="notifyIconFab" class="ti ti-bell"></i></button>
<div class="sidebar" id="sidebar">
  <div class="logo"><i class="ti ti-sparkles"></i> {{ clinic }}</div>
  <nav class="nav">
    <a href="/dashboard" class="{% if active=='dashboard' %}active{% endif %}"><i class="ti ti-layout-dashboard"></i>Сводка</a>
    <a href="/chats"    class="{% if active=='chats'    %}active{% endif %}"><i class="ti ti-message-circle"></i>Чаты <span class="nav-unread" style="display:none"></span></a>
    <a href="/clients"  class="{% if active=='clients'  %}active{% endif %}"><i class="ti ti-users"></i>Клиенты</a>
    <a href="/broadcast" class="{% if active=='broadcast' %}active{% endif %}"><i class="ti ti-speakerphone"></i>Рассылка</a>
    <a href="/templates" class="{% if active=='templates' %}active{% endif %}"><i class="ti ti-template"></i>Шаблоны</a>
    <a href="/chat_templates" class="{% if active=='chat_templates' %}active{% endif %}"><i class="ti ti-bolt"></i>Быстрые ответы</a>
  </nav>
  <div class="sidebar-foot">
    <button class="theme-btn" id="themeToggle" onclick="toggleTheme()">
      <i id="themeIcon" class="ti ti-moon"></i><span id="themeLabel">Тёмная тема</span>
    </button>
    <button class="theme-btn" id="notifyToggle" onclick="toggleNotify()">
      <i id="notifyIcon" class="ti ti-bell"></i><span id="notifyLabel">Уведомления</span>
    </button>
    {% if session.get('is_super') %}<a href="/adm"><i class="ti ti-shield-lock"></i> Контроль доступа</a>{% endif %}
    <a href="/logout"><i class="ti ti-logout"></i> Выйти</a>
  </div>
</div>
<nav class="mob-bar">
  <a href="/chats"    class="{% if active=='chats'    %}active{% endif %}" style="position:relative"><i class="ti ti-message-circle icon"></i>Чаты <span class="nav-unread" style="display:none"></span></a>
  <a href="/clients"  class="{% if active=='clients'  %}active{% endif %}"><i class="ti ti-users icon"></i>Клиенты</a>
  <a href="/broadcast" class="{% if active=='broadcast' %}active{% endif %}"><i class="ti ti-speakerphone icon"></i>Рассылка</a>
  <a href="/templates" class="{% if active=='templates' %}active{% endif %}"><i class="ti ti-template icon"></i>Шаблоны</a>
  <a href="/chat_templates" class="{% if active=='chat_templates' %}active{% endif %}"><i class="ti ti-bolt icon"></i>Быстрые</a>
</nav>
<div class="main">{{ body|safe }}</div>
{% else %}
{{ body|safe }}
{% endif %}
<div id="toast" class="toast"></div>
<div class="push-stack" id="pushStack"></div>
<script>
function savePref(key, value){
  // Личные настройки (тема, обои) сохраняются в аккаунте на сервере.
  try{
    var b = {}; b[key] = value;
    fetch('/api/me/prefs', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)});
  }catch(e){}
}
function applyTheme(theme, save){
  document.documentElement.setAttribute('data-theme', theme);
  var icon = document.getElementById('themeIcon');
  var label = document.getElementById('themeLabel');
  if(icon) icon.className = theme === 'dark' ? 'ti ti-sun' : 'ti ti-moon';
  if(label) label.textContent = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема';
  if(save) savePref('theme', theme);
}
function toggleTheme(){
  var cur = document.documentElement.getAttribute('data-theme') || 'light';
  applyTheme(cur === 'dark' ? 'light' : 'dark', true);
}
applyTheme(document.documentElement.getAttribute('data-theme') || 'light', false);
function applyNotifyUI(){
  var on = window.CRM_NOTIFY !== false;
  var icon = document.getElementById('notifyIcon');
  var label = document.getElementById('notifyLabel');
  if(icon) icon.className = on ? 'ti ti-bell' : 'ti ti-bell-off';
  if(label) label.textContent = on ? 'Уведомления: вкл' : 'Уведомления: выкл';
  var fabIcon = document.getElementById('notifyIconFab');
  if(fabIcon) fabIcon.className = on ? 'ti ti-bell' : 'ti ti-bell-off';
  var fab = document.getElementById('notifyFab');
  if(fab) fab.classList.toggle('off', !on);
}
function toggleNotify(){
  window.CRM_NOTIFY = (window.CRM_NOTIFY === false);  // переключаем вкл/выкл
  applyNotifyUI();
  savePref('notify', window.CRM_NOTIFY);   // сохраняем в аккаунт (и гасит/включает Web Push)
}
applyNotifyUI();
function showToast(msg, type){
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (type || '');
  setTimeout(function(){ t.className = 'toast'; }, 3200);
}
function toggleSidebar(){
  document.getElementById('sidebar').classList.toggle('mob-open');
  document.getElementById('sideOverlay').classList.toggle('open');
}
function closeSidebar(){
  document.getElementById('sidebar').classList.remove('mob-open');
  document.getElementById('sideOverlay').classList.remove('open');
}
function openModal(id){ document.getElementById(id).classList.add('open'); }
function closeModal(id){ document.getElementById(id).classList.remove('open'); }
function esc(s){
  if(!s) return '';
  return s.split('&').join('&amp;').split('<').join('&lt;').split('>').join('&gt;').split('\n').join('<br>');
}
function renderMsgMedia(m){
  if(!m.has_media) return '';
  var url = '/api/media/' + m.id;
  if(m.media_type === 'photo'){
    return '<div class="msg-media"><img src="'+url+'" alt="" style="cursor:zoom-in" onclick="openLightbox(this.src)"></div>';
  }
  if(m.media_type === 'video_note'){
    return '<div class="circle" data-src="'+url+'"></div>';
  }
  if(m.media_type === 'video'){
    return '<div class="msg-media"><video src="'+url+'" controls preload="metadata"></video></div>';
  }
  if(m.media_type === 'audio'){
    return '<div class="voice" data-src="'+url+'"></div>';
  }
  var name = m.media_filename || 'Файл';
  return '<a class="msg-file" href="'+url+'" target="_blank">' +
    '<span class="file-icon"><i class="ti ti-paperclip"></i></span><span class="file-name">'+esc(name)+'</span></a>';
}
// Превращает плейсхолдеры .voice/.circle в плеер с волной и круг с кольцом прогресса.
// Вызывается после первичной отрисовки и после добавления новых сообщений.
function _fmtT(s){ s=Math.max(0,Math.floor(s||0)); return Math.floor(s/60)+':'+('0'+(s%60)).slice(-2); }
function initMediaPlayers(root){
  root = root || document;
  if(!root || !root.querySelectorAll) return;
  try{
  // ── Голосовые ──
  var voices = root.querySelectorAll('.voice:not(.ready)');
  Array.prototype.forEach.call(voices, function(box){
    box.classList.add('ready');
    var src = box.getAttribute('data-src'), bars = '';
    for(var i=0;i<32;i++){ bars += '<i style="height:'+(26+Math.round(Math.abs(Math.sin(i*1.7))*62))+'%"></i>'; }
    box.innerHTML =
      '<button class="v-play" type="button"><svg viewBox="0 0 24 24" width="17" height="17"><path class="v-ic" d="M8 5v14l11-7z"/></svg></button>'+
      '<div class="v-wave">'+bars+'</div><span class="v-time">0:00</span>'+
      '<audio preload="metadata" src="'+src+'"></audio>';
    var audio=box.querySelector('audio'), btn=box.querySelector('.v-play'), ic=box.querySelector('.v-ic'),
        wave=box.querySelector('.v-wave'), tEl=box.querySelector('.v-time'), nb=wave.children.length;
    audio.addEventListener('loadedmetadata', function(){ if(isFinite(audio.duration)) tEl.textContent=_fmtT(audio.duration); });
    btn.addEventListener('click', function(){
      if(audio.paused){
        document.querySelectorAll('.voice audio').forEach(function(a){ if(a!==audio) a.pause(); });
        audio.play();
      } else { audio.pause(); }
    });
    audio.addEventListener('play', function(){ ic.setAttribute('d','M7 5h3.5v14H7zM13.5 5H17v14h-3.5z'); });
    audio.addEventListener('pause', function(){ ic.setAttribute('d','M8 5v14l11-7z'); });
    audio.addEventListener('timeupdate', function(){
      var p=audio.duration?audio.currentTime/audio.duration:0, on=Math.round(p*nb);
      for(var i=0;i<nb;i++){ wave.children[i].classList.toggle('on', i<on); }
      tEl.textContent=_fmtT(audio.currentTime||audio.duration);
    });
    audio.addEventListener('ended', function(){
      for(var i=0;i<nb;i++){ wave.children[i].classList.remove('on'); }
      tEl.textContent=_fmtT(audio.duration);
    });
    wave.addEventListener('click', function(e){
      var rc=wave.getBoundingClientRect();
      if(audio.duration) audio.currentTime=((e.clientX-rc.left)/rc.width)*audio.duration;
    });
  });
  // ── Кружки (video_note) ──
  var circles = root.querySelectorAll('.circle:not(.ready)');
  Array.prototype.forEach.call(circles, function(box){
    box.classList.add('ready');
    var src=box.getAttribute('data-src');
    box.innerHTML =
      '<video class="c-vid" src="'+src+'" preload="metadata" playsinline webkit-playsinline muted></video>'+
      '<svg class="c-ring" viewBox="0 0 100 100"><circle class="c-bg" cx="50" cy="50" r="48"/><circle class="c-fg" cx="50" cy="50" r="48"/></svg>'+
      '<button class="c-play" type="button"><svg viewBox="0 0 24 24" width="30" height="30"><path d="M8 5v14l11-7z" fill="#fff"/></svg></button>';
    var vid=box.querySelector('.c-vid'), ring=box.querySelector('.c-fg'), btn=box.querySelector('.c-play'),
        C=2*Math.PI*48;
    ring.style.strokeDasharray=C; ring.style.strokeDashoffset=C;
    vid.addEventListener('loadedmetadata', function(){ try{ vid.currentTime=0.05; }catch(e){} });
    function play(){
      document.querySelectorAll('.circle video').forEach(function(v){ if(v!==vid) v.pause(); });
      vid.muted=false; vid.play();
    }
    btn.addEventListener('click', play);
    vid.addEventListener('click', function(){ if(vid.paused) play(); else vid.pause(); });
    vid.addEventListener('play', function(){ box.classList.add('playing'); });
    vid.addEventListener('pause', function(){ box.classList.remove('playing'); });
    vid.addEventListener('timeupdate', function(){
      ring.style.strokeDashoffset = C*(1-(vid.duration?vid.currentTime/vid.duration:0));
    });
    vid.addEventListener('ended', function(){ box.classList.remove('playing'); ring.style.strokeDashoffset=C; try{vid.currentTime=0.05;}catch(e){} });
  });
  }catch(e){ if(window.console) console.warn('initMediaPlayers:', e); }
}
// Авто-подпись медиа («📷 Фото» и т.п.) нужна только для превью в списке диалогов,
// в самом пузыре её показывать не нужно — фото/файл и так видны.
function isAutoMediaLabel(m){
  if(!m.has_media || !m.text) return false;
  return m.text==='📷 Фото' || m.text==='🎬 Видео' ||
         (m.media_type==='audio' && m.text==='🎤 Голосовое') ||
         (m.media_type==='video_note' && m.text==='⭕ Видеокружок') ||
         (m.media_type==='document' && m.text==='📎 '+(m.media_filename||''));
}
function renderMsg(m, animate){
  var cls = m.direction === 'system' ? 'system' : m.direction;
  var anim = animate ? ' msg-new' : '';
  var showText = m.text && !isAutoMediaLabel(m);
  var sig = (m.direction === 'out' && m.sent_by) ? '<span class="msig">'+esc(m.sent_by)+'</span> · ' : '';
  return '<div class="msg-wrap '+cls+anim+'" data-msg-id="'+m.id+'"><div class="msg '+cls+'">' +
    renderMsgMedia(m) + (showText ? '<span class="mtext">'+esc(m.text)+'</span>' : '') +
    '<span class="mtime" data-date="'+esc((m.created_at||'').slice(0,5))+'">'+sig+m.created_at+'</span></div></div>';
}

// ── Реалтайм: бейдж непрочитанных + звук + уведомления (SSE с откатом на опрос) ──
var CRM_AUTHED = {% if session.get('admin') %}true{% else %}false{% endif %};
var crmLastIncoming = null, crmAudioCtx = null, crmGotData = false, crmErr = 0;

function crmUnlockAudio(){
  try{
    if(!crmAudioCtx) crmAudioCtx = new (window.AudioContext||window.webkitAudioContext)();
    if(crmAudioCtx.state === 'suspended') crmAudioCtx.resume();
  }catch(e){}
}
document.addEventListener('click', crmUnlockAudio);

function crmDing(){
  try{
    // На телефоне звук лагает — отключаем (там работает системное уведомление).
    if(window.matchMedia && window.matchMedia('(max-width:767px)').matches) return;
    if(!crmAudioCtx){ crmAudioCtx = new (window.AudioContext||window.webkitAudioContext)(); }
    if(crmAudioCtx.state === 'suspended'){ crmAudioCtx.resume(); }
    var ctx=crmAudioCtx, t0=ctx.currentTime;
    function note(freq,start,dur,peak){
      var o=ctx.createOscillator(), g=ctx.createGain();
      o.type='sine'; o.frequency.value=freq;
      o.connect(g); g.connect(ctx.destination);
      var t=t0+start;
      g.gain.setValueAtTime(0.0001,t);
      g.gain.exponentialRampToValueAtTime(peak,t+0.03);
      g.gain.exponentialRampToValueAtTime(0.0001,t+dur);
      o.start(t); o.stop(t+dur+0.02);
    }
    note(784,0,0.34,0.10);     // мягкое «дин»
    note(1047,0.12,0.40,0.09); // «дон»
  }catch(e){}
}

function crmBadge(dialogs){
  document.querySelectorAll('.nav-unread').forEach(function(b){
    if(dialogs>0){ b.textContent=dialogs; b.style.display=''; } else { b.style.display='none'; b.textContent=''; }
  });
  var base=document.title.replace(/^\(\d+\)\s*/,'');
  document.title = dialogs>0 ? '('+dialogs+') '+base : base;
  try{ localStorage.setItem('crm-unread', dialogs); }catch(e){}
}

function crmNotify(p){
  try{
    if(!(('Notification' in window) && Notification.permission==='granted')) return;
    if(document.visibilityState==='visible') return;
    var title=(p&&p.sender)||'Новое сообщение';
    var url=(p&&p.client_id)?('/chats/'+p.client_id):'/chats';
    var opts={body:(p&&p.text)||'Новое сообщение в CRM',
              tag:'crm-'+((p&&p.client_id)||'x'),
              data:{url:url}, icon:'/assets/icons/icon-192.png'};
    // На телефоне (и в установленном приложении) уведомление показывает service worker —
    // тогда клик по нему откроет нужный чат. На десктопе без SW — обычное Notification.
    if(navigator.serviceWorker && navigator.serviceWorker.ready){
      navigator.serviceWorker.ready.then(function(reg){ reg.showNotification(title, opts); })
        .catch(function(){ try{ new Notification(title, opts); }catch(e){} });
    } else {
      var n=new Notification(title, opts);
      n.onclick=function(){ window.focus(); location.href=url; n.close(); };
    }
  }catch(e){}
}

// Всплывающая карточка-пуш внутри панели (видна на любой странице, гаснет через 4с)
function crmPush(p){
  var stack=document.getElementById('pushStack'); if(!stack) return;
  var card=document.createElement('div'); card.className='push-card';
  card.innerHTML='<div class="pf"><i class="ti ti-message-circle"></i> '+esc((p&&p.sender)||'Клиент')+'</div>'+
                 '<div class="pt">'+esc((p&&p.text)||'Новое сообщение')+'</div>';
  card.onclick=function(){ if(p&&p.client_id) location.href='/chats/'+p.client_id; };
  stack.appendChild(card);
  setTimeout(function(){ card.classList.add('hide'); setTimeout(function(){ card.remove(); }, 320); }, 4000);
}

function crmHandle(p){
  if(!p) return;
  crmGotData = true;
  crmBadge(p.dialogs||0);
  if(crmLastIncoming!==null && p.incoming_id>crmLastIncoming && window.CRM_NOTIFY !== false){
    crmDing();
    crmNotify(p);
    if(!(window.activeId && window.activeId === p.client_id)){ crmPush(p); }
  }
  crmLastIncoming = p.incoming_id;
  document.dispatchEvent(new CustomEvent('crm:update', {detail:p}));
}

function crmPoll(){
  // мгновенно показываем последнее известное число (без мигания при переходах)
  try{ var c=parseInt(localStorage.getItem('crm-unread')||'0',10); if(c>0) crmBadge(c); }catch(e){}
  function tick(){
    fetch('/api/unread').then(function(r){return r.json();}).then(crmHandle).catch(function(){});
  }
  tick();                       // сразу при загрузке, не ждём 2.5 секунды
  setInterval(tick, 2500);
}

// Web Push: base64url-ключ → Uint8Array (формат applicationServerKey).
function urlB64ToUint8(base64){
  var pad='='.repeat((4-base64.length%4)%4);
  var b64=(base64+pad).replace(/-/g,'+').replace(/_/g,'/');
  var raw=atob(b64); var arr=new Uint8Array(raw.length);
  for(var i=0;i<raw.length;i++) arr[i]=raw.charCodeAt(i);
  return arr;
}
// Подписка браузера на Web Push (нужно granted-разрешение). Идемпотентно.
function setupPush(reg){
  try{
    if(!('PushManager' in window)) return;
    if(!('Notification' in window) || Notification.permission!=='granted') return;
    fetch('/api/push/vapid_public').then(function(r){return r.json();}).then(function(j){
      if(!j || !j.key) return;
      reg.pushManager.getSubscription().then(function(sub){
        if(sub) return sub;
        return reg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:urlB64ToUint8(j.key)});
      }).then(function(sub){
        if(!sub) return;
        fetch('/api/push/subscribe', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(sub)});
      }).catch(function(e){});
    }).catch(function(){});
  }catch(e){}
}

function crmRealtime(){
  if(!CRM_AUTHED) return;
  if('serviceWorker' in navigator){
    navigator.serviceWorker.register('/sw.js').then(function(reg){
      if(!('Notification' in window)) return;
      if(Notification.permission==='granted'){
        setupPush(reg);
      } else if(Notification.permission==='default'){
        // iOS требует жест пользователя для запроса разрешения — ловим первый клик.
        document.addEventListener('click', function(){
          try{
            Notification.requestPermission().then(function(p){ if(p==='granted') setupPush(reg); });
          }catch(e){}
        }, {once:true});
      }
    }).catch(function(){});
  }
  crmPoll();  // лёгкий опрос; SSE убран — он держал потоки сервера и всё тормозило
}
crmRealtime();
</script>
</body>
</html>"""


def render(tpl, **ctx):
    ctx.setdefault("clinic", CLINIC_NAME)
    ctx["display_name"] = db.client_display_name
    body = render_template_string(tpl, **ctx)
    html = render_template_string(BASE, body=body, **ctx)
    return Response(html, mimetype="text/html")


# ── Login ──────────────────────────────────────────────────────────────────────

LOGIN_TPL = """
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px;position:relative;
  background:radial-gradient(125% 125% at 50% 0%, #6e1320 0%, #4a0e17 55%, #2c0810 100%)">
  <div style="width:100%;max-width:380px">
    <div style="background:#fbf6ee;border-radius:22px;padding:46px 34px 32px;text-align:center;
      box-shadow:0 26px 72px rgba(0,0,0,.48);border:1px solid rgba(176,138,82,.32)">
      <div style="font-family:'Duende','Great Vibes',cursive;font-size:46px;line-height:1.05;color:#57101b">{{ clinic }}</div>
      <div style="width:54px;height:1px;background:#b08a52;margin:14px auto 12px"></div>
      <p style="color:#9a8a78;margin-bottom:26px;font-size:11px;letter-spacing:.18em;text-transform:uppercase">Панель администратора</p>
      {% if err %}<p style="color:#c0392b;margin-bottom:14px;font-size:13px">{{ err }}</p>{% endif %}
      <form method="POST" style="display:flex;flex-direction:column;gap:12px">
        <div style="position:relative;display:flex;align-items:center">
          <i class="ti ti-user" style="position:absolute;left:14px;color:#b08a52;font-size:17px"></i>
          <input type="text" name="login" placeholder="Логин" autocomplete="username"
            style="width:100%;padding:13px 14px 13px 42px;border-radius:12px;border:1px solid #e6dac6;background:#fff;color:#241a1b;font-size:15px;outline:none">
        </div>
        <div style="position:relative;display:flex;align-items:center">
          <i class="ti ti-lock" style="position:absolute;left:14px;color:#b08a52;font-size:17px"></i>
          <input type="password" name="password" placeholder="Пароль" autocomplete="current-password"
            style="width:100%;padding:13px 14px 13px 42px;border-radius:12px;border:1px solid #e6dac6;background:#fff;color:#241a1b;font-size:15px;outline:none">
        </div>
        <button type="submit"
          style="width:100%;padding:13px;margin-top:6px;border:none;border-radius:12px;cursor:pointer;
          background:linear-gradient(135deg,#8c1d2b,#5e1019);color:#fbeede;font-size:15px;font-weight:600;
          letter-spacing:.05em;box-shadow:0 6px 18px rgba(94,16,25,.4)">Войти</button>
      </form>
      <p style="color:#b9ab98;margin-top:22px;font-size:10px;letter-spacing:.16em;text-transform:uppercase">Клиника эстетической медицины</p>
    </div>
  </div>
</div>"""


_login_fails = {}   # ip -> (число_неудачных_попыток, заблокировано_до_unixtime)


@app.route("/login", methods=["GET", "POST"])
def login():
    # Уже авторизован — нечего показывать форму входа, сразу в панель.
    if session.get("admin"):
        return redirect("/chats")
    err = ""
    if request.method == "POST":
        ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
              or request.remote_addr or "?")
        now = time.time()
        cnt, until = _login_fails.get(ip, (0, 0))
        if until > now:
            err = "Слишком много попыток. Подождите пару минут."
        else:
            user = db.verify_admin(request.form.get("login", ""),
                                   request.form.get("password", ""))
            if user:
                session.permanent = True
                session["admin"] = True
                session["admin_id"] = user["id"]
                session["admin_token"] = user.get("token")
                session["admin_name"] = user.get("name") or user["login"]
                session["is_super"] = bool(user.get("is_super"))
                session["theme"] = user.get("theme") or "light"
                session["wallpaper"] = user.get("wallpaper") or "default"
                session["notify"] = bool(user.get("notify"))
                _login_fails.pop(ip, None)
                return redirect("/chats")
            cnt += 1
            until = now + 300 if cnt >= 5 else 0   # после 5 неудач — пауза 5 минут
            _login_fails[ip] = (cnt, until)
            err = "Неверный логин или пароль"
    return render_template_string(
        BASE, body=render_template_string(LOGIN_TPL, err=err, clinic=CLINIC_NAME),
        title="Вход", clinic=CLINIC_NAME, active="", session=session
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ── /adm — скрытая панель супер-админа (управление аккаунтами) ─────────────────

ADM_PIN_TPL = """
<div class="adm-pin">
  <div class="adm-pin-card">
    <div style="font-size:30px">🔒</div>
    <h2 style="margin:8px 0 4px">Контроль доступа</h2>
    <p style="color:var(--text-sec);margin:0 0 14px">Введите PIN-код</p>
    {% if err %}<div style="color:var(--red);margin-bottom:10px">{{ err }}</div>{% endif %}
    <form method="post" action="/adm/pin">
      <input name="pin" type="password" inputmode="numeric" maxlength="4" autofocus
             class="adm-pin-inp" placeholder="••••">
      <button class="adm-go" type="submit">Войти</button>
    </form>
  </div>
</div>
<style>
.adm-pin{display:flex;justify-content:center;padding:60px 16px}
.adm-pin-card{background:var(--card);border:1px solid var(--border);border-radius:16px;
  padding:30px;max-width:340px;width:100%;text-align:center;box-shadow:var(--shadow)}
.adm-pin-inp{width:140px;text-align:center;letter-spacing:10px;font-size:24px;padding:10px;
  border:1px solid var(--border);border-radius:10px;background:var(--bg);color:var(--text)}
.adm-go{display:block;width:100%;margin-top:14px;padding:11px;border:none;border-radius:10px;
  background:var(--accent);color:#fff;font-weight:600;cursor:pointer}
</style>
"""

ADM_TPL = """
<div class="scroll-page" style="max-width:920px;margin:0 auto;padding:18px">
  <h1 style="margin:0 0 4px">Контроль доступа</h1>
  <p style="color:var(--text-sec);margin:0 0 18px">Кабинеты сотрудников, разлогин и аналитика. Видно только супер-админу.</p>

  <div class="adm-sec">
    <div class="adm-h">Аккаунты</div>
    <table class="adm-tbl">
      <tr><th>Имя</th><th>Логин</th><th>Роль</th><th>Статус</th><th>Действия</th></tr>
      {% for a in admins %}
      <tr>
        <td>{{ a.name or '—' }}</td>
        <td>{{ a.login }}</td>
        <td>{% if a.is_super %}<b>Супер-админ</b>{% else %}Админ{% endif %}</td>
        <td>{% if a.is_active %}<span style="color:var(--green,#2e9e5b)">активен</span>{% else %}<span style="color:var(--red)">выключен</span>{% endif %}</td>
        <td>
          {% if a.is_super %}
            <span style="color:var(--text-sec)">— это вы</span>
          {% else %}
          <div class="adm-acts">
            <form method="post" action="/adm/{{a.id}}/password" class="adm-f">
              <input name="password" type="text" class="adm-inp" placeholder="новый пароль">
              <button class="adm-btn">Пароль</button>
            </form>
            <form method="post" action="/adm/{{a.id}}/logout" class="adm-f">
              <button class="adm-btn">Разлогинить</button></form>
            <form method="post" action="/adm/{{a.id}}/active" class="adm-f">
              <input type="hidden" name="active" value="{{ 0 if a.is_active else 1 }}">
              <button class="adm-btn">{{ 'Выключить' if a.is_active else 'Включить' }}</button></form>
            <form method="post" action="/adm/{{a.id}}/delete" class="adm-f"
                  onsubmit="return confirm('Удалить аккаунт {{a.login}} навсегда?')">
              <button class="adm-btn adm-danger">Удалить</button></form>
          </div>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
    <form method="post" action="/adm/logout-all" style="margin-top:12px"
          onsubmit="return confirm('Разлогинить ВСЕХ, включая вас? Все должны будут войти заново.')">
      <button class="adm-btn adm-danger">Разлогинить всех</button>
    </form>
    <p style="color:var(--text-sec);font-size:12px;margin:8px 0 0">Свой пароль супер-админа меняешь в config_local.py на сервере, затем «Разлогинить всех» — старые входы перестанут работать.</p>
  </div>

  <div class="adm-sec">
    <div class="adm-h">Добавить аккаунт</div>
    <form method="post" action="/adm/create" class="adm-create">
      <input name="name" placeholder="Имя (для подписи)" class="adm-inp" required>
      <input name="login" placeholder="Логин" class="adm-inp" autocomplete="off" required>
      <input name="password" placeholder="Пароль" class="adm-inp" autocomplete="new-password" required>
      <button class="adm-btn adm-primary">Создать</button>
    </form>
    <p style="color:var(--text-sec);font-size:12px;margin:8px 0 0">Сотрудник входит на той же странице входа своим логином и паролем. Его сообщения подписываются именем.</p>
  </div>

  <div class="adm-sec">
    <div class="adm-h">Сообщения по админам</div>
    <table class="adm-tbl">
      <tr><th>Админ</th><th>Неделя</th><th>Месяц</th><th>Всё время</th></tr>
      {% for s in stats %}
      <tr><td>{{ s.name }}</td><td>{{ s.week }}</td><td>{{ s.month }}</td><td>{{ s['all'] }}</td></tr>
      {% else %}
      <tr><td colspan="4" style="color:var(--text-sec)">Пока нет отправленных сообщений с подписью.</td></tr>
      {% endfor %}
    </table>
  </div>
</div>
<style>
.adm-sec{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:16px}
.adm-h{font-weight:700;margin-bottom:10px}
.adm-tbl{width:100%;border-collapse:collapse;font-size:14px}
.adm-tbl th{text-align:left;color:var(--text-sec);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--border)}
.adm-tbl td{padding:8px;border-bottom:1px solid var(--border);vertical-align:middle}
.adm-acts{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.adm-f{display:inline-flex;gap:4px;margin:0}
.adm-inp{padding:7px 9px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px}
.adm-btn{padding:7px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px;cursor:pointer}
.adm-btn:hover{background:var(--hover)}
.adm-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.adm-danger{color:var(--red);border-color:var(--red)}
.adm-create{display:flex;flex-wrap:wrap;gap:8px}
.adm-create .adm-inp{flex:1;min-width:150px}
</style>
"""


def _adm_pin_required():
    return bool(config.ADMIN_PIN) and not session.get("adm_pin_ok")


def _adm_action_target(admin_id):
    """Цель действия должна существовать и НЕ быть супер-админом (защита от самоблокировки)."""
    t = db.get_admin(admin_id)
    return t if (t and not t.get("is_super")) else None


@app.route("/adm")
@require_super
def adm_home():
    if _adm_pin_required():
        return render(ADM_PIN_TPL, title="Контроль", active="", err="")
    wk = {r["name"]: r["n"] for r in db.admin_message_stats("week")}
    mo = {r["name"]: r["n"] for r in db.admin_message_stats("month")}
    stats = [{"name": r["name"], "week": wk.get(r["name"], 0),
              "month": mo.get(r["name"], 0), "all": r["n"]}
             for r in db.admin_message_stats(None)]
    return render(ADM_TPL, title="Контроль доступа", active="",
                  admins=db.get_all_admins(), stats=stats)


@app.route("/adm/pin", methods=["POST"])
@require_super
def adm_pin():
    pin = (request.form.get("pin") or "").strip()
    if pin and hmac.compare_digest(pin, str(config.ADMIN_PIN)):
        session["adm_pin_ok"] = True
        return redirect("/adm")
    return render(ADM_PIN_TPL, title="Контроль", active="", err="Неверный PIN")


@app.route("/adm/create", methods=["POST"])
@require_super
def adm_create():
    if _adm_pin_required():
        return redirect("/adm")
    db.create_admin(request.form.get("login", ""), request.form.get("password", ""),
                    request.form.get("name", ""), is_super=False)
    return redirect("/adm")


@app.route("/adm/<int:admin_id>/password", methods=["POST"])
@require_super
def adm_password(admin_id):
    if _adm_pin_required():
        return redirect("/adm")
    if _adm_action_target(admin_id):
        db.set_admin_password(admin_id, request.form.get("password", ""))
    return redirect("/adm")


@app.route("/adm/<int:admin_id>/logout", methods=["POST"])
@require_super
def adm_force_logout(admin_id):
    if _adm_pin_required():
        return redirect("/adm")
    if _adm_action_target(admin_id):
        db.force_logout_admin(admin_id)
    return redirect("/adm")


@app.route("/adm/<int:admin_id>/active", methods=["POST"])
@require_super
def adm_active(admin_id):
    if _adm_pin_required():
        return redirect("/adm")
    if _adm_action_target(admin_id):
        db.set_admin_active(admin_id, request.form.get("active") == "1")
    return redirect("/adm")


@app.route("/adm/<int:admin_id>/delete", methods=["POST"])
@require_super
def adm_delete(admin_id):
    if _adm_pin_required():
        return redirect("/adm")
    if _adm_action_target(admin_id):
        db.delete_admin(admin_id)
    return redirect("/adm")


@app.route("/adm/logout-all", methods=["POST"])
@require_super
def adm_logout_all():
    if _adm_pin_required():
        return redirect("/adm")
    db.logout_all_admins()   # включая супер-админа — после этого войти заново всем
    session.clear()
    return redirect("/login")


@app.route("/api/me/prefs", methods=["POST"])
@require_auth
def api_me_prefs():
    aid = session.get("admin_id")
    data = request.json or {}
    if "theme" in data:
        db.set_admin_pref(aid, "theme", data["theme"])
        session["theme"] = data["theme"]
    if "wallpaper" in data:
        db.set_admin_pref(aid, "wallpaper", data["wallpaper"])
        session["wallpaper"] = data["wallpaper"]
    if "notify" in data:
        on = bool(data["notify"])
        db.set_admin_notify(aid, on)
        session["notify"] = on
    return jsonify({"ok": True})


@app.route("/")
def index():
    return redirect("/chats")


# ── Сводка (мини-дашборд) ──────────────────────────────────────────────────────

DASHBOARD_TPL = """
<div class="scroll-page">
  <div class="page-hdr"><h2><i class="ti ti-layout-dashboard"></i> Сводка</h2></div>
  <div class="dash-cards">
    <div class="dash-card">
      <div class="dc-ic"><i class="ti ti-users"></i></div>
      <div class="dc-v">{{ s.total_clients }}</div><div class="dc-l">Всего клиентов</div>
    </div>
    <div class="dash-card">
      <div class="dc-ic"><i class="ti ti-user-plus"></i></div>
      <div class="dc-v">{{ s.new_7d }}</div><div class="dc-l">Новых за 7 дней</div>
      <div class="dc-sub">сегодня: {{ s.new_today }}</div>
    </div>
    <div class="dash-card">
      <div class="dc-ic"><i class="ti ti-message-2"></i></div>
      <div class="dc-v">{{ s.msg_in_7d }}</div><div class="dc-l">Входящих за 7 дней</div>
      <div class="dc-sub">ответов: {{ s.msg_out_7d }}</div>
    </div>
    <div class="dash-card">
      <div class="dc-ic"><i class="ti ti-bell"></i></div>
      <div class="dc-v">{{ s.unread_dialogs }}</div><div class="dc-l">Непрочитанных диалогов</div>
      <div class="dc-sub">сообщений: {{ s.unread_total }}</div>
    </div>
  </div>
  <div class="dash-chart">
    <div class="dch-hd">Сообщения от клиентов по дням (за 7 дней)</div>
    <div class="dch-bars">
      {% for p in s.series %}
      <div class="dch-col">
        <div class="dch-bar" style="height:{{ (8 + (p.cnt / mx * 120))|int }}px" title="{{ p.cnt }}"><span>{{ p.cnt }}</span></div>
        <div class="dch-x">{{ p.label }}</div>
      </div>
      {% endfor %}
    </div>
  </div>

  <div class="dash-chart">
    <div class="dch-hd">Топ разделов бота (30 дней)</div>
    {% if a.sections %}
      {% for x in a.sections %}
      <div class="bar-row">
        <span class="bar-lbl">{{ x.label }}</span>
        <span class="bar-track"><span class="bar-fill" style="width:{{ (x.n / sec_mx * 100)|int }}%"></span></span>
        <span class="bar-val">{{ x.n }}</span>
      </div>
      {% endfor %}
    {% else %}<div class="dash-empty">Пока нет данных — клиенты ещё не нажимали</div>{% endif %}
  </div>

  <div class="dash-chart">
    <div class="dch-hd">Воронка: старт → телефон (30 дней)</div>
    <div class="funnel">
      <div class="fn-step"><div class="fn-n">{{ a.funnel.starts }}</div><div class="fn-l">Запустили бота</div></div>
      <i class="ti ti-arrow-right fn-arrow" aria-hidden="true"></i>
      <div class="fn-step"><div class="fn-n">{{ a.funnel.phones }}</div><div class="fn-l">Оставили телефон</div></div>
      <div class="fn-rate">{{ a.funnel.rate }}%</div>
    </div>
  </div>
</div>
<style>
.dash-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:20px}
.dash-2col{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-top:16px}
.bar-row{display:flex;align-items:center;gap:12px;margin:10px 0}
.bar-lbl{width:110px;font-size:13px;color:var(--text);flex-shrink:0}
.bar-track{flex:1;height:10px;background:var(--hover);border-radius:6px;overflow:hidden}
.bar-fill{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent-h));border-radius:6px;min-width:2px}
.bar-val{width:40px;text-align:right;font-size:13px;font-weight:600;color:var(--text)}
.dash-empty{color:var(--text-sec);font-size:13px;padding:10px 0}
.funnel{display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.fn-step{text-align:center}
.fn-n{font-size:26px;font-weight:700;color:var(--text);line-height:1}
.fn-l{font-size:12px;color:var(--text-sec);margin-top:4px}
.fn-arrow{font-size:22px;color:var(--text-sec)}
.fn-rate{margin-left:auto;background:var(--accent-soft);color:var(--accent);font-weight:700;font-size:15px;padding:8px 16px;border-radius:12px}
.dash-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px 18px 16px;box-shadow:var(--shadow)}
.dash-card .dc-ic{width:38px;height:38px;border-radius:10px;background:var(--accent-soft);color:var(--accent);
  display:flex;align-items:center;justify-content:center;font-size:20px;margin-bottom:12px}
.dash-card .dc-v{font-size:30px;font-weight:700;color:var(--text);line-height:1}
.dash-card .dc-l{font-size:13px;color:var(--text-sec);margin-top:4px}
.dash-card .dc-sub{font-size:12px;color:var(--muted);margin-top:6px}
.dash-chart{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px;box-shadow:var(--shadow)}
.dch-hd{font-size:14px;font-weight:600;color:var(--text);margin-bottom:18px}
.dch-bars{display:flex;align-items:flex-end;gap:10px;height:160px}
.dch-col{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:6px;height:100%}
.dch-bar{width:70%;max-width:46px;background:linear-gradient(180deg,var(--accent),var(--accent-h));
  border-radius:7px 7px 0 0;position:relative;min-height:8px;transition:height .4s ease}
.dch-bar span{position:absolute;top:-18px;left:0;right:0;text-align:center;font-size:11px;color:var(--text-sec);font-weight:600}
.dch-x{font-size:11px;color:var(--muted)}
</style>"""


@app.route("/dashboard")
@require_auth
def dashboard():
    s = db.get_dashboard_stats()
    mx = max([p["cnt"] for p in s["series"]] or [0]) or 1
    a = db.get_bot_analytics(30)
    sec_mx = max([x["n"] for x in a["sections"]] or [0]) or 1
    return render(DASHBOARD_TPL, title="Сводка", active="dashboard",
                  s=s, mx=mx, a=a, sec_mx=sec_mx)


# ── Чаты ───────────────────────────────────────────────────────────────────────

CHATS_TPL = """
<div class="chat-layout" style="flex:1;overflow:hidden">

  <!-- Список клиентов -->
  <div class="client-list" id="clientList">
    <div class="client-list-hdr" style="display:flex;align-items:center;justify-content:space-between;gap:8px">
      <span><i class="ti ti-message-circle"></i> Диалоги</span>
      <span id="dlgHdrRight" style="display:flex;align-items:center;gap:8px">
        {% if total_unread > 0 %}<span class="unread">{{total_unread}}</span>{% endif %}
        <button class="dlg-filter" id="unreadBtn" onclick="toggleUnreadOnly(this)" title="Только непрочитанные"><i class="ti ti-notebook"></i></button>
      </span>
    </div>
    <div class="client-search">
      <input id="srch" placeholder="Поиск по имени или телефону..." oninput="filterClients(this.value)">
    </div>
    <div class="client-items" id="clist">
      {% for c in clients %}
      <div class="ci {% if c.id==active_id %}active{% endif %}"
           data-id="{{c.id}}"
           onclick="openChat({{c.id}})"
           data-q="{{ display_name(c)|lower }} {{(c.last_name or '')|lower}} {{(c.first_name or '')|lower}} {{(c.patronymic or '')|lower}} {{c.phone or ''}} {{(c.username or '')|lower}}">
        <div class="av">{{(c.first_name or c.last_name or '?')[0]|upper}}</div>
        <div class="info">
          <div class="cname">{{ display_name(c) }}</div>
          <div class="cprev" data-prev>{{c.last_message or 'Нет сообщений'}}</div>
        </div>
        <div class="meta">
          <span class="ctime" data-time data-ts="{% if c.last_message_at %}{{ c.last_message_at.timestamp()|int }}{% else %}0{% endif %}">{% if c.last_message_at %}{{c.last_message_at.strftime('%H:%M')}}{% endif %}</span>
          <span class="unread" data-unread style="{% if c.unread_count == 0 %}display:none{% endif %}">{{c.unread_count or ''}}</span>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>

  <!-- Окно чата -->
  <div class="chat-win {% if not active_client %}mob-hidden{% endif %}" id="chatWin">
    <div id="chatPanel" style="display:{% if active_client %}flex{% else %}none{% endif %};flex:1;flex-direction:column;min-height:0;overflow:hidden;position:relative">
      <div class="chat-hdr">
        <button class="btn btn-ghost back-btn" onclick="backToList()" style="padding:6px 10px;flex-shrink:0"><i class="ti ti-arrow-left"></i> Назад</button>
        <div class="av" id="chatAv" onclick="openClientCard()" title="Карточка клиента" style="cursor:pointer;width:38px;height:38px;border-radius:50%;background:linear-gradient(135deg,var(--accent),var(--accent-h));color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0">
          {% if active_client %}{{(active_client.first_name or active_client.last_name or '?')[0]|upper}}{% endif %}
        </div>
        <div class="info" onclick="openClientCard()" title="Карточка клиента" style="cursor:pointer">
          <div class="cname" id="chatName">{% if active_client %}{{ display_name(active_client) }}{% endif %}</div>
          <div class="cphone" id="chatPhone">{% if active_client %}{{active_client.phone or 'телефон не указан'}}{% endif %}</div>
        </div>
        <div class="hdr-menu" style="flex-shrink:0">
          <button class="hdr-btn" onclick="toggleHdrMenu(event)" aria-label="Меню чата"><i class="ti ti-dots-vertical"></i></button>
          <div class="hdr-dropdown">
            <button class="hdr-item" onclick="toggleChatSearch();closeHdrMenu()"><i class="ti ti-search"></i> Поиск</button>
            <button class="hdr-item" onclick="openModal('wallpaperModal');closeHdrMenu()"><i class="ti ti-photo"></i> Обои чата</button>
          </div>
        </div>
      </div>
      <div class="chat-search-bar" id="chatSearchBar" style="display:none">
        <i class="ti ti-search"></i>
        <input id="chatSrch" placeholder="Поиск в переписке…" oninput="chatSearch(this.value)">
        <span class="chat-srch-info" id="chatSrchInfo"></span>
        <button onclick="toggleChatSearch()" title="Закрыть"><i class="ti ti-x"></i></button>
      </div>
      <div class="msgs" id="msgs">
        {% for m in messages %}
        <div class="msg-wrap {{m.direction}}" data-msg-id="{{m.id}}">
          <div class="msg {{m.direction}}">
            {% if m.media_type == 'photo' %}
            <div class="msg-media"><img src="/api/media/{{m.id}}" alt="" loading="lazy" style="cursor:zoom-in" onclick="openLightbox(this.src)"></div>
            {% elif m.media_type == 'video_note' %}
            <div class="circle" data-src="/api/media/{{m.id}}"></div>
            {% elif m.media_type == 'video' %}
            <div class="msg-media"><video src="/api/media/{{m.id}}" controls preload="metadata"></video></div>
            {% elif m.media_type == 'audio' %}
            <div class="voice" data-src="/api/media/{{m.id}}"></div>
            {% elif m.media_type == 'document' %}
            <a class="msg-file" href="/api/media/{{m.id}}" target="_blank">
              <span class="file-icon"><i class="ti ti-paperclip"></i></span>
              <span class="file-name">{{ m.media_filename or 'Файл' }}</span>
            </a>
            {% endif %}
            {% set _auto = m.media_type and (m.text == '📷 Фото' or m.text == '🎬 Видео' or (m.media_type == 'audio' and m.text == '🎤 Голосовое') or (m.media_type == 'video_note' and m.text == '⭕ Видеокружок') or (m.media_type == 'document' and m.text == '📎 ' ~ (m.media_filename or ''))) %}
            {% if m.text and not _auto %}<span class="mtext">{{ m.text|e }}</span>{% endif %}
            <span class="mtime" data-date="{{m.created_at.strftime('%d.%m')}}">{% if m.direction == 'out' and m.sent_by %}<span class="msig">{{ m.sent_by|e }}</span> · {% endif %}{{m.created_at.strftime('%d.%m %H:%M')}}</span>
          </div>
        </div>
        {% endfor %}
      </div>
      <button class="scroll-fab" id="scrollFab" onclick="scrollMsgsBottom()" title="Вниз"><i class="ti ti-chevron-down"></i></button>
      <!-- Быстрые шаблоны (кнопки над вводом) -->
      <div id="quickTemplates" style="display:flex; gap:8px; flex-wrap:wrap; padding:8px 12px 4px; border-top:1px solid var(--border); background:var(--card);"></div>
      <div class="chat-compose">
        <div class="attach-preview" id="attachPreview"></div>
        <div class="emoji-panel" id="emojiPanel"></div>
        <div class="chat-inp">
          <input type="file" id="fileInput" accept="image/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.zip,.rar,.txt,.csv" multiple>
          <button class="btn-icon" type="button" onclick="toggleEmoji()" title="Эмодзи">
            <svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
          </button>
          <button class="btn-icon" type="button" onclick="document.getElementById('fileInput').click()" title="Прикрепить файл">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
          </button>
          <textarea id="rtxt" rows="1" placeholder="Сообщение..."
            onkeydown="if(event.key==='Enter'&&!event.shiftKey&&!window.matchMedia('(max-width:767px)').matches){event.preventDefault();sendReply();}"
            oninput="autoGrow(this)"></textarea>
          <button class="btn-send" id="sendBtn" onclick="sendReply()" title="Отправить">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
          </button>
        </div>
      </div>
    </div>
    <div class="empty" id="chatPlaceholder" style="margin:auto;text-align:center;{% if active_client %}display:none{% endif %}">
      <div style="font-family:'Duende','Great Vibes',cursive;font-size:56px;line-height:1.05;color:var(--accent)">{{ clinic }}</div>
      <div style="width:60px;height:1px;background:var(--gold);margin:16px auto 12px;opacity:.7"></div>
      <div style="color:var(--text-sec);font-size:13px;letter-spacing:.04em">Выберите диалог, чтобы начать переписку</div>
    </div>
  </div>
</div>

<div id="wallpaperModal" class="modal" onclick="if(event.target===this)closeModal('wallpaperModal')">
  <div class="modal-box" style="max-width:340px">
    <div class="modal-head"><h3>Обои чата</h3><span class="modal-close" onclick="closeModal('wallpaperModal')"><i class="ti ti-x"></i></span></div>
    <div class="wp-grid">
      <button class="wp-card" data-wp="light" onclick="setWallpaper('light')"><span class="wp-prev sw-light"></span><span class="wp-name">Светлая</span></button>
      <button class="wp-card" data-wp="dark" onclick="setWallpaper('dark')"><span class="wp-prev sw-dark"></span><span class="wp-name">Тёмная</span></button>
      <button class="wp-card" data-wp="photo" onclick="setWallpaper('photo')"><span class="wp-prev sw-photo"></span><span class="wp-name">Рабочая</span></button>
    </div>
  </div>
</div>

<div id="clientCardModal" class="modal" onclick="if(event.target===this)closeModal('clientCardModal')">
  <div class="modal-box" style="max-width:380px">
    <div class="modal-head">
      <h3 id="ccName">Карточка клиента</h3>
      <span class="modal-close" onclick="closeModal('clientCardModal')"><i class="ti ti-x"></i></span>
    </div>
    <div class="cc-phone" id="ccPhone"></div>
    <div id="ccVip"></div>
    <div class="cc-stats" id="ccStats"></div>
    <div class="cc-link" id="ccLink"></div>
    <label class="cc-notes-label">Заметки администратора</label>
    <textarea id="ccNotes" class="cc-notes" placeholder="Например: предпочитает вечер, аллергия на…"></textarea>
    <button class="btn btn-primary" onclick="saveClientNotes()" style="margin-top:8px;width:100%;justify-content:center"><i class="ti ti-device-floppy"></i> Сохранить заметку</button>
  </div>
</div>

<div id="lightbox" class="lightbox" onclick="closeLightbox()">
  <img id="lightboxImg" src="" alt="" onclick="event.stopPropagation()">
</div>

<script>
var activeId = {% if active_id %}{{ active_id }}{% else %}null{% endif %};
var pendingFiles = [];
var lastKnownMsgIds = new Set();
var pollTimer = null;

function autoGrow(el){
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

function initMsgState(){
  lastKnownMsgIds.clear();
  var box = document.getElementById('msgs');
  if(!box) return;
  box.querySelectorAll('[data-msg-id]').forEach(function(el){
    lastKnownMsgIds.add(parseInt(el.dataset.msgId, 10));
  });
}

function getLastMsgId(){
  var max = 0;
  lastKnownMsgIds.forEach(function(id){ if(id > max) max = id; });
  return max;
}

function isAtBottom(box){
  return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
}

function appendMsg(m, animate){
  var box = document.getElementById('msgs');
  if(!box || lastKnownMsgIds.has(m.id)) return false;
  var tmp = document.createElement('div');
  tmp.innerHTML = renderMsg(m, animate);
  var node = tmp.firstChild;
  box.appendChild(node);
  initMediaPlayers(node);
  lastKnownMsgIds.add(m.id);
  return true;
}

// Прокрутка к самому низу диалога. Картинки/видео грузятся позже и меняют высоту,
// поэтому пиннимся ко дну несколько раз: сразу, по кадру, при загрузке каждого медиа и через таймеры.
function scrollMsgsBottom(box){
  box = box || document.getElementById('msgs');
  if(!box) return;
  var pin = function(){ box.scrollTop = box.scrollHeight; };
  var media = Array.prototype.slice.call(box.querySelectorAll('img,video'));
  var pending = media.filter(function(el){
    return !(el.complete || (el.readyState && el.readyState >= 2));
  });
  if(!pending.length){ pin(); requestAnimationFrame(pin); return; }
  // Прячем содержимое (visibility, место сохраняется), пока грузятся фото/видео,
  // держим прокрутку внизу — и показываем уже внизу, без видимого «прыжка».
  box.style.visibility = 'hidden';
  pin();
  var done = 0, finished = false;
  var reveal = function(){
    if(finished) return; finished = true;
    pin(); requestAnimationFrame(pin);
    box.style.visibility = '';
  };
  pending.forEach(function(el){
    var f = function(){ pin(); if(++done >= pending.length) reveal(); };
    el.addEventListener('load', f, {once:true});
    el.addEventListener('loadeddata', f, {once:true});
    el.addEventListener('error', f, {once:true});
  });
  setTimeout(reveal, 700);   // страховка, чтобы чат не остался скрытым
}

async function openChat(id){
  if(activeId === id) return;
  activeId = id;
  history.pushState({chatId: id}, '', '/chats/' + id);
  document.querySelectorAll('.ci').forEach(function(el){
    el.classList.toggle('active', parseInt(el.dataset.id, 10) === id);
  });
  // Мгновенно подставляем имя/аватар выбранного диалога в шапку — без мелькания старого.
  var ciEl = document.querySelector('.ci[data-id="'+id+'"]');
  if(ciEl){
    var nmEl = ciEl.querySelector('.cname');
    var nm = nmEl ? nmEl.textContent.trim() : '';
    if(nm){
      var hName = document.getElementById('chatName'); if(hName) hName.textContent = nm;
      var hAv = document.getElementById('chatAv');
      if(hAv){ hAv.textContent = (nm[0] || '?').toUpperCase(); hAv.style.background = avColor(nm); }
      var hPh = document.getElementById('chatPhone'); if(hPh) hPh.textContent = '';
    }
  }
  if(window.innerWidth < 768){
    document.getElementById('clientList').classList.add('mob-hidden');
    document.getElementById('chatWin').classList.remove('mob-hidden');
    document.body.classList.add('chat-open');
  }
  await loadChat(id);
}

async function loadChat(id){
  document.getElementById('chatPanel').style.display = 'flex';
  document.getElementById('chatPlaceholder').style.display = 'none';
  // Сразу убираем переписку предыдущего диалога, чтобы при переходе (особенно
  // на телефоне) не мелькал чужой чат, пока грузится новый.
  var _mb = document.getElementById('msgs'); if(_mb) _mb.innerHTML = '';
  // Скелетон показываем только если загрузка реально затянулась (>220мс).
  var skTimer = setTimeout(function(){ showChatSkeleton(document.getElementById('msgs')); }, 220);
  try{
    var r = await fetch('/api/chat/' + id);
    var data = await r.json();
    clearTimeout(skTimer);
    if(!data.ok) return;

    var c = data.client;
    var nm = c.display_name || ((c.last_name || '') + ' ' + (c.first_name || '') + ' ' + (c.patronymic || '')).trim() || '?';
    var avEl = document.getElementById('chatAv');
    avEl.textContent = (nm.trim()[0] || '?').toUpperCase();
    avEl.style.background = avColor(nm);
    document.getElementById('chatName').textContent = nm;
    document.getElementById('chatPhone').textContent = c.phone || 'телефон не указан';

    var box = document.getElementById('msgs');
    box.innerHTML = data.messages.map(function(m){ return renderMsg(m, false); }).join('');
    lastKnownMsgIds.clear();
    initMsgState();
    initMediaPlayers(box);
    insertDateSeparators(box);
    scrollMsgsBottom(box);
    updateScrollFab();
    // сбрасываем поиск по переписке при переходе в другой диалог
    var csb = document.getElementById('chatSearchBar'); if(csb) csb.style.display = 'none';
    var cs = document.getElementById('chatSrch'); if(cs) cs.value = '';

    var ci = document.querySelector('.ci[data-id="'+id+'"] [data-unread]');
    if(ci){ ci.style.display = 'none'; ci.textContent = ''; }
    refreshHdrUnread();   // сразу пересчитать бейдж диалогов (не ждать опроса/скролла)
  } catch(e){ clearTimeout(skTimer); }
}

function backToList(){
  activeId = null;
  document.body.classList.remove('chat-open');
  document.getElementById('clientList').classList.remove('mob-hidden');
  document.getElementById('chatWin').classList.add('mob-hidden');
  document.getElementById('chatPanel').style.display = 'none';
  document.getElementById('chatPlaceholder').style.display = '';
  history.pushState({chatId: null}, '', '/chats');
}

var _dlgQ = '', _unreadOnly = false;
function applyDialogFilter(){
  document.querySelectorAll('.ci').forEach(function(el){
    var hay = (el.dataset.q || '');
    var name = el.querySelector('.cname');
    if(name) hay += ' ' + name.textContent.toLowerCase();
    var okQ = (_dlgQ === '' || hay.indexOf(_dlgQ) !== -1);
    var u = el.querySelector('[data-unread]');
    var hasUnread = u && u.style.display !== 'none' && (u.textContent || '').trim() !== '';
    var okU = (!_unreadOnly || hasUnread);
    el.style.display = (okQ && okU) ? '' : 'none';
  });
}
function filterClients(q){ _dlgQ = (q || '').trim().toLowerCase(); applyDialogFilter(); }
function toggleUnreadOnly(btn){
  _unreadOnly = !_unreadOnly;
  if(btn) btn.classList.toggle('active', _unreadOnly);
  applyDialogFilter();
}

// Относительное время в списке диалогов: HH:MM сегодня, «Вчера», день недели, иначе дд.мм
function relTime(ts){
  if(!ts) return '';
  var d = new Date(ts * 1000), now = new Date();
  var p = function(n){ return (n < 10 ? '0' : '') + n; };
  if(d.toDateString() === now.toDateString()) return p(d.getHours()) + ':' + p(d.getMinutes());
  var y = new Date(now.getTime() - 86400000);
  if(d.toDateString() === y.toDateString()) return 'Вчера';
  if((now - d) < 7 * 86400000) return ['вс','пн','вт','ср','чт','пт','сб'][d.getDay()];
  return p(d.getDate()) + '.' + p(d.getMonth() + 1);
}
function formatDialogTimes(){
  document.querySelectorAll('.ctime[data-ts]').forEach(function(el){
    var ts = parseInt(el.dataset.ts || '0', 10);
    if(ts) el.textContent = relTime(ts);
  });
}

// ── Цветные аватары: цвет из имени (фирменная палитра драгоценных тонов) ──
var AV_COLORS = [
  ['#8c1d2b','#5e1019'], ['#1f6f6b','#124a47'], ['#7a5a2e','#4f3a1c'],
  ['#5b3a6b','#3c2647'], ['#3f6b46','#27452d'], ['#3f5a8c','#293c5e'],
  ['#a8552e','#6e371d'], ['#6b6f2e','#45471c']
];
function avColor(str){
  var h=0; str=str||'';
  for(var i=0;i<str.length;i++){ h=str.charCodeAt(i)+((h<<5)-h); }
  var c=AV_COLORS[Math.abs(h)%AV_COLORS.length];
  return 'linear-gradient(135deg,'+c[0]+','+c[1]+')';
}
function colorizeAvatars(root){
  (root||document).querySelectorAll('.ci').forEach(function(ci){
    var av=ci.querySelector('.av'), nm=ci.querySelector('.cname');
    if(av && nm && !av.dataset._c){ av.dataset._c='1'; av.style.background=avColor(nm.textContent.trim()); }
  });
}

// ── Метки дат для разделителей в чате ──
function dateLabel(ddmm){
  var now=new Date(), pad=function(n){return (n<10?'0':'')+n;};
  var today=pad(now.getDate())+'.'+pad(now.getMonth()+1);
  var y=new Date(now.getTime()-86400000);
  var yest=pad(y.getDate())+'.'+pad(y.getMonth()+1);
  if(ddmm===today) return 'Сегодня';
  if(ddmm===yest) return 'Вчера';
  return ddmm;
}
function insertDateSeparators(box){
  box = box || document.getElementById('msgs'); if(!box) return;
  box.querySelectorAll('.date-sep').forEach(function(s){ s.remove(); });
  var last=null;
  Array.prototype.slice.call(box.querySelectorAll('.msg-wrap')).forEach(function(w){
    var t=w.querySelector('.mtime'); if(!t) return;
    var d=(t.getAttribute('data-date') || (t.textContent||'').trim().slice(0,5));
    if(d && d!==last){
      var sep=document.createElement('div');
      sep.className='date-sep'; sep.innerHTML='<span>'+dateLabel(d)+'</span>';
      box.insertBefore(sep, w);
      last=d;
    }
  });
}

// ── Скелетон чата, пока грузятся сообщения ──
function showChatSkeleton(box){
  box = box || document.getElementById('msgs'); if(!box) return;
  var rows=['in','in','out','in','out','out'].map(function(side){
    return '<div class="sk sk-msg '+side+'" style="width:'+(38+Math.random()*30)+'%"></div>';
  }).join('');
  box.innerHTML='<div style="display:flex;flex-direction:column;width:100%">'+rows+'</div>';
}

// Красим аватары сразу при разборе страницы (разметка списка уже выше) —
// до первой отрисовки, чтобы не было заметного «перекраса»/моргания кружков.
colorizeAvatars();
formatDialogTimes();   // сразу относительное время (Вчера/день), чтобы не моргало ЧЧ:ММ
try{ initMediaPlayers(); }catch(e){}   // плееры — не должны влиять на остальной скрипт (опрос ниже)
(function(){
  var cav=document.getElementById('chatAv'), cnm=document.getElementById('chatName');
  if(cav && cnm && cnm.textContent.trim()) cav.style.background=avColor(cnm.textContent.trim());
})();

function updateAttachPreview(){
  var box = document.getElementById('attachPreview');
  if(!box) return;
  box.innerHTML = '';
  pendingFiles.forEach(function(f, i){
    var item = document.createElement('div');
    item.className = 'attach-item';
    if(f.type.startsWith('image/')){
      var img = document.createElement('img');
      img.src = URL.createObjectURL(f);
      item.appendChild(img);
    } else if(f.type.startsWith('video/')){
      var vid = document.createElement('video');
      vid.src = URL.createObjectURL(f);
      item.appendChild(vid);
    } else {
      var thumb = document.createElement('div');
      thumb.className = 'file-thumb';
      thumb.innerHTML = '<i class="ti ti-paperclip"></i>';
      item.appendChild(thumb);
    }
    var rm = document.createElement('button');
    rm.className = 'remove';
    rm.textContent = '×';
    rm.onclick = function(){ pendingFiles.splice(i, 1); updateAttachPreview(); };
    item.appendChild(rm);
    box.appendChild(item);
  });
  box.classList.toggle('has-files', pendingFiles.length > 0);
}

document.getElementById('fileInput') && document.getElementById('fileInput').addEventListener('change', function(e){
  addFiles(e.target.files);
  e.target.value = '';
});

// Добавление файлов с авто-сжатием крупных картинок (быстрее грузить, экономит место).
function addFiles(files){
  Array.from(files || []).forEach(function(f){
    if(f.type && f.type.indexOf('image/') === 0){
      compressImage(f).then(function(cf){ pendingFiles.push(cf); updateAttachPreview(); });
    } else {
      pendingFiles.push(f); updateAttachPreview();
    }
  });
}
function compressImage(file){
  return new Promise(function(resolve){
    try{
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function(){
        var MAX = 1600, w = img.width, h = img.height;
        if(w <= MAX && h <= MAX && file.size < 1024 * 1024){ URL.revokeObjectURL(url); resolve(file); return; }
        var scale = Math.min(1, MAX / Math.max(w, h));
        var cw = Math.round(w * scale), ch = Math.round(h * scale);
        var cv = document.createElement('canvas'); cv.width = cw; cv.height = ch;
        cv.getContext('2d').drawImage(img, 0, 0, cw, ch);
        cv.toBlob(function(blob){
          URL.revokeObjectURL(url);
          if(!blob){ resolve(file); return; }
          var name = (file.name || 'photo').replace(/\\.[^.]+$/, '') + '.jpg';
          resolve(new File([blob], name, { type: 'image/jpeg' }));
        }, 'image/jpeg', 0.85);
      };
      img.onerror = function(){ URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    } catch(e){ resolve(file); }
  });
}

// Поиск по тексту внутри переписки
function toggleChatSearch(){
  var bar = document.getElementById('chatSearchBar'); if(!bar) return;
  var open = bar.style.display === 'none' || !bar.style.display;
  bar.style.display = open ? 'flex' : 'none';
  if(open){ var i = document.getElementById('chatSrch'); if(i){ i.focus(); } }
  else { var i2 = document.getElementById('chatSrch'); if(i2){ i2.value = ''; } chatSearch(''); }
}
function _hlEscape(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function _hlMark(text, q){
  var lo = text.toLowerCase(), qlo = q.toLowerCase(), out = '', i = 0;
  while(true){
    var idx = lo.indexOf(qlo, i);
    if(idx === -1){ out += _hlEscape(text.slice(i)); break; }
    out += _hlEscape(text.slice(i, idx)) + '<mark>' + _hlEscape(text.slice(idx, idx + q.length)) + '</mark>';
    i = idx + q.length;
  }
  return out;
}
function chatSearch(q){
  q = (q || '').trim();
  var box = document.getElementById('msgs'); if(!box) return;
  var ql = q.toLowerCase(), matches = 0;
  box.querySelectorAll('.msg-wrap').forEach(function(w){
    var el = w.querySelector('.mtext');
    if(el && el.dataset._orig === undefined) el.dataset._orig = el.textContent;
    if(!ql){
      if(el) el.textContent = el.dataset._orig;   // снять подсветку
      w.style.display = '';
      return;
    }
    var orig = el ? el.dataset._orig : '';
    var hit = el && orig.toLowerCase().indexOf(ql) !== -1;
    w.style.display = hit ? '' : 'none';
    if(hit){ matches++; el.innerHTML = _hlMark(orig, q); }
    else if(el){ el.textContent = el.dataset._orig; }
  });
  box.querySelectorAll('.date-sep').forEach(function(s){ s.style.display = ql ? 'none' : ''; });
  var info = document.getElementById('chatSrchInfo');
  if(info){
    if(!ql){ info.textContent = ''; info.classList.remove('empty'); }
    else if(matches > 0){ info.textContent = matches + ' найдено'; info.classList.remove('empty'); }
    else { info.textContent = 'ничего не найдено'; info.classList.add('empty'); }
  }
}

// Плавающая кнопка «вниз»
function updateScrollFab(){
  var box = document.getElementById('msgs'), fab = document.getElementById('scrollFab');
  if(!box || !fab) return;
  var far = (box.scrollHeight - box.scrollTop - box.clientHeight) > 240;
  fab.classList.toggle('show', far);
}

// Меню «три точки» в шапке (наведение/клик) + обои чата
function toggleHdrMenu(e){
  if(e) e.stopPropagation();
  var m = e && e.currentTarget.closest('.hdr-menu');
  if(m) m.classList.toggle('open');
}
function closeHdrMenu(){
  document.querySelectorAll('.hdr-menu.open').forEach(function(m){ m.classList.remove('open'); });
}
function setWallpaper(wp){
  window.CRM_WP = wp;
  savePref('wallpaper', wp);   // личная настройка аккаунта
  applyWallpaper();   // меняем фон сразу, окно не закрываем — только по крестику
}
function applyWallpaper(){
  var wp = window.CRM_WP || 'default';
  var box = document.getElementById('msgs');
  if(box){
    box.classList.remove('wp-light','wp-dark','wp-photo');
    if(wp !== 'default') box.classList.add('wp-' + wp);
  }
  document.querySelectorAll('.wp-card').forEach(function(b){
    b.classList.toggle('active', b.dataset.wp === wp);
  });
}
document.addEventListener('click', function(e){
  if(!e.target.closest('.hdr-menu')) closeHdrMenu();
});

// Карточка клиента: контекст YClients + заметки админа (тап по имени в шапке чата)
function ccStat(v, l){
  return '<div class="cc-stat"><div class="v">'+esc(String(v))+'</div><div class="l">'+esc(l)+'</div></div>';
}
function openClientCard(){
  if(!activeId) return;
  var m = document.getElementById('clientCardModal'); if(!m) return;
  var nm = document.getElementById('chatName');
  document.getElementById('ccName').textContent = (nm && nm.textContent.trim()) || 'Карточка клиента';
  document.getElementById('ccPhone').textContent = '';
  document.getElementById('ccVip').innerHTML = '';
  document.getElementById('ccStats').innerHTML = '<div class="cc-empty">Загрузка…</div>';
  document.getElementById('ccLink').innerHTML = '';
  document.getElementById('ccNotes').value = '';
  m.classList.add('open');
  fetch('/api/client/'+activeId+'/card').then(function(r){ return r.json(); }).then(function(d){
    if(!d || !d.ok){ document.getElementById('ccStats').innerHTML = '<div class="cc-empty">Клиент не найден.</div>'; return; }
    document.getElementById('ccName').textContent = d.name || 'Клиент';
    document.getElementById('ccPhone').innerHTML = d.phone ? ('<i class="ti ti-phone" style="font-size:13px;vertical-align:-2px"></i> '+esc(d.phone)) : '';
    document.getElementById('ccNotes').value = d.notes || '';
    var y = d.yclients || {};
    if(y.found){
      document.getElementById('ccVip').innerHTML = y.is_vip ? '<span class="cc-vip">VIP</span>' : '';
      document.getElementById('ccStats').innerHTML =
        ccStat((y.visits != null ? y.visits : '—'), 'визитов') +
        ccStat((y.nearest || '—'), 'ближайшая запись') +
        ccStat((y.last_visit || '—'), 'последний визит') +
        ccStat(Math.round(y.bonus || 0), 'бонусы');
      document.getElementById('ccLink').innerHTML = y.url
        ? ('<a href="'+y.url+'" target="_blank"><i class="ti ti-external-link" style="font-size:12px"></i> Открыть в YClients</a>')
        : '';
    } else {
      document.getElementById('ccStats').innerHTML = '<div class="cc-empty">Данные YClients недоступны (нет телефона или клиент не найден).</div>';
    }
  }).catch(function(){
    document.getElementById('ccStats').innerHTML = '<div class="cc-empty">Не удалось загрузить.</div>';
  });
}
function saveClientNotes(){
  if(!activeId) return;
  var notes = document.getElementById('ccNotes').value;
  fetch('/api/client/'+activeId+'/notes', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({notes:notes})
  }).then(function(r){ return r.json(); }).then(function(d){
    if(d && d.ok) showToast('Заметка сохранена', 'ok');
    else showToast('Не удалось сохранить', 'err');
  }).catch(function(){ showToast('Ошибка сети', 'err'); });
}

// Лайтбокс для фото (зум на весь экран, клик вне фото — закрыть)
function openLightbox(src){
  var lb = document.getElementById('lightbox'); if(!lb) return;
  document.getElementById('lightboxImg').src = src;
  lb.classList.add('open');
}
function closeLightbox(){
  var lb = document.getElementById('lightbox'); if(!lb) return;
  lb.classList.remove('open');
  document.getElementById('lightboxImg').src = '';
}

async function sendReply(){
  if(window._crmSending) return;            // защита от повторной отправки (двойной клик / Enter)
  var textarea = document.getElementById('rtxt');
  var text = textarea.value.trim();
  if(!text && !pendingFiles.length){ showToast('Введите сообщение или прикрепите файл', 'err'); return; }
  if(!activeId){ showToast('Клиент не выбран', 'err'); return; }

  window._crmSending = true;
  var btn = document.getElementById('sendBtn');
  if(btn) btn.disabled = true;

  try{
    var result;
    if(pendingFiles.length){
      for(var i = 0; i < pendingFiles.length; i++){
        var fd = new FormData();
        fd.append('text', i === 0 ? text : '');
        fd.append('file', pendingFiles[i]);
        var resp = await fetch('/api/send/' + activeId, { method: 'POST', body: fd });
        result = await resp.json();
        if(!result.ok) break;
      }
    } else {
      var resp = await fetch('/api/send/' + activeId, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: text})
      });
      result = await resp.json();
    }

    if(result && result.ok){
      textarea.value = '';
      textarea.style.height = 'auto';
      pendingFiles = [];
      updateAttachPreview();
      await syncMsgs(true);
      syncDialogs();
      if(result.warning) showToast(result.warning, '');
    } else {
      showToast('Ошибка: ' + (result && result.error || 'неизвестная'), 'err');
    }
  } catch(e){
    showToast('Ошибка сети: ' + e.message, 'err');
  } finally {
    window._crmSending = false;
    if(btn) btn.disabled = false;
  }
}

async function syncMsgs(forceScroll){
  if(!activeId) return;
  try{
    var box = document.getElementById('msgs');
    if(!box) return;
    var atBottom = isAtBottom(box);
    var after = getLastMsgId();
    var r = await fetch('/api/messages/' + activeId + '?after=' + after);
    if(!r.ok) return;
    var msgs = await r.json();
    if(!msgs.length) return;

    var added = false;
    msgs.forEach(function(m){
      if(appendMsg(m, true)) added = true;
    });
    if(added) insertDateSeparators(box);
    if(added && (forceScroll || atBottom)){
      box.scrollTop = box.scrollHeight;
    }
  } catch(e){}
}

function buildDialogEl(d){
  // строит DOM-элемент нового диалога (для авто-появления без перезагрузки)
  var el = document.createElement('div');
  el.className = 'ci';
  el.dataset.id = d.id;
  el.setAttribute('onclick', 'openChat(' + d.id + ')');
  el.dataset.q = d.q || '';
  el.innerHTML =
    '<div class="av">' + esc(d.letter || '?') + '</div>' +
    '<div class="info">' +
      '<div class="cname">' + esc(d.display_name || 'Клиент') + '</div>' +
      '<div class="cprev" data-prev>' + esc(d.last_message || 'Нет сообщений') + '</div>' +
    '</div>' +
    '<div class="meta">' +
      '<span class="ctime" data-time data-ts="' + (d.last_message_ts || 0) + '"></span>' +
      '<span class="unread" data-unread style="display:none"></span>' +
    '</div>';
  return el;
}

// Бейдж у колокольчика = число непрочитанных ДИАЛОГОВ (а не сообщений), без активного.
// Считаем по DOM, поэтому обновляется мгновенно (при входе в чат бейдж диалога прячется).
function refreshHdrUnread(){
  var n = 0;
  document.querySelectorAll('.ci').forEach(function(el){
    if(parseInt(el.dataset.id, 10) === activeId) return;
    var u = el.querySelector('[data-unread]');
    if(u && u.style.display !== 'none' && (u.textContent || '').trim() !== '') n++;
  });
  var right = document.getElementById('dlgHdrRight');
  var badge = right ? right.querySelector('.unread') : null;
  if(!badge && n > 0 && right){
    right.insertAdjacentHTML('afterbegin', '<span class="unread"></span>');
    badge = right.querySelector('.unread');
  }
  if(badge){
    if(n > 0){ badge.style.display = ''; badge.textContent = n; }
    else badge.style.display = 'none';
  }
}

async function syncDialogs(){
  try{
    var r = await fetch('/api/dialogs');
    if(!r.ok) return;
    var list = await r.json();
    var container = document.getElementById('clist');
    list.sort(function(a,b){ return (b.last_message_ts || 0) - (a.last_message_ts || 0); });
    list.forEach(function(d){
      var el = document.querySelector('.ci[data-id="'+d.id+'"]');
      if(!el){ el = buildDialogEl(d); }   // новый диалог — создаём строку
      if(!el) return;
      var prev = el.querySelector('[data-prev]');
      var time = el.querySelector('[data-time]');
      var unread = el.querySelector('[data-unread]');
      if(prev && prev.textContent !== (d.last_message || 'Нет сообщений')){
        prev.textContent = d.last_message || 'Нет сообщений';
      }
      if(time){ time.dataset.ts = d.last_message_ts || 0; time.textContent = relTime(d.last_message_ts); }
      if(unread){
        if(d.id === activeId || !d.unread_count){
          unread.style.display = 'none';
          unread.textContent = '';
        } else {
          unread.style.display = '';
          unread.textContent = d.unread_count;
        }
      }
      if(container) container.appendChild(el);
    });
    colorizeAvatars();
    applyDialogFilter();
    refreshHdrUnread();
  } catch(e){}
}

function startPolling(){
  if(pollTimer) clearInterval(pollTimer);
  // подстраховка; основное обновление приходит мгновенно по событию crm:update (SSE)
  pollTimer = setInterval(function(){
    if(document.visibilityState !== 'visible') return;
    syncDialogs();
    if(activeId) syncMsgs(false);
  }, 4000);
}

// Мгновенное обновление переписки при новом сообщении (push из общего SSE-канала)
document.addEventListener('crm:update', function(){
  if(document.visibilityState !== 'visible') return;
  syncDialogs();
  if(activeId) syncMsgs(false);
});

async function loadQuickTemplates() {
    try {
        const r = await fetch('/api/chat_templates');
        const templates = await r.json();
        const container = document.getElementById('quickTemplates');
        if (!container) return;
        container.innerHTML = '';
        for (const t of templates) {
            const btn = document.createElement('button');
            btn.className = 'btn-sm btn-ghost';
            btn.textContent = t.name;
            btn.style.fontSize = '12px';
            btn.onclick = () => {
                const textarea = document.getElementById('rtxt');
                textarea.value = t.text;
                textarea.focus();
                autoGrow(textarea);
            };
            container.appendChild(btn);
        }
    } catch(e) { console.warn(e); }
}

// ── Эмодзи-пикер ──────────────────────────────────────────────────────────────
var EMOJIS = ["😊","😀","😁","😂","🤣","🙂","😉","😍","🥰","😘","😎","🤩","🥳","😌","😇","🤗","🤔","😅","😋","😴","😭","😢","😡","🥺","😬","🙄","👍","👎","👌","🙏","👏","🙌","💪","🤝","✌️","👋","🤍","❤️","🧡","💛","💚","💙","💜","🔥","✨","⭐","🎉","🎁","🌸","🌹","💐","💎","💆","💄","💅","✅","❌","📍","📞","💬","🕐","📅","☕"];
function buildEmoji(){
  var p=document.getElementById('emojiPanel'); if(!p || p.dataset.built) return;
  EMOJIS.forEach(function(e){
    var b=document.createElement('button'); b.type='button'; b.textContent=e;
    b.onclick=function(){ insertEmoji(e); };
    p.appendChild(b);
  });
  p.dataset.built='1';
}
function toggleEmoji(){
  buildEmoji();
  var p=document.getElementById('emojiPanel'); if(p) p.classList.toggle('open');
}
function insertEmoji(e){
  var ta=document.getElementById('rtxt'); if(!ta) return;
  var s=ta.selectionStart||0, en=ta.selectionEnd||0;
  ta.value = ta.value.slice(0,s) + e + ta.value.slice(en);
  var pos=s+e.length; ta.selectionStart=ta.selectionEnd=pos;
  ta.focus(); autoGrow(ta);
}

window.addEventListener('load', function(){
  initMsgState();
  colorizeAvatars();
  var cav=document.getElementById('chatAv'), cnm=document.getElementById('chatName');
  if(cav && cnm && cnm.textContent.trim()) cav.style.background = avColor(cnm.textContent.trim());
  formatDialogTimes();
  applyWallpaper();
  var msgs = document.getElementById('msgs');
  if(msgs){
    insertDateSeparators(msgs); scrollMsgsBottom(msgs);
    msgs.addEventListener('scroll', updateScrollFab);
  }
  // Перетаскивание файлов в окно чата
  var cw = document.getElementById('chatWin');
  if(cw){
    ['dragover','dragenter'].forEach(function(ev){
      cw.addEventListener(ev, function(e){ if(activeId){ e.preventDefault(); cw.classList.add('drag'); } });
    });
    cw.addEventListener('dragleave', function(e){ if(e.target === cw) cw.classList.remove('drag'); });
    cw.addEventListener('drop', function(e){
      cw.classList.remove('drag');
      if(!activeId) return;
      e.preventDefault();
      if(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length){ addFiles(e.dataTransfer.files); }
    });
  }
  if(activeId){
    history.replaceState({chatId: activeId}, '', '/chats/' + activeId);
  } else {
    history.replaceState({chatId: null}, '', '/chats');
  }
  if(window.innerWidth < 768){
    if(activeId){
      document.getElementById('clientList').classList.add('mob-hidden');
      document.getElementById('chatWin').classList.remove('mob-hidden');
      document.body.classList.add('chat-open');
    } else {
      document.getElementById('clientList').classList.remove('mob-hidden');
      document.getElementById('chatWin').classList.add('mob-hidden');
      document.body.classList.remove('chat-open');
    }
  }
  startPolling();
  loadQuickTemplates();
});

// Вставка изображения из буфера (Ctrl+V) прямо в чат
document.addEventListener('paste', function(e){
  if(!activeId || !e.clipboardData) return;
  var items = e.clipboardData.items || [], imgs = [];
  for(var i = 0; i < items.length; i++){
    if(items[i].type && items[i].type.indexOf('image/') === 0){
      var f = items[i].getAsFile(); if(f) imgs.push(f);
    }
  }
  if(imgs.length){ e.preventDefault(); addFiles(imgs); showToast('Изображение добавлено', 'ok'); }
});

// Горячие клавиши: Ctrl/⌘+K — поиск по диалогам, Esc — закрыть поиск/панель/чат
document.addEventListener('keydown', function(e){
  if((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')){
    var s = document.getElementById('srch'); if(s){ e.preventDefault(); s.focus(); s.select(); }
    return;
  }
  if(e.key === 'Escape'){
    var lb = document.getElementById('lightbox');
    if(lb && lb.classList.contains('open')){ closeLightbox(); return; }
    var bar = document.getElementById('chatSearchBar');
    if(bar && bar.style.display !== 'none' && bar.style.display){ toggleChatSearch(); return; }
    var ep = document.getElementById('emojiPanel');
    if(ep && ep.classList.contains('open')){ ep.classList.remove('open'); return; }
    if(window.innerWidth < 768 && document.body.classList.contains('chat-open')){ backToList(); }
  }
});

window.addEventListener('popstate', function(e){
  if(e.state && e.state.chatId){
    activeId = e.state.chatId;
    loadChat(e.state.chatId);
    document.querySelectorAll('.ci').forEach(function(el){
      el.classList.toggle('active', parseInt(el.dataset.id, 10) === e.state.chatId);
    });
    if(window.innerWidth < 768){
      document.getElementById('clientList').classList.add('mob-hidden');
      document.getElementById('chatWin').classList.remove('mob-hidden');
      document.body.classList.add('chat-open');
    }
  } else {
    activeId = null;
    document.body.classList.remove('chat-open');
    document.getElementById('clientList').classList.remove('mob-hidden');
    document.getElementById('chatWin').classList.add('mob-hidden');
    document.getElementById('chatPanel').style.display = 'none';
    document.getElementById('chatPlaceholder').style.display = '';
    document.querySelectorAll('.ci').forEach(function(el){ el.classList.remove('active'); });
  }
});

document.addEventListener('visibilitychange', function(){
  if(document.visibilityState === 'visible'){
    syncDialogs();
    if(activeId) syncMsgs(false);
  }
});
</script>
"""


@app.route("/chats")
@app.route("/chats/<int:client_id>")
@require_auth
def chats(client_id=None):
    clients = db.get_all_clients()
    active_client = None
    messages = []
    if client_id:
        active_client = db.get_client(client_id)
        messages = db.get_messages(client_id)
        db.mark_messages_read(client_id)
    # бейдж у колокольчика = число непрочитанных ДИАЛОГОВ (активный уже помечен прочитанным выше)
    total_unread = db.get_unread_summary()["dialogs"]
    return render(CHATS_TPL, title="Чаты", active="chats",
                  clients=clients, active_client=active_client,
                  messages=messages, active_id=client_id,
                  total_unread=total_unread)


# ── Клиенты ────────────────────────────────────────────────────────────────────

CLIENTS_TPL = """
<div class="scroll-page">
  <div class="page-hdr">
    <h2><i class="ti ti-users"></i> Клиенты <span style="font-size:13px;color:var(--muted);font-weight:400">({{clients|length}})</span></h2>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-ghost" onclick="openModal('catModal')" style="font-size:12px"><i class="ti ti-tag"></i> Категории</button>
      <a class="btn btn-ghost" href="/clients/export" style="font-size:12px"><i class="ti ti-download"></i> Экспорт CSV</a>
    </div>
  </div>

  <div style="margin-bottom:12px">
    <input id="cliSearch" placeholder="Поиск по имени или телефону…" oninput="searchClients(this.value)"
           style="width:100%;max-width:440px">
  </div>

  <div style="margin-bottom:16px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <span style="font-size:12px;color:var(--muted);font-weight:600">Фильтр:</span>
    <button class="btn btn-sm" id="fAll" onclick="filterByCat(0,this)" style="background:var(--pink)">Все</button>
    {% for cat in categories %}
    <button class="btn btn-sm" id="fCat{{cat.id}}" onclick="filterByCat({{cat.id}},this)"
      style="background:{{cat.color}}">{{cat.name}}</button>
    {% endfor %}
  </div>

  <div class="tbl-wrap">
    <table class="cli-tbl">
      <thead>
        <tr>
          <th>Клиент</th><th>Телефон</th><th>Дата рождения</th><th>Потрачено</th><th>Категории</th><th>Зарегистрирован</th><th>Действия</th>
        </tr>
      </thead>
      <tbody id="clientTbody">
      {% for c in clients %}
      <tr class="cli-row" data-cats="{% for cat in c.categories %}{{cat.id}} {% endfor %}" data-q="{{ display_name(c)|lower }} {{ c.phone or '' }} {{ (c.username or '')|lower }}"
          data-id="{{c.id}}" data-name="{{ display_name(c)|e }}" data-phone="{{ (c.phone or '—')|e }}"
          data-user="{{ (c.username or '')|e }}"
          data-bot="{{ ((c.reg_first_name or c.first_name or '') ~ ' ' ~ (c.reg_last_name or c.last_name or ''))|trim|e }}"
          data-birth="{{ c.birth_date.strftime('%d.%m.%Y') if c.birth_date else '—' }}"
          data-reg="{{ c.created_at.strftime('%d.%m.%Y') if c.created_at else '—' }}"
          data-first="{{(c.reg_first_name or c.first_name)|e}}" data-last="{{(c.reg_last_name or c.last_name)|e}}"
          data-patron="{{(c.reg_patronymic or c.patronymic)|e}}" data-notes="{{c.notes|e}}"
          onclick="openClientDetails(this)">
        <td>
          <div style="display:flex;align-items:center;gap:10px">
            <div class="av" style="width:32px;height:32px;font-size:12px;flex-shrink:0">{{(c.first_name or c.last_name or '?')[0]|upper}}</div>
            <div>
              <a id="cname{{c.id}}" class="cname-link" title="ФИО из YClients (по номеру)" style="font-weight:600;text-decoration:none;color:var(--text);cursor:pointer">{{ display_name(c) }}</a><span class="cli-botname" id="botname{{c.id}}" data-bn="{{ display_name(c)|e }}" style="display:none;font-size:12px;color:var(--muted);font-weight:400"> ({{ display_name(c)|e }})</span>
              <div style="font-size:11px;color:var(--muted)" title="Введено клиентом в боте">
                {% if c.username %}@{{c.username}} · {% endif %}{{ c.phone or '' }}
              </div>
            </div>
          </div>
        </td>
        <td class="col-hide-m" style="white-space:nowrap">{{c.phone or '—'}}</td>
        <td class="bd-cell col-hide-m" data-client="{{c.id}}" style="color:var(--muted);font-size:12px;white-space:nowrap">{{ c.birth_date.strftime('%d.%m.%Y') if c.birth_date else '—' }}</td>
        <td class="spent-cell col-hide-m" data-client="{{c.id}}" data-phone="{{c.phone or ''}}" style="white-space:nowrap;font-size:13px">{% if c.phone %}<span style="color:var(--muted)">…</span>{% else %}—{% endif %}</td>
        <td>
          <div style="display:flex;flex-wrap:wrap;gap:2px">
            <span class="vip-tag" data-client="{{c.id}}" style="display:none"></span>
            {% for cat in c.categories %}
            <span class="tag" style="background:{{cat.color}}">{{cat.name}}</span>
            {% endfor %}
          </div>
        </td>
        <td class="col-hide-m" style="color:var(--muted);font-size:12px;white-space:nowrap">{{c.created_at.strftime('%d.%m.%Y') if c.created_at else '—'}}</td>
        <td data-actions>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn-sm" onclick="event.stopPropagation();editClient({{c.id}},'{{(c.reg_first_name or c.first_name)|e}}','{{(c.reg_last_name or c.last_name)|e}}','{{(c.reg_patronymic or c.patronymic)|e}}','{{c.phone|e}}','{{c.notes|e}}')"><i class="ti ti-edit"></i></button>
            <button class="btn-sm" onclick="event.stopPropagation();editCategories({{c.id}},'{{(c.reg_first_name or c.first_name)|e}}')"><i class="ti ti-tag"></i></button>
            <a href="/chats/{{c.id}}" class="btn-sm" onclick="event.stopPropagation()"><i class="ti ti-message-circle"></i></a>
          </div>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<!-- Модал деталей клиента (тап по карточке) -->
<div id="cliDetailModal" class="modal">
  <div class="modal-box">
    <div class="modal-head"><h3 id="cdName">Клиент</h3><button class="modal-close" onclick="closeModal('cliDetailModal')">×</button></div>
    <div id="cdBody"></div>
    <div style="display:flex;gap:8px;margin-top:16px;flex-wrap:wrap">
      <button class="btn btn-primary" style="flex:1;justify-content:center" onclick="cdEdit()"><i class="ti ti-edit"></i> Изменить</button>
      <button class="btn btn-ghost" style="flex:1;justify-content:center" onclick="cdCats()"><i class="ti ti-tag"></i> Категории</button>
      <a id="cdChat" class="btn btn-ghost" style="flex:1;justify-content:center" href="#"><i class="ti ti-message-circle"></i> Чат</a>
    </div>
  </div>
</div>

<!-- Модал категорий клиента -->
<div id="clientCatModal" class="modal">
  <div class="modal-box">
    <div class="modal-head">
      <h3 id="ccTitle">Категории клиента</h3>
      <button class="modal-close" onclick="closeModal('clientCatModal')">×</button>
    </div>
    <div id="ccBody"></div>
    <button class="btn btn-primary" style="width:100%;justify-content:center;margin-top:16px" onclick="saveCats()">Сохранить</button>
  </div>
</div>

<!-- Модал управления категориями -->
<div id="catModal" class="modal">
  <div class="modal-box">
    <div class="modal-head"><h3><i class="ti ti-tag"></i> Категории</h3><button class="modal-close" onclick="closeModal('catModal')">×</button></div>
    <div id="catList">
      {% for cat in categories %}
      <div style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--border)" id="catRow{{cat.id}}">
        <span class="tag" style="background:{{cat.color}}">{{cat.name}}</span>
        <span style="flex:1;color:var(--muted);font-size:12px">{{cat.color}}</span>
        {% if cat.protected %}
        <span style="color:var(--muted);font-size:14px" title="Системная категория — удалить нельзя, можно ставить вручную"><i class="ti ti-lock"></i></span>
        {% else %}
        <button class="btn-sm btn-danger" onclick="deleteCat({{cat.id}})"><i class="ti ti-x"></i></button>
        {% endif %}
      </div>
      {% endfor %}
    </div>
    <div style="margin-top:16px;display:flex;flex-direction:column;gap:10px">
      <div class="form-row"><label>Название</label><input id="newCatName" placeholder="Например: VIP"></div>
      <div class="form-row">
        <label>Цвет</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input type="color" id="newCatColor" value="#c06090">
          <span style="font-size:12px;color:var(--muted)">Выберите цвет метки</span>
        </div>
      </div>
      <button class="btn btn-primary" onclick="addCat()">+ Добавить категорию</button>
    </div>
  </div>
</div>

<!-- Модал редактирования клиента -->
<div id="clientEditModal" class="modal">
  <div class="modal-box">
    <div class="modal-head">
      <h3><i class="ti ti-edit"></i> Редактировать клиента</h3>
      <button class="modal-close" onclick="closeModal('clientEditModal')">×</button>
    </div>
    <input type="hidden" id="editClientId">
    <div class="form-row"><label>Фамилия</label><input id="editLastName" placeholder="Иванова"></div>
    <div class="form-row"><label>Имя</label><input id="editFirstName" placeholder="Анна"></div>
    <div class="form-row"><label>Отчество</label><input id="editPatronymic" placeholder="Сергеевна"></div>
    <div class="form-row"><label>Телефон</label><input id="editPhone" placeholder="+79001234567"></div>
    <div class="form-row"><label>Примечания</label><textarea id="editNotes" rows="3" placeholder="Доп. информация..."></textarea></div>
    <button class="btn btn-primary" style="width:100%;justify-content:center" onclick="saveClientEdit()">Сохранить</button>
  </div>
</div>

<script>
var allCats = {{ categories_json|safe }};
var currentClientId = null;

// ── Данные YClients: кэш в браузере + тихое фоновое обновление ─────────────────
var ycCache = {};
var ycQueue = [];
var ycActive = 0;
var YC_MAX = 3;                 // не больше N одновременных запросов к YClients
var YC_TTL = 10 * 60 * 1000;    // насколько кэш считается свежим (10 минут)

function ycFmtMoney(v){
  try { return Math.round(v).toLocaleString('ru-RU') + ' ₽'; }
  catch(e){ return Math.round(v) + ' ₽'; }
}

function ycCacheGet(id){
  try { var raw = localStorage.getItem('yc_' + id); return raw ? JSON.parse(raw) : null; }
  catch(e){ return null; }
}
function ycCacheSet(id, data){
  try { localStorage.setItem('yc_' + id, JSON.stringify({data: data, ts: Date.now()})); }
  catch(e){}
}
function ycStale(entry){ return !entry || (Date.now() - entry.ts) > YC_TTL; }

function ycApply(id, data){
  var spent = document.querySelector('.spent-cell[data-client="'+id+'"]');
  var bd = document.querySelector('.bd-cell[data-client="'+id+'"]');
  var nameEl = document.getElementById('cname'+id);
  var vip = document.querySelector('.vip-tag[data-client="'+id+'"]');
  if(spent){
    if(data && data.found){
      spent.textContent = ycFmtMoney(data.total_paid || 0);
      spent.style.color = '';
    } else {
      spent.textContent = '—';
      spent.style.color = 'var(--muted)';
    }
  }
  // если дата рождения локально пустая — подставим из YClients
  if(bd && data && data.found && data.birth_date && bd.textContent.trim() === '—'){
    bd.textContent = data.birth_date;
  }
  // Автотег VIP (потрачено ≥ порога). Если категория VIP уже показана сервером — не дублируем.
  if(vip && data && data.found && data.is_vip){
    var wrap = vip.parentNode;
    var dup = wrap ? Array.prototype.some.call(wrap.querySelectorAll('.tag'), function(t){ return t.textContent.trim() === 'VIP'; }) : false;
    if(!dup){
      vip.textContent = 'VIP';
      vip.className = 'tag vip-tag';
      vip.style.background = '#b08d57';
      vip.style.display = '';
    }
  }
  // Главное — ФИО из YClients (по номеру); рядом в скобках — что клиент указал в боте.
  var bn = document.getElementById('botname' + id);
  if(nameEl && data && data.found && data.name){
    nameEl.textContent = data.name;
    if(data.url){
      nameEl.href = data.url;
      nameEl.target = '_blank';
      nameEl.rel = 'noopener';
      nameEl.title = 'Открыть клиента в базе YClients';
      nameEl.style.color = 'var(--accent)';
      nameEl.style.cursor = 'pointer';
    }
    if(bn){
      var botName = (bn.getAttribute('data-bn') || '').trim();
      bn.style.display = (botName && botName !== data.name.trim()) ? '' : 'none';
    }
  } else if(bn){
    bn.style.display = 'none';   // в YClients не найден — главное и так ботовское имя
  }
}

function ycPump(){
  while(ycActive < YC_MAX && ycQueue.length){
    var id = ycQueue.shift();
    ycActive++;
    (function(cid){
      fetch('/api/client/'+cid+'/yclients')
        .then(function(r){ return r.json(); })
        .then(function(data){
          var prev = ycCacheGet(cid);
          ycCacheSet(cid, data);
          ycCache[cid] = data;
          // экран обновляем, только если данные реально изменились — без мельтешения
          if(!prev || JSON.stringify(prev.data) !== JSON.stringify(data)){ ycApply(cid, data); }
        })
        .catch(function(){ if(!ycCacheGet(cid)){ ycApply(cid, {found:false}); } })
        .finally(function(){ ycActive--; ycPump(); });
    })(id);
  }
}

function ycRequest(id){
  if(ycQueue.indexOf(id) === -1){ ycQueue.push(id); ycPump(); }
}

var ycObserver = new IntersectionObserver(function(entries){
  entries.forEach(function(en){
    if(en.isIntersecting){
      var el = en.target;
      ycObserver.unobserve(el);
      if(el.dataset.phone){ ycRequest(parseInt(el.dataset.client, 10)); }
    }
  });
}, {rootMargin: '120px'});

function ycInit(){
  document.querySelectorAll('.spent-cell[data-phone]').forEach(function(el){
    if(!el.dataset.phone) return;
    var id = parseInt(el.dataset.client, 10);
    var cached = ycCacheGet(id);
    if(cached){ ycCache[id] = cached.data; ycApply(id, cached.data); }  // мгновенно из кэша — без «…»
    if(ycStale(cached)){ ycObserver.observe(el); }                      // обновим тихо в фоне
  });
}
if(document.readyState === 'loading'){ document.addEventListener('DOMContentLoaded', ycInit); }
else { ycInit(); }

var _cliCat = 0, _cliQ = '';
function applyCliFilter(){
  document.querySelectorAll('#clientTbody tr').forEach(function(r){
    var catOk = (_cliCat === 0) || (r.dataset.cats || '').trim().split(' ').includes(String(_cliCat));
    var qOk = (_cliQ === '') || (r.dataset.q || '').indexOf(_cliQ) !== -1;
    r.style.display = (catOk && qOk) ? '' : 'none';
  });
}
function filterByCat(catId, btn){
  _cliCat = catId;
  document.querySelectorAll('[id^=fCat],[id=fAll]').forEach(function(b){ b.style.background = 'var(--muted)'; });
  btn.style.background = 'var(--pink)';
  applyCliFilter();
}
function searchClients(v){
  _cliQ = (v || '').toLowerCase().trim();
  applyCliFilter();
}

// Тап по карточке клиента → модалка со всеми данными (в т.ч. скрытыми на мобилке).
var _cdTr = null;
function openClientDetails(tr){
  if(window.innerWidth >= 768) return;   // только на мобильной версии; на десктопе таблица и так всё показывает
  _cdTr = tr;
  var g = function(k){ return tr.getAttribute(k) || ''; };
  var nmEl = tr.querySelector('.cname-link');
  document.getElementById('cdName').textContent = (nmEl && nmEl.textContent.trim()) || g('data-name') || 'Клиент';
  var spentEl = tr.querySelector('.spent-cell');
  var spent = spentEl ? spentEl.textContent.trim() : '';
  if(spent === '…' || spent === '') spent = '—';
  var vipEl = tr.querySelector('.vip-tag');
  var vip = (vipEl && vipEl.style.display !== 'none') ? vipEl.textContent.trim() : '';
  var cats = [];
  tr.querySelectorAll('td .tag').forEach(function(t){ cats.push(t.textContent.trim()); });
  var row = function(label, val){
    return val ? '<div class="cd-row"><span class="cd-l">'+label+'</span><span class="cd-v">'+esc(val)+'</span></div>' : '';
  };
  document.getElementById('cdBody').innerHTML =
    row('Телефон', g('data-phone')) +
    (g('data-user') ? row('Telegram', '@'+g('data-user')) : '') +
    row('Имя из бота', g('data-bot')) +
    row('Дата рождения', g('data-birth')) +
    row('Потрачено', spent) +
    row('Зарегистрирован', g('data-reg')) +
    (vip ? row('Статус', vip) : '') +
    (cats.length ? row('Категории', cats.join(', ')) : '');
  document.getElementById('cdChat').href = '/chats/' + g('data-id');
  openModal('cliDetailModal');
}
function cdEdit(){
  if(!_cdTr) return; var g = function(k){ return _cdTr.getAttribute(k) || ''; };
  closeModal('cliDetailModal');
  editClient(g('data-id'), g('data-first'), g('data-last'), g('data-patron'),
             g('data-phone') === '—' ? '' : g('data-phone'), g('data-notes'));
}
function cdCats(){
  if(!_cdTr) return; var g = function(k){ return _cdTr.getAttribute(k) || ''; };
  closeModal('cliDetailModal');
  editCategories(g('data-id'), g('data-first'));
}

async function editCategories(clientId, name){
  currentClientId = clientId;
  document.getElementById('ccTitle').textContent = 'Категории: ' + name;
  var r = await fetch('/api/client/' + clientId + '/categories');
  var current = await r.json();
  var currentIds = current.map(function(c){ return c.id; });
  document.getElementById('ccBody').innerHTML = allCats.map(function(c){
    return '<label style="display:flex;align-items:center;gap:10px;padding:8px 0;cursor:pointer;border-bottom:1px solid var(--border)">' +
      '<input type="checkbox" value="' + c.id + '" ' + (currentIds.includes(c.id) ? 'checked' : '') + '>' +
      '<span class="tag" style="background:' + c.color + '">' + esc(c.name) + '</span></label>';
  }).join('') || '<div class="empty">Категорий нет. Создайте их выше.</div>';
  openModal('clientCatModal');
}

async function saveCats(){
  var ids = Array.from(document.querySelectorAll('#ccBody input:checked')).map(function(i){ return parseInt(i.value); });
  var r = await fetch('/api/client/' + currentClientId + '/categories', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({category_ids: ids})
  });
  var j = await r.json();
  if(j.ok){ showToast('Категории сохранены', 'ok'); closeModal('clientCatModal'); setTimeout(function(){ location.reload(); }, 800); }
  else showToast('Ошибка', 'err');
}

async function addCat(){
  var name = document.getElementById('newCatName').value.trim();
  var color = document.getElementById('newCatColor').value;
  if(!name){ showToast('Введите название', 'err'); return; }
  var r = await fetch('/api/categories', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, color: color})
  });
  var j = await r.json();
  if(j.ok){ showToast('Категория добавлена', 'ok'); setTimeout(function(){ location.reload(); }, 600); }
  else showToast('Ошибка: ' + j.error, 'err');
}

async function deleteCat(id){
  if(!confirm('Удалить категорию?')) return;
  var r = await fetch('/api/categories/' + id, {method: 'DELETE'});
  var j = await r.json();
  if(j.ok){ document.getElementById('catRow' + id).remove(); showToast('Удалено', 'ok'); }
  else showToast('Ошибка', 'err');
}

function editClient(id, firstName, lastName, patronymic, phone, notes) {
    document.getElementById('editClientId').value = id;
    document.getElementById('editLastName').value = lastName;
    document.getElementById('editFirstName').value = firstName;
    document.getElementById('editPatronymic').value = patronymic;
    document.getElementById('editPhone').value = phone;
    document.getElementById('editNotes').value = notes;
    openModal('clientEditModal');
}

async function saveClientEdit() {
    const id = document.getElementById('editClientId').value;
    const data = {
        first_name: document.getElementById('editFirstName').value,
        last_name: document.getElementById('editLastName').value,
        patronymic: document.getElementById('editPatronymic').value,
        phone: document.getElementById('editPhone').value,
        notes: document.getElementById('editNotes').value
    };
    const r = await fetch(`/api/client/${id}`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    });
    const j = await r.json();
    if (j.ok) {
        showToast('Клиент обновлён', 'ok');
        closeModal('clientEditModal');
        setTimeout(() => location.reload(), 800);
    } else {
        showToast('Ошибка: ' + j.error, 'err');
    }
}
</script>
"""


@app.route("/clients")
@require_auth
def clients():
    cl = db.get_all_clients()
    categories = db.get_all_categories()
    cats_json = json.dumps([{"id": c["id"], "name": c["name"], "color": c["color"]} for c in categories])
    return render(CLIENTS_TPL, title="Клиенты", active="clients",
                  clients=cl, categories=categories, categories_json=cats_json)


@app.route("/clients/export")
@require_auth
def clients_export():
    import csv
    import io
    rows = db.get_all_clients()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Фамилия", "Имя", "Отчество", "Телефон", "Username", "Заметки", "Создан"])
    for c in rows:
        created = c.get("created_at")
        w.writerow([
            c.get("last_name", "") or "",
            c.get("first_name", "") or "",
            c.get("patronymic", "") or "",
            c.get("phone", "") or "",
            c.get("username", "") or "",
            (c.get("notes", "") or "").replace("\n", " "),
            created.strftime("%d.%m.%Y") if created else "",
        ])
    # BOM (﻿), чтобы Excel правильно открыл кириллицу.
    data = "﻿" + buf.getvalue()
    return Response(
        data, mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=clients.csv"},
    )


# ── Рассылка (напоминания о визите + поздравления с ДР) ───────────────────────

BROADCAST_TPL = """
{{ keys_cheatsheet|safe }}
{{ emoji_picker|safe }}
<div class="scroll-page">
  <div class="page-hdr">
    <h2><i class="ti ti-speakerphone"></i> Рассылка</h2>
  </div>
  <p style="color:var(--text-sec);margin-bottom:20px;font-size:13px;max-width:720px">
    В тексте можно использовать подстановки (заменяются автоматически):
    <code class="ph">{ИМЯ}</code> <code class="ph">{ФИО}</code>
    <code class="ph">{ДАТА}</code> <code class="ph">{ВРЕМЯ}</code> <code class="ph">{ВРАЧ}</code>
  </p>

  {% if reminder %}
  <div class="bc-card">
    <div class="bc-head">
      <div>
        <div class="bc-title"><i class="ti ti-clock"></i> {{ reminder.label }}</div>
        <div class="bc-hint">Бот раз в час берёт записи на завтра из YClients и за сутки отправляет это сообщение с кнопками «Подтвердить/Отменить».<br>{{ reminder.hint }}</div>
      </div>
      <button class="btn btn-ghost btn-sm" onclick="resetTpl('reminder')"><i class="ti ti-arrow-back-up"></i> Сбросить</button>
    </div>
    <textarea id="tpl_reminder" rows="6" style="width:100%;font-family:inherit;font-size:13px;line-height:1.5">{{ reminder.text }}</textarea>
    <button class="btn btn-primary" style="margin-top:10px" onclick="saveTpl('reminder')">Сохранить</button>
  </div>
  {% endif %}

  {% if birthday %}
  <div class="bc-card">
    <div class="bc-head">
      <div>
        <div class="bc-title"><i class="ti ti-cake"></i> {{ birthday.label }}</div>
        <div class="bc-hint">Автоматическое поздравление в день рождения клиента (дата рождения из анкеты).<br>{{ birthday.hint }}</div>
      </div>
      <label class="switch" title="Автоотправка поздравлений">
        <input type="checkbox" id="bdayToggle" {% if birthday_enabled %}checked{% endif %} onchange="toggleBirthday(this)">
        <span class="slider"></span>
      </label>
    </div>
    <div style="margin:2px 0 12px;font-size:12px;color:var(--text-sec)">
      Автоотправка: <b id="bdayState">{% if birthday_enabled %}включена{% else %}выключена{% endif %}</b>
    </div>
    <textarea id="tpl_birthday" rows="5" style="width:100%;font-family:inherit;font-size:13px;line-height:1.5">{{ birthday.text }}</textarea>
    <div style="margin-top:10px;display:flex;gap:8px">
      <button class="btn btn-primary" onclick="saveTpl('birthday')">Сохранить</button>
      <button class="btn btn-ghost" onclick="resetTpl('birthday')"><i class="ti ti-arrow-back-up"></i> Сбросить</button>
    </div>
  </div>
  {% endif %}
</div>

<style>
.ph{background:var(--hover);padding:2px 6px;border-radius:4px}
.bc-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:16px;box-shadow:var(--shadow)}
.bc-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px;flex-wrap:wrap}
.bc-title{font-weight:600;font-size:15px}
.bc-hint{font-size:11px;color:var(--text-sec);margin-top:4px;line-height:1.5}
.switch{position:relative;display:inline-block;width:46px;height:26px;flex-shrink:0}
.switch input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;inset:0;background:var(--border);transition:.3s;border-radius:26px}
.slider:before{content:"";position:absolute;height:20px;width:20px;left:3px;top:3px;background:#fff;transition:.3s;border-radius:50%}
.switch input:checked + .slider{background:var(--green)}
.switch input:checked + .slider:before{transform:translateX(20px)}
</style>

<script>
var tplDefaults = {{ defaults_json|safe }};

async function saveTpl(key){
  var text = document.getElementById('tpl_' + key).value;
  var r = await fetch('/api/templates/' + key, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: text})
  });
  var j = await r.json();
  if(j.ok) showToast('Шаблон сохранён', 'ok');
  else showToast('Ошибка: ' + (j.error || 'неизвестная'), 'err');
}

function resetTpl(key){
  if(!confirm('Сбросить текст к значению по умолчанию?')) return;
  document.getElementById('tpl_' + key).value = tplDefaults[key] || '';
}

async function toggleBirthday(el){
  var enabled = el.checked;
  var r = await fetch('/api/settings/birthday_enabled', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled: enabled})
  });
  var j = await r.json();
  if(j.ok){
    document.getElementById('bdayState').textContent = enabled ? 'включена' : 'выключена';
    showToast(enabled ? 'Поздравления включены' : 'Поздравления выключены', 'ok');
  } else {
    el.checked = !enabled;
    showToast('Ошибка сохранения', 'err');
  }
}
</script>
"""


@app.route("/broadcast")
@require_auth
def broadcast_page():
    import json as _json
    all_t = {t["key"]: t for t in get_all_templates_for_ui()}
    reminder = all_t.get("reminder")
    birthday = all_t.get("birthday")
    defaults = {}
    if reminder:
        defaults["reminder"] = reminder["default"]
    if birthday:
        defaults["birthday"] = birthday["default"]
    return render(BROADCAST_TPL, title="Рассылка", active="broadcast",
                  reminder=reminder, birthday=birthday,
                  birthday_enabled=db.get_setting("birthday_enabled"),
                  keys_cheatsheet=_keys_cheatsheet_html(),
                  emoji_picker=_EMOJI_PICKER_HTML,
                  defaults_json=_json.dumps(defaults, ensure_ascii=False))


@app.route("/api/templates/<key>", methods=["PUT"])
@require_auth
def api_save_template(key):
    from templates import DEFAULT_TEMPLATES
    if key not in DEFAULT_TEMPLATES:
        return jsonify({"ok": False, "error": "неизвестный шаблон"})
    data = request.json or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "пустой текст"})
    db.save_message_template(key, text)
    return jsonify({"ok": True})


@app.route("/api/settings/birthday_enabled", methods=["POST"])
@require_auth
def api_set_birthday_enabled():
    data = request.json or {}
    db.set_setting("birthday_enabled", bool(data.get("enabled")))
    return jsonify({"ok": True})


# ── Шаблоны: все тексты, которые бот шлёт клиенту ─────────────────────────────

# Все тексты, которые бот реально отправляет клиенту (кроме reminder/birthday —
# те на странице «Рассылка»). Порядок = логика общения с клиентом.
BOT_TEXTS_ORDER = [
    "bot_welcome", "bot_after_phone",
    "bot_reg_start", "bot_reg_firstname", "bot_reg_patronymic",
    "bot_reg_birth", "bot_reg_done",
    "bot_profile_caption", "bot_contacts",
    "booking_created", "bot_confirm_first", "bot_confirm_repeat",
]

BOT_TEXTS_TPL = """
{{ keys_cheatsheet|safe }}
{{ emoji_picker|safe }}
<div class="scroll-page">
  <div class="page-hdr"><h2><i class="ti ti-template"></i> Шаблоны сообщений бота</h2></div>
  <p style="color:var(--text-sec);margin-bottom:20px;font-size:13px;max-width:760px">
    Здесь редактируются все тексты, которые бот отправляет клиенту. Подстановки в
    фигурных скобках заменяются автоматически — полный список по кнопке
    «Ключи» справа сверху. Текст между *звёздочками* станет <b>жирным</b>,
    а премиум-эмодзи вставляются ключом вида {эмодзи:сердце}.
  </p>
  {% for t in items %}
  <div class="bt-card">
    <div class="bt-head">
      <div>
        <div class="bt-title">{{ t.label }}</div>
        <div class="bt-hint">{{ t.hint }}</div>
      </div>
      <button class="btn btn-ghost btn-sm" onclick="resetTpl('{{ t.key }}')"><i class="ti ti-arrow-back-up"></i> Сбросить</button>
    </div>
    <textarea id="tpl_{{ t.key }}" rows="5" style="width:100%;font-family:inherit;font-size:13px;line-height:1.5">{{ t.text }}</textarea>
    <button class="btn btn-primary" style="margin-top:10px" onclick="saveTpl('{{ t.key }}')">Сохранить</button>
  </div>
  {% endfor %}
</div>

<style>
.bt-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:16px;box-shadow:var(--shadow)}
.bt-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px;flex-wrap:wrap}
.bt-title{font-weight:600;font-size:15px}
.bt-hint{font-size:11px;color:var(--text-sec);margin-top:4px;line-height:1.5}
</style>

<script>
var tplDefaults = {{ defaults_json|safe }};
async function saveTpl(key){
  var text = document.getElementById('tpl_' + key).value;
  var r = await fetch('/api/templates/' + key, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: text})
  });
  var j = await r.json();
  if(j.ok) showToast('Сохранено', 'ok');
  else showToast('Ошибка: ' + (j.error || 'неизвестная'), 'err');
}
function resetTpl(key){
  if(!confirm('Сбросить текст к значению по умолчанию?')) return;
  document.getElementById('tpl_' + key).value = tplDefaults[key] || '';
}
</script>
"""


@app.route("/emoji_pack/<path:fname>")
@require_auth
def emoji_pack_file(fname):
    from flask import send_from_directory
    return send_from_directory(os.path.join(BASE_DIR, "emoji_pack"), fname)


@app.route("/api/emoji_pack")
@require_auth
def api_emoji_pack():
    import json as _json
    p = os.path.join(BASE_DIR, "emoji_pack", "manifest.json")
    if not os.path.exists(p):
        return jsonify([])
    try:
        with open(p, encoding="utf-8") as f:
            return jsonify(_json.load(f))
    except Exception:
        return jsonify([])


@app.route("/templates")
@require_auth
def bot_texts_page():
    import json as _json
    by_key = {t["key"]: t for t in get_all_templates_for_ui()}
    items = [by_key[k] for k in BOT_TEXTS_ORDER if k in by_key]
    defaults = {t["key"]: t["default"] for t in items}
    return render(BOT_TEXTS_TPL, title="Шаблоны", active="templates",
                  items=items, keys_cheatsheet=_keys_cheatsheet_html(),
                  emoji_picker=_EMOJI_PICKER_HTML,
                  defaults_json=_json.dumps(defaults, ensure_ascii=False))


# ── Быстрые ответы (шаблоны чатов) ─────────────────────────────────────────────

CHAT_TEMPLATES_TPL = """
<div class="scroll-page">
  <div class="page-hdr">
    <h2><i class="ti ti-bolt"></i> Быстрые ответы (шаблоны для чата)</h2>
    <button class="btn btn-primary" onclick="openModal('addTemplateModal')">+ Добавить шаблон</button>
  </div>
  <p style="color:var(--text-sec); margin-bottom:20px; font-size:13px;">
    Шаблоны появятся в виде кнопок над полем ввода в чате. При нажатии текст подставляется в поле – вы можете его отредактировать и отправить.
  </p>

  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>Название</th>
          <th>Текст</th>
          <th>Действия</th>
        </tr>
      </thead>
      <tbody id="templatesList">
      {% for t in templates %}
      <tr id="tplRow{{t.id}}">
        <td style="font-weight:600;">{{ t.name }}</td>
        <td style="max-width:400px; white-space:pre-wrap;">{{ t.text[:100] }}{% if t.text|length > 100 %}…{% endif %}</td>
        <td>
          <button class="btn-sm" onclick="editTemplate({{t.id}}, '{{t.name|e}}', '{{t.text|e}}')"><i class="ti ti-edit"></i></button>
          <button class="btn-sm btn-danger" onclick="deleteTemplate({{t.id}})"><i class="ti ti-trash"></i></button>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<!-- Модальное окно добавления/редактирования -->
<div id="addTemplateModal" class="modal">
  <div class="modal-box">
    <div class="modal-head">
      <h3 id="templateModalTitle">Добавить шаблон</h3>
      <button class="modal-close" onclick="closeModal('addTemplateModal')">×</button>
    </div>
    <input type="hidden" id="editTemplateId">
    <div class="form-row"><label>Название (будет на кнопке)</label><input id="templateName" placeholder="Например: ПРИВЕТ"></div>
    <div class="form-row"><label>Текст сообщения</label><textarea id="templateText" rows="5" placeholder="Ваш текст…"></textarea></div>
    <button class="btn btn-primary" style="width:100%; justify-content:center" onclick="saveTemplate()">Сохранить</button>
  </div>
</div>

<script>
async function saveTemplate() {
    const id = document.getElementById('editTemplateId').value;
    const name = document.getElementById('templateName').value.trim();
    const text = document.getElementById('templateText').value;
    if (!name || !text) { showToast('Заполните название и текст', 'err'); return; }
    const url = id ? `/api/chat_templates/${id}` : '/api/chat_templates';
    const method = id ? 'PUT' : 'POST';
    const r = await fetch(url, {
        method: method,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name, text })
    });
    const j = await r.json();
    if (j.ok) {
        showToast('Шаблон сохранён', 'ok');
        closeModal('addTemplateModal');
        setTimeout(() => location.reload(), 600);
    } else {
        showToast('Ошибка: ' + (j.error || 'неизвестная'), 'err');
    }
}

function editTemplate(id, name, text) {
    document.getElementById('editTemplateId').value = id;
    document.getElementById('templateName').value = name;
    document.getElementById('templateText').value = text;
    document.getElementById('templateModalTitle').innerText = 'Редактировать шаблон';
    openModal('addTemplateModal');
}

async function deleteTemplate(id) {
    if (!confirm('Удалить шаблон?')) return;
    const r = await fetch(`/api/chat_templates/${id}`, { method: 'DELETE' });
    const j = await r.json();
    if (j.ok) {
        document.getElementById('tplRow' + id).remove();
        showToast('Удалено', 'ok');
    } else {
        showToast('Ошибка', 'err');
    }
}

document.addEventListener('DOMContentLoaded', function() {
    const modal = document.getElementById('addTemplateModal');
    if (modal) {
        modal.addEventListener('hidden.bs-modal', function() {
            document.getElementById('editTemplateId').value = '';
            document.getElementById('templateName').value = '';
            document.getElementById('templateText').value = '';
            document.getElementById('templateModalTitle').innerText = 'Добавить шаблон';
        });
    }
});
</script>
"""


@app.route("/chat_templates")
@require_auth
def chat_templates_page():
    templates = db.get_all_chat_templates()
    return render(CHAT_TEMPLATES_TPL, title="Быстрые ответы", active="chat_templates", templates=templates)


# ── API ────────────────────────────────────────────────────────────────────────

@app.route("/api/send/<int:client_id>", methods=["POST"])
@require_auth
def api_send(client_id):
    client = db.get_client(client_id)
    if not client:
        return jsonify({"ok": False, "error": "not found"})

    uploaded = request.files.get("file")
    if uploaded and uploaded.filename:
        text = (request.form.get("text") or "").strip()
        raw_name = secure_filename(uploaded.filename) or "file"
        ext = raw_name.rsplit(".", 1)[-1].lower() if "." in raw_name else "bin"
        if ext not in ALLOWED_EXTENSIONS:
            return jsonify({"ok": False, "error": "тип файла не поддерживается"})
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        local_path = os.path.join(UPLOAD_FOLDER, unique_name)
        uploaded.save(local_path)
        media_type = _media_kind(raw_name, uploaded.mimetype)
        display_text = text or {"photo": "📷 Фото", "video": "🎬 Видео", "document": f"📎 {raw_name}"}[media_type]
        tg_id = client.get("tg_id")
        if tg_id and tg_id > 0:
            try:
                _send_tg_media(tg_id, media_type, local_path, caption=text, filename=raw_name)
            except Exception as e:
                # Не дошло до Telegram — НЕ сохраняем в чат (иначе «в чате есть, а клиенту нет»),
                # и убираем осиротевший файл.
                app.logger.warning("api_send media fail (client %s): %s", client_id, e)
                try:
                    os.remove(local_path)
                except OSError:
                    pass
                return jsonify({"ok": False, "error": "Не отправлено — Telegram недоступен. Попробуйте ещё раз."})
            db.save_message(
                client_id, "out", display_text,
                media_type=media_type, media_filename=raw_name,
                media_local_path=unique_name, sent_by=session.get("admin_name"),
            )
            return jsonify({"ok": True})
        # У клиента нет Telegram — осознанно сохраняем только в CRM
        db.save_message(
            client_id, "out", display_text,
            media_type=media_type, media_filename=raw_name,
            media_local_path=unique_name, sent_by=session.get("admin_name"),
        )
        return jsonify({"ok": True, "warning": "У клиента нет Telegram — сохранено только в CRM"})

    data = request.json or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty"})

    tg_id = client.get("tg_id")
    if tg_id and tg_id > 0:
        try:
            _send_tg(tg_id, text)
        except Exception as e:
            # Не дошло до Telegram — НЕ сохраняем в чат, чтобы не было «в чате есть, а клиенту нет».
            app.logger.warning("api_send tg fail (client %s): %s", client_id, e)
            return jsonify({"ok": False, "error": "Не отправлено — Telegram недоступен. Попробуйте ещё раз."})
        db.save_message(client_id, "out", text, sent_by=session.get("admin_name"))
        return jsonify({"ok": True})
    # У клиента нет Telegram — осознанно сохраняем только в CRM
    db.save_message(client_id, "out", text, sent_by=session.get("admin_name"))
    return jsonify({"ok": True, "warning": "У клиента нет Telegram — сохранено только в CRM"})


@app.route("/api/dialogs")
@require_auth
def api_dialogs():
    clients = db.get_dialogs_light()
    out = []
    for c in clients:
        name = db.client_display_name(c) or "Клиент"
        out.append({
            "id": c["id"],
            "display_name": name,
            "letter": (name.strip()[:1] or "?").upper(),
            "q": " ".join([name, c.get("phone") or "", c.get("username") or ""]).lower(),
            "last_message": c.get("last_message") or "",
            "last_message_at": c["last_message_at"].strftime("%H:%M") if c.get("last_message_at") else "",
            "last_message_ts": c["last_message_at"].timestamp() if c.get("last_message_at") else 0,
            "unread_count": int(c.get("unread_count") or 0),
        })
    return jsonify(out)


def _realtime_payload():
    """Сводка для бейджа + данные последнего входящего (для всплывающего пуша)."""
    s = db.get_unread_summary()
    p = {"dialogs": s["dialogs"], "total": s["total"], "incoming_id": 0}
    last = db.get_last_incoming_message()
    if last:
        p["incoming_id"] = last["id"]
        p["client_id"] = last["client_id"]
        p["sender"] = db.client_display_name(last) or "Клиент"
        p["text"] = (last.get("text") or "").strip()[:90]
    return p


@app.route("/api/unread")
@require_auth
def api_unread():
    return jsonify(_realtime_payload())


@app.route("/api/chat/<int:client_id>")
@require_auth
def api_chat(client_id):
    client = db.get_client(client_id)
    if not client:
        return jsonify({"ok": False, "error": "not found"}), 404
    db.mark_messages_read(client_id)
    msgs = db.get_messages(client_id)
    return jsonify({
        "ok": True,
        "client": {
            "id": client["id"],
            "first_name": client.get("first_name") or "",
            "last_name": client.get("last_name") or "",
            "patronymic": client.get("patronymic") or "",
            "display_name": db.client_display_name(client),
            "phone": client.get("phone") or "",
        },
        "messages": [_message_to_json(m) for m in msgs],
    })


@app.route("/api/messages/<int:client_id>")
@require_auth
def api_messages(client_id):
    after = request.args.get("after", 0, type=int)
    db.mark_messages_read(client_id)
    if after:
        msgs = db.get_messages_since(client_id, after)
    else:
        msgs = db.get_messages(client_id)
    return jsonify([_message_to_json(m) for m in msgs])


def _media_to_mp3(src_path):
    """Конвертирует голосовое (.oga/opus) в mp3 через ffmpeg — иначе iPhone/Safari не играют.
    Возвращает имя нового файла или None (ffmpeg нет/ошибка → оставляем оригинал)."""
    import subprocess
    dst = os.path.splitext(src_path)[0] + ".mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src_path, "-vn", "-codec:a", "libmp3lame", "-q:a", "5", dst],
            check=True, capture_output=True, timeout=90,
        )
        try:
            os.remove(src_path)
        except OSError:
            pass
        return os.path.basename(dst)
    except Exception as e:
        app.logger.warning("ffmpeg→mp3 не удалось (%s): %s", os.path.basename(src_path), e)
        return None


@app.route("/api/media/<int:message_id>")
@require_auth
def api_media(message_id):
    msg = db.get_message(message_id)
    if not msg:
        return jsonify({"ok": False}), 404

    # Медиа конкретного сообщения не меняется — можно смело кэшировать в браузере.
    _MEDIA_CACHE = "private, max-age=2592000, immutable"

    # 1) Уже скачано на диск — отдаём из файла (быстро, без прокси и Telegram).
    if msg.get("media_local_path"):
        path = os.path.join(UPLOAD_FOLDER, msg["media_local_path"])
        if os.path.isfile(path):
            # inline: браузер сам покажет картинку/PDF во вкладке, остальное — скачает.
            r = send_file(path, as_attachment=False,
                          download_name=msg.get("media_filename") or "file")
            r.headers["Cache-Control"] = _MEDIA_CACHE
            return r

    # 2) Есть только file_id — качаем из Telegram ОДИН раз, кладём на диск, запоминаем
    #    путь в БД; следующие запросы пойдут веткой выше (с диска, мгновенно).
    if msg.get("media_file_id"):
        try:
            import mimetypes
            from urllib.parse import quote
            url = _tg_file_url(msg["media_file_id"])
            resp = _requests.get(url, timeout=30, proxies=_TG_PROXIES)
            if resp.status_code == 200:
                fname = msg.get("media_filename") or "file"
                ext = (os.path.splitext(url.split("?")[0])[1]
                       or os.path.splitext(fname)[1] or "")
                local_name = f"msg{message_id}{ext}"
                disk_path = os.path.join(UPLOAD_FOLDER, local_name)
                try:
                    with open(disk_path, "wb") as _f:
                        _f.write(resp.content)
                    # Голосовые Telegram (.oga/opus) → mp3, иначе iPhone/Safari не играют.
                    if msg.get("media_type") == "audio" and ext.lower() in (".oga", ".ogg", ".opus", ""):
                        mp3 = _media_to_mp3(disk_path)
                        if mp3:
                            local_name = mp3
                            disk_path = os.path.join(UPLOAD_FOLDER, local_name)
                    db.set_message_local_path(message_id, local_name)
                    r = send_file(disk_path, as_attachment=False,
                                  download_name=msg.get("media_filename") or "file")
                    r.headers["Cache-Control"] = _MEDIA_CACHE
                    return r
                except Exception as e:
                    app.logger.warning("media cache (msg %s): %s", message_id, e)
                # запасной путь: отдать прямо из памяти (если не удалось сохранить/сконвертировать)
                mimetype = (mimetypes.guess_type(fname)[0]
                            or resp.headers.get("Content-Type")
                            or "application/octet-stream")
                r = Response(resp.content, mimetype=mimetype)
                r.headers["Content-Disposition"] = "inline; filename*=UTF-8''" + quote(fname)
                r.headers["Cache-Control"] = _MEDIA_CACHE
                return r
        except Exception:
            pass

    return jsonify({"ok": False}), 404


@app.route("/api/client/<int:client_id>/categories", methods=["GET"])
@require_auth
def api_get_client_categories(client_id):
    cats = db.get_client_categories(client_id)
    return jsonify([{"id": c["id"], "name": c["name"], "color": c["color"]} for c in cats])


@app.route("/api/client/<int:client_id>/categories", methods=["POST"])
@require_auth
def api_set_client_categories(client_id):
    data = request.json or {}
    ids = data.get("category_ids", [])
    db.set_client_categories(client_id, ids)
    return jsonify({"ok": True})


@app.route("/api/categories", methods=["POST"])
@require_auth
def api_create_category():
    data = request.json or {}
    name = data.get("name", "").strip()
    color = data.get("color", "#c06090")
    if not name:
        return jsonify({"ok": False, "error": "empty name"})
    cat_id = db.create_category(name, color)
    if cat_id:
        return jsonify({"ok": True, "id": cat_id})
    return jsonify({"ok": False, "error": "already exists"})


@app.route("/api/categories/<int:cat_id>", methods=["DELETE"])
@require_auth
def api_delete_category(cat_id):
    ok = db.delete_category(cat_id)
    if not ok:
        return jsonify({"ok": False, "error": "Эту категорию нельзя удалить"})
    return jsonify({"ok": True})


# ── API для обновления клиента ─────────────────────────────────────────────────
@app.route("/api/client/<int:client_id>", methods=["PUT"])
@require_auth
def api_update_client(client_id):
    data = request.json or {}
    first_name = data.get("first_name", "").strip()
    last_name = data.get("last_name", "").strip()
    patronymic = data.get("patronymic", "").strip()
    phone = data.get("phone", "").strip()
    notes = data.get("notes", "").strip()

    if phone:
        phone = db.normalize_phone(phone)

    db.update_client(client_id, first_name, last_name, patronymic, phone, notes)
    return jsonify({"ok": True})


# ── API данных из YCLIENTS (только чтение) ─────────────────────────────────────

def _yclients_card(client):
    """Сводка YCLIENTS по клиенту для веба. {'found': False, ...} если нет данных."""
    phone = client.get("phone") if client else None
    if not phone:
        return {"found": False, "reason": "no_phone"}
    try:
        summary = asyncio.run(yclients.get_profile_summary(phone))
    except Exception as e:
        return {"found": False, "reason": "error", "error": str(e)}
    if not summary:
        return {"found": False, "reason": "not_found"}

    # Авто-VIP: только добавляем метку (ручной VIP и пометки админа не трогаем).
    if summary.get("is_vip") and client.get("id"):
        try:
            db.tag_client_vip(client["id"])
        except Exception:
            pass

    days_with_us = None
    fv = summary.get("first_visit")
    if fv:
        d = (date.today() - fv).days
        days_with_us = d if d >= 0 else 0

    cid = summary.get("client_id")
    query = "".join(ch for ch in str(phone) if ch.isdigit())
    url = ""
    if YCLIENTS_CLIENT_URL_TEMPLATE and YCLIENTS_COMPANY_ID and query:
        try:
            url = YCLIENTS_CLIENT_URL_TEMPLATE.format(
                company_id=YCLIENTS_COMPANY_ID, client_id=cid or "", query=query)
        except Exception:
            url = ""

    def _d(v):
        return v.strftime("%d.%m.%Y") if v else None

    return {
        "found": True,
        "client_id": cid,
        "url": url,
        "visits": summary.get("visits", 0),
        "total_paid": float(summary.get("total_paid") or 0),
        "bonus": float(summary.get("bonus") or 0),
        "is_vip": bool(summary.get("is_vip")),
        "last_visit": _d(summary.get("last_visit")),
        "first_visit": _d(summary.get("first_visit")),
        "nearest": _d(summary.get("nearest")),
        "birth_date": _d(summary.get("birth_date")),
        "days_with_us": days_with_us,
        "name": summary.get("name") or "",
    }


@app.route("/api/client/<int:client_id>/yclients")
@require_auth
def api_client_yclients(client_id):
    client = db.get_client(client_id)
    if not client:
        return jsonify({"found": False, "reason": "not found"}), 404
    return jsonify(_yclients_card(client))


@app.route("/api/client/<int:client_id>/card")
@require_auth
def api_client_card(client_id):
    client = db.get_client(client_id)
    if not client:
        return jsonify({"ok": False}), 404
    return jsonify({
        "ok": True,
        "name": db.client_display_name(client),
        "phone": client.get("phone") or "",
        "notes": client.get("notes") or "",
        "yclients": _yclients_card(client),
    })


@app.route("/api/client/<int:client_id>/notes", methods=["POST"])
@require_auth
def api_client_notes(client_id):
    data = request.json or {}
    notes = (data.get("notes") or "").strip()
    try:
        db.update_client_notes(client_id, notes)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── API для шаблонов чатов ────────────────────────────────────────────────────
@app.route("/api/chat_templates", methods=["GET"])
@require_auth
def api_get_chat_templates():
    templates = db.get_all_chat_templates()
    return jsonify(templates)


@app.route("/api/chat_templates", methods=["POST"])
@require_auth
def api_create_chat_template():
    data = request.json or {}
    name = data.get("name", "").strip()
    text = data.get("text", "").strip()
    if not name or not text:
        return jsonify({"ok": False, "error": "Название и текст обязательны"})
    tpl_id = db.create_chat_template(name, text)
    if tpl_id:
        return jsonify({"ok": True, "id": tpl_id})
    return jsonify({"ok": False, "error": "Ошибка БД"})


@app.route("/api/chat_templates/<int:tpl_id>", methods=["PUT"])
@require_auth
def api_update_chat_template(tpl_id):
    data = request.json or {}
    name = data.get("name", "").strip()
    text = data.get("text", "").strip()
    if not name or not text:
        return jsonify({"ok": False, "error": "Название и текст обязательны"})
    db.update_chat_template(tpl_id, name, text)
    return jsonify({"ok": True})


@app.route("/api/chat_templates/<int:tpl_id>", methods=["DELETE"])
@require_auth
def api_delete_chat_template(tpl_id):
    db.delete_chat_template(tpl_id)
    return jsonify({"ok": True})


# ── Запуск ─────────────────────────────────────────────────────────────────────

def run_web():
    # waitress — нормальный WSGI-сервер с пулом потоков: вечный SSE-поток
    # (/api/stream) занимает один поток, а переходы по страницам обслуживаются
    # остальными и не встают в очередь. Это убирает «зависания» dev-сервера.
    try:
        from waitress import serve
        serve(
            app,
            host=getattr(config, "WEB_HOST", "127.0.0.1"),
            port=WEB_PORT,
            threads=16,
            channel_timeout=300,
            ident="ReformCRM",
        )
    except ImportError:
        # запасной вариант, если waitress не установлен — встроенный сервер Flask
        app.run(host=getattr(config, "WEB_HOST", "127.0.0.1"), port=WEB_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run_web()