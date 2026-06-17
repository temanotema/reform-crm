"""
admin_web.py — веб-панель Re.form CRM.
"""

import asyncio
import json
import os
import time
import uuid
import requests as _requests
from datetime import date
from flask import Flask, render_template_string, request, redirect, session, jsonify, Response, send_file
from werkzeug.utils import secure_filename
import threading

import database as db
import yclients
from config import (
    ADMIN_PASSWORD, BOT_TOKEN, WEB_PORT, SECRET_KEY, CLINIC_NAME,
    YCLIENTS_COMPANY_ID, YCLIENTS_CLIENT_URL_TEMPLATE,
)
from templates import get_all_templates_for_ui

app = Flask(__name__)
app.secret_key = SECRET_KEY

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
        resp = _requests.post(url, data=data, files=files, timeout=60)
    else:
        resp = _requests.post(url, json=data, timeout=30)
    result = resp.json()
    if not result.get("ok"):
        raise Exception(result.get("description", "Telegram API error"))
    return result


def _send_tg(tg_id: int, text: str, reply_markup=None):
    payload = {"chat_id": tg_id, "text": text, "parse_mode": "Markdown"}
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
        "has_media": bool(m.get("media_type") or m.get("media_file_id") or m.get("media_local_path")),
    }


# ── Auth decorator ─────────────────────────────────────────────────────────────

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def dec(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/login")
        return f(*args, **kwargs)
    return dec


# ── Base template ─────────────────────────────────────────────────────────────

BASE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<script>(function(){document.documentElement.setAttribute('data-theme',localStorage.getItem('crm-theme')||'light');})();</script>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{{ title }} — {{ clinic }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root,[data-theme="light"]{
  --accent:#3390ec;--accent-h:#2b7cd3;--accent-soft:#dceaf8;
  --bg:#e4e7eb;--card:#eceef1;--sidebar-bg:#eceef1;--border:#d0d4da;
  --text:#1a1a1a;--text-sec:#5c6370;--sidebar:300px;
  --chat-bg:#d8dde3;--bubble-in:#f5f6f8;--bubble-out:#3390ec;
  --bubble-out-text:#fff;--bubble-in-border:#c8cdd4;--hover:#dfe2e6;--input-bg:#f0f1f3;
  --shadow:0 1px 2px rgba(0,0,0,.06);--shadow-lg:0 8px 32px rgba(0,0,0,.12);
  --green:#4fae4e;--red:#e53935;--orange:#e8a317;
  --overlay:rgba(0,0,0,.4);--scroll-thumb:#b8bdc4;
  --pink:var(--accent);--pink-d:var(--accent-h);--pink-l:var(--accent-soft);--pink-ll:var(--hover);
  --muted:var(--text-sec);
}
[data-theme="dark"]{
  --accent:#6ab3f3;--accent-h:#5aa0e0;--accent-soft:#1a3a5c;
  --bg:#0e1621;--card:#17212b;--sidebar-bg:#17212b;--border:#242f3d;
  --text:#f5f5f5;--text-sec:#708499;--chat-bg:#0e1621;
  --bubble-in:#182533;--bubble-out:#2b5278;--bubble-out-text:#fff;
  --hover:#1e2c3a;--input-bg:#242f3d;--shadow:0 1px 2px rgba(0,0,0,.25);
  --shadow-lg:0 8px 32px rgba(0,0,0,.45);--overlay:rgba(0,0,0,.6);
  --scroll-thumb:#3d4f63;
  --pink:var(--accent);--pink-d:var(--accent-h);--pink-l:var(--accent-soft);--pink-ll:var(--hover);
  --muted:var(--text-sec);
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:'Inter','Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);
  font-size:14px;transition:background .3s,color .3s}

/* ── Sidebar ── */
.sidebar{position:fixed;top:0;left:0;width:var(--sidebar);height:100vh;
  background:var(--sidebar-bg);border-right:1px solid var(--border);
  display:flex;flex-direction:column;z-index:200;transition:transform .3s ease,background .3s}
.sidebar .logo{padding:20px 18px 16px;font-weight:700;font-size:16px;
  color:var(--accent);border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:10px;letter-spacing:-.2px}
.nav{padding:10px 8px}
.nav a{display:flex;align-items:center;gap:12px;padding:11px 14px;margin:2px 0;
  color:var(--text-sec);text-decoration:none;font-weight:500;transition:all .2s ease;
  font-size:14px;border-radius:10px}
.nav a:hover{background:var(--hover);color:var(--text)}
.nav a.active{background:var(--accent-soft);color:var(--accent);font-weight:600}
.nav-unread{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;
  padding:0 5px;border-radius:10px;background:var(--red);color:#fff;font-size:11px;font-weight:700;line-height:1}
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
  justify-content:space-around;padding:6px 0 env(safe-area-inset-bottom,0);
  transition:background .3s}
.mob-bar a{display:flex;flex-direction:column;align-items:center;gap:2px;
  color:var(--text-sec);text-decoration:none;font-size:10px;padding:4px 8px;
  border-radius:8px;min-width:52px;text-align:center;transition:color .2s}
.mob-bar a.active{color:var(--accent)}
.mob-bar a span.icon{font-size:20px;line-height:1}

/* ── Hamburger ── */
.hamburger{display:none;position:fixed;top:12px;left:12px;z-index:400;
  background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:7px 10px;cursor:pointer;font-size:18px;line-height:1;color:var(--accent);
  box-shadow:var(--shadow);transition:all .2s}
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
.client-search input{width:100%;font-size:13px;background:var(--hover);border:none;border-radius:20px;padding:9px 14px}
.client-search input:focus{background:var(--input-bg);border:1px solid var(--accent);box-shadow:none}
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
.unread{background:var(--accent);color:#fff;border-radius:12px;min-width:20px;height:20px;
  padding:0 6px;font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}

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
.msg-wrap.msg-new{animation:msgIn .2s ease}
@keyframes msgIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.msg{padding:8px 12px 6px;border-radius:12px;line-height:1.45;word-break:break-word;font-size:14px;
  white-space:normal;position:relative;width:fit-content;max-width:100%;box-shadow:var(--shadow)}
.msg .mtext{white-space:pre-wrap}
.msg.in{background:var(--bubble-in);color:var(--text);border-radius:4px 12px 12px 12px;
  border:1px solid var(--bubble-in-border,#e0e4e8)}
[data-theme="dark"] .msg.in{border-color:var(--border)}
.msg.out{background:var(--bubble-out);color:var(--bubble-out-text);border-radius:12px 12px 4px 12px}
.msg.system{background:var(--hover);border:1px dashed var(--border);font-size:12px;
  color:var(--text-sec);text-align:center}
.msg .mtime{font-size:11px;opacity:.55;margin-top:4px;display:block;text-align:right}
.msg-media{margin-bottom:6px;border-radius:8px;overflow:hidden;max-width:280px;line-height:0}
.msg-media img,.msg-media video{display:block;max-width:280px;max-height:360px;width:auto;height:auto;
  border-radius:8px;cursor:pointer;object-fit:contain}
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
  padding:12px 20px;border-radius:12px;z-index:9999;font-size:13px;border:1px solid var(--border);
  box-shadow:var(--shadow-lg);opacity:0;transform:translateY(12px);transition:all .3s ease;
  pointer-events:none;max-width:calc(100vw - 48px)}
.toast.show{opacity:1;transform:translateY(0)}
.toast.ok{border-color:var(--green);background:rgba(79,174,78,.12)}
.toast.err{border-color:var(--red);background:rgba(229,57,53,.1)}

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
@media(max-width:767px){.push-stack{left:10px;right:10px;width:auto;bottom:72px}}

/* ── Misc ── */
.page-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:10px}
.page-hdr h2{font-size:20px;color:var(--text);font-weight:600}
.empty{padding:48px 24px;text-align:center;color:var(--text-sec);font-size:14px}
.scroll-page{flex:1;overflow-y:auto;padding:24px}
.login-card{background:var(--card)!important;box-shadow:var(--shadow-lg)!important}

/* ── Responsive ── */
@media(max-width:767px){
  .sidebar{transform:translateX(-100%)}
  .sidebar.mob-open{transform:translateX(0)}
  .hamburger{display:block}
  .main{margin-left:0;padding-top:52px;padding-bottom:60px}
  .mob-bar{display:flex}
  .chat-layout{flex-direction:column}
  .client-list{width:100%;height:100%;border-right:none;border-bottom:1px solid var(--border)}
  .chat-win{height:100%}
  .client-list.mob-hidden{display:none}
  .chat-win.mob-hidden{display:none}
  .back-btn{display:inline-flex!important}
  .msgs{padding:10px}
  .msg{max-width:88%}
  .scroll-page{padding:14px}
  .page-hdr h2{font-size:16px}
  table{min-width:420px}
}
@media(min-width:768px){
  .back-btn{display:none!important}
}
</style>
</head>
<body>
{% if session.get('admin') %}
<div class="sidebar-overlay" id="sideOverlay" onclick="closeSidebar()"></div>
<button class="hamburger" onclick="toggleSidebar()">☰</button>
<div class="sidebar" id="sidebar">
  <div class="logo">💆 {{ clinic }}</div>
  <nav class="nav">
    <a href="/chats"    class="{% if active=='chats'    %}active{% endif %}">💬 Чаты <span class="nav-unread" style="display:none"></span></a>
    <a href="/clients"  class="{% if active=='clients'  %}active{% endif %}">👥 Клиенты</a>
    <a href="/broadcast" class="{% if active=='broadcast' %}active{% endif %}">📢 Рассылка</a>
    <a href="/templates" class="{% if active=='templates' %}active{% endif %}">📝 Шаблоны</a>
    <a href="/chat_templates" class="{% if active=='chat_templates' %}active{% endif %}">⚡ Быстрые ответы</a>
  </nav>
  <div class="sidebar-foot">
    <button class="theme-btn" id="updateBtn" onclick="crmApplyUpdate()" style="display:none;color:var(--green)">
      <span>⬆️</span><span id="updateLabel">Обновить</span>
    </button>
    <button class="theme-btn" id="themeToggle" onclick="toggleTheme()">
      <span id="themeIcon">🌙</span><span id="themeLabel">Тёмная тема</span>
    </button>
    <a href="/logout">🚪 Выйти</a>
    <div id="appVersion" style="font-size:10px;color:var(--text-sec);padding:2px 10px"></div>
  </div>
</div>
<nav class="mob-bar">
  <a href="/chats"    class="{% if active=='chats'    %}active{% endif %}" style="position:relative"><span class="icon">💬</span>Чаты <span class="nav-unread" style="display:none"></span></a>
  <a href="/clients"  class="{% if active=='clients'  %}active{% endif %}"><span class="icon">👥</span>Клиенты</a>
  <a href="/broadcast" class="{% if active=='broadcast' %}active{% endif %}"><span class="icon">📢</span>Рассылка</a>
  <a href="/templates" class="{% if active=='templates' %}active{% endif %}"><span class="icon">📝</span>Шаблоны</a>
  <a href="/chat_templates" class="{% if active=='chat_templates' %}active{% endif %}"><span class="icon">⚡</span>Быстрые</a>
</nav>
<div class="main">{{ body|safe }}</div>
{% else %}
{{ body|safe }}
{% endif %}
<div id="toast" class="toast"></div>
<div class="push-stack" id="pushStack"></div>
<script>
function applyTheme(theme){
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('crm-theme', theme);
  var icon = document.getElementById('themeIcon');
  var label = document.getElementById('themeLabel');
  if(icon) icon.textContent = theme === 'dark' ? '☀️' : '🌙';
  if(label) label.textContent = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема';
}
function toggleTheme(){
  var cur = document.documentElement.getAttribute('data-theme') || 'light';
  applyTheme(cur === 'dark' ? 'light' : 'dark');
}
applyTheme(localStorage.getItem('crm-theme') || 'light');
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
    return '<div class="msg-media"><img src="'+url+'" alt="фото" onclick="window.open(this.src)"></div>';
  }
  if(m.media_type === 'video'){
    return '<div class="msg-media"><video src="'+url+'" controls preload="metadata"></video></div>';
  }
  var name = m.media_filename || 'Файл';
  return '<a class="msg-file" href="'+url+'" target="_blank" download>' +
    '<span class="file-icon">📎</span><span class="file-name">'+esc(name)+'</span></a>';
}
function renderMsg(m, animate){
  var cls = m.direction === 'system' ? 'system' : m.direction;
  var anim = animate ? ' msg-new' : '';
  return '<div class="msg-wrap '+cls+anim+'" data-msg-id="'+m.id+'"><div class="msg '+cls+'">' +
    renderMsgMedia(m) + (m.text ? '<span class="mtext">'+esc(m.text)+'</span>' : '') +
    '<span class="mtime">'+m.created_at+'</span></div></div>';
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
    if(!crmAudioCtx){ crmAudioCtx = new (window.AudioContext||window.webkitAudioContext)(); }
    if(crmAudioCtx.state === 'suspended'){ crmAudioCtx.resume(); }
    var o=crmAudioCtx.createOscillator(), g=crmAudioCtx.createGain();
    o.connect(g); g.connect(crmAudioCtx.destination); o.type='sine'; o.frequency.value=880;
    var t=crmAudioCtx.currentTime;
    g.gain.setValueAtTime(0.0001,t);
    g.gain.exponentialRampToValueAtTime(0.18,t+0.02);
    g.gain.exponentialRampToValueAtTime(0.0001,t+0.35);
    o.start(t); o.stop(t+0.37);
  }catch(e){}
}

function crmBadge(dialogs){
  document.querySelectorAll('.nav-unread').forEach(function(b){
    if(dialogs>0){ b.textContent=dialogs; b.style.display=''; } else { b.style.display='none'; b.textContent=''; }
  });
  var base=document.title.replace(/^\(\d+\)\s*/,'');
  document.title = dialogs>0 ? '('+dialogs+') '+base : base;
}

function crmNotify(p){
  try{
    if(('Notification' in window) && Notification.permission==='granted' && document.visibilityState!=='visible'){
      var n=new Notification((p&&p.sender)||'Новое сообщение', {body:(p&&p.text)||'Новое сообщение в CRM'});
      n.onclick=function(){ window.focus(); if(p&&p.client_id) location.href='/chats/'+p.client_id; n.close(); };
    }
  }catch(e){}
}

// Всплывающая карточка-пуш внутри панели (видна на любой странице, гаснет через 4с)
function crmPush(p){
  var stack=document.getElementById('pushStack'); if(!stack) return;
  var card=document.createElement('div'); card.className='push-card';
  card.innerHTML='<div class="pf">💬 '+esc((p&&p.sender)||'Клиент')+'</div>'+
                 '<div class="pt">'+esc((p&&p.text)||'Новое сообщение')+'</div>';
  card.onclick=function(){ if(p&&p.client_id) location.href='/chats/'+p.client_id; };
  stack.appendChild(card);
  setTimeout(function(){ card.classList.add('hide'); setTimeout(function(){ card.remove(); }, 320); }, 4000);
}

function crmHandle(p){
  if(!p) return;
  crmGotData = true;
  crmBadge(p.dialogs||0);
  if(crmLastIncoming!==null && p.incoming_id>crmLastIncoming){
    crmDing();
    crmNotify(p);
    if(!(window.activeId && window.activeId === p.client_id)){ crmPush(p); }
  }
  crmLastIncoming = p.incoming_id;
  document.dispatchEvent(new CustomEvent('crm:update', {detail:p}));
}

function crmPoll(){
  setInterval(function(){
    fetch('/api/unread').then(function(r){return r.json();}).then(crmHandle).catch(function(){});
  }, 2500);
}

function crmRealtime(){
  if(!CRM_AUTHED) return;
  if(('Notification' in window) && Notification.permission==='default'){
    document.addEventListener('click', function(){ try{Notification.requestPermission();}catch(e){} }, {once:true});
  }
  if(typeof EventSource!=='undefined'){
    try{
      var es=new EventSource('/api/stream');
      es.addEventListener('update', function(e){ try{ crmHandle(JSON.parse(e.data)); }catch(err){} });
      es.onerror=function(){ crmErr++; if(!crmGotData && crmErr>=2){ try{es.close();}catch(_){ } crmPoll(); } };
      return;
    }catch(e){}
  }
  crmPoll();
}
crmRealtime();

function crmCheckUpdate(){
  if(!CRM_AUTHED) return;
  fetch('/api/update/check').then(function(r){return r.json();}).then(function(d){
    var ver=document.getElementById('appVersion');
    if(ver && d && d.current) ver.textContent='версия '+d.current;
    if(d && d.available){
      var b=document.getElementById('updateBtn'), l=document.getElementById('updateLabel');
      if(l) l.textContent='Обновить до v'+d.latest;
      if(b) b.style.display='';
    }
  }).catch(function(){});
}
function crmApplyUpdate(){
  if(!confirm('Скачать и установить обновление? Приложение закроется для установки.')) return;
  showToast('Загрузка обновления…','');
  fetch('/api/update/apply',{method:'POST'}).then(function(r){return r.json();}).then(function(j){
    if(j.ok) showToast('Установщик запущен — следуйте инструкциям.','ok');
    else showToast('Ошибка обновления: '+(j.error||'неизвестно'),'err');
  }).catch(function(){ showToast('Ошибка сети','err'); });
}
crmCheckUpdate();
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
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);padding:16px;position:relative">
  <button class="theme-btn" onclick="toggleTheme()" style="position:absolute;top:16px;right:16px;width:auto;padding:8px 14px;border:1px solid var(--border);border-radius:10px">
    <span id="themeIcon">🌙</span>
  </button>
  <div class="login-card" style="padding:40px 32px;border-radius:20px;width:100%;max-width:360px;text-align:center">
    <div style="width:72px;height:72px;border-radius:50%;background:linear-gradient(135deg,var(--accent),var(--accent-h));
      color:#fff;display:flex;align-items:center;justify-content:center;font-size:32px;margin:0 auto 16px">💆</div>
    <h1 style="color:var(--text);margin-bottom:4px;font-size:22px;font-weight:700">{{ clinic }}</h1>
    <p style="color:var(--text-sec);margin-bottom:28px;font-size:14px">Панель администратора</p>
    {% if err %}<p style="color:var(--red);margin-bottom:12px;font-size:13px">{{ err }}</p>{% endif %}
    <form method="POST" style="display:flex;flex-direction:column;gap:12px">
      <input type="password" name="password" placeholder="Пароль" style="width:100%;text-align:center">
      <button class="btn btn-primary" type="submit" style="width:100%;justify-content:center;padding:12px">Войти</button>
    </form>
  </div>
</div>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/chats")
        err = "Неверный пароль"
    return render_template_string(
        BASE, body=render_template_string(LOGIN_TPL, err=err, clinic=CLINIC_NAME),
        title="Вход", clinic=CLINIC_NAME, active="", session=session
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def index():
    return redirect("/chats")


# ── Чаты ───────────────────────────────────────────────────────────────────────

CHATS_TPL = """
<div class="chat-layout" style="flex:1;overflow:hidden">

  <!-- Список клиентов -->
  <div class="client-list" id="clientList">
    <div class="client-list-hdr" style="display:flex;align-items:center;justify-content:space-between">
      <span>💬 Диалоги</span>
      {% if total_unread > 0 %}<span class="unread">{{total_unread}}</span>{% endif %}
    </div>
    <div class="client-search">
      <input id="srch" placeholder="🔍 Поиск..." oninput="filterClients(this.value)">
    </div>
    <div class="client-items" id="clist">
      {% for c in clients %}
      <div class="ci {% if c.id==active_id %}active{% endif %}"
           data-id="{{c.id}}"
           onclick="openChat({{c.id}})"
           data-q="{{(c.last_name or '')|lower}} {{(c.first_name or '')|lower}} {{(c.patronymic or '')|lower}} {{c.phone or ''}}">
        <div class="av">{{(c.first_name or c.last_name or '?')[0]|upper}}</div>
        <div class="info">
          <div class="cname">{{ display_name(c) }}</div>
          <div class="cprev" data-prev>{{c.last_message or 'Нет сообщений'}}</div>
        </div>
        <div class="meta">
          <span class="ctime" data-time>{% if c.last_message_at %}{{c.last_message_at.strftime('%H:%M')}}{% endif %}</span>
          <span class="unread" data-unread style="{% if c.unread_count == 0 %}display:none{% endif %}">{{c.unread_count or ''}}</span>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>

  <!-- Окно чата -->
  <div class="chat-win {% if not active_client %}mob-hidden{% endif %}" id="chatWin">
    <div id="chatPanel" style="display:{% if active_client %}flex{% else %}none{% endif %};flex:1;flex-direction:column;min-height:0;overflow:hidden">
      <div class="chat-hdr">
        <button class="btn btn-ghost back-btn" onclick="backToList()" style="padding:6px 10px;flex-shrink:0">← Назад</button>
        <div class="av" id="chatAv" style="width:38px;height:38px;border-radius:50%;background:linear-gradient(135deg,var(--accent),var(--accent-h));color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0">
          {% if active_client %}{{(active_client.first_name or active_client.last_name or '?')[0]|upper}}{% endif %}
        </div>
        <div class="info">
          <div class="cname" id="chatName">{% if active_client %}{{ display_name(active_client) }}{% endif %}</div>
          <div class="cphone" id="chatPhone">{% if active_client %}{{active_client.phone or 'телефон не указан'}}{% endif %}</div>
        </div>
      </div>
      <div class="msgs" id="msgs">
        {% for m in messages %}
        <div class="msg-wrap {{m.direction}}" data-msg-id="{{m.id}}">
          <div class="msg {{m.direction}}">
            {% if m.media_type == 'photo' %}
            <div class="msg-media"><img src="/api/media/{{m.id}}" alt="фото" loading="lazy" onclick="window.open(this.src)"></div>
            {% elif m.media_type == 'video' %}
            <div class="msg-media"><video src="/api/media/{{m.id}}" controls preload="metadata"></video></div>
            {% elif m.media_type == 'document' %}
            <a class="msg-file" href="/api/media/{{m.id}}" target="_blank" download>
              <span class="file-icon">📎</span>
              <span class="file-name">{{ m.media_filename or 'Файл' }}</span>
            </a>
            {% endif %}
            {% if m.text %}<span class="mtext">{{ m.text|e }}</span>{% endif %}
            <span class="mtime">{{m.created_at.strftime('%d.%m %H:%M')}}</span>
          </div>
        </div>
        {% endfor %}
      </div>
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
            onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendReply();}"
            oninput="autoGrow(this)"></textarea>
          <button class="btn-send" id="sendBtn" onclick="sendReply()" title="Отправить">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
          </button>
        </div>
      </div>
    </div>
    <div class="empty" id="chatPlaceholder" style="margin:auto;{% if active_client %}display:none{% endif %}">Выберите диалог</div>
  </div>
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
  box.appendChild(tmp.firstChild);
  lastKnownMsgIds.add(m.id);
  return true;
}

async function openChat(id){
  if(activeId === id) return;
  activeId = id;
  history.pushState({chatId: id}, '', '/chats/' + id);
  document.querySelectorAll('.ci').forEach(function(el){
    el.classList.toggle('active', parseInt(el.dataset.id, 10) === id);
  });
  if(window.innerWidth < 768){
    document.getElementById('clientList').classList.add('mob-hidden');
    document.getElementById('chatWin').classList.remove('mob-hidden');
  }
  await loadChat(id);
}

async function loadChat(id){
  try{
    var r = await fetch('/api/chat/' + id);
    var data = await r.json();
    if(!data.ok) return;

    var c = data.client;
    document.getElementById('chatPanel').style.display = 'flex';
    document.getElementById('chatPlaceholder').style.display = 'none';
    document.getElementById('chatAv').textContent = (c.first_name || c.last_name || '?')[0].toUpperCase();
    document.getElementById('chatName').textContent = c.display_name || ((c.last_name || '') + ' ' + (c.first_name || '') + ' ' + (c.patronymic || '')).trim();
    document.getElementById('chatPhone').textContent = c.phone || 'телефон не указан';

    var box = document.getElementById('msgs');
    box.innerHTML = data.messages.map(function(m){ return renderMsg(m, false); }).join('');
    lastKnownMsgIds.clear();
    initMsgState();
    box.scrollTop = box.scrollHeight;

    var ci = document.querySelector('.ci[data-id="'+id+'"] [data-unread]');
    if(ci){ ci.style.display = 'none'; ci.textContent = ''; }
  } catch(e){}
}

function backToList(){
  activeId = null;
  document.getElementById('clientList').classList.remove('mob-hidden');
  document.getElementById('chatWin').classList.add('mob-hidden');
  document.getElementById('chatPanel').style.display = 'none';
  document.getElementById('chatPlaceholder').style.display = '';
  history.pushState({chatId: null}, '', '/chats');
}

function filterClients(q){
  q = q.toLowerCase();
  document.querySelectorAll('.ci').forEach(function(el){
    el.style.display = el.dataset.q.includes(q) ? '' : 'none';
  });
}

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
      thumb.textContent = '📎';
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
  Array.from(e.target.files || []).forEach(function(f){ pendingFiles.push(f); });
  e.target.value = '';
  updateAttachPreview();
});

async function sendReply(){
  var textarea = document.getElementById('rtxt');
  var text = textarea.value.trim();
  if(!text && !pendingFiles.length){ showToast('Введите сообщение или прикрепите файл', 'err'); return; }
  if(!activeId){ showToast('Клиент не выбран', 'err'); return; }

  var btn = document.getElementById('sendBtn');
  btn.disabled = true;

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
      if(result.warning) showToast('⚠️ ' + result.warning, '');
    } else {
      showToast('Ошибка: ' + (result && result.error || 'неизвестная'), 'err');
    }
  } catch(e){
    showToast('Ошибка сети: ' + e.message, 'err');
  }

  btn.disabled = false;
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
    if(added && (forceScroll || atBottom)){
      box.scrollTop = box.scrollHeight;
    }
  } catch(e){}
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
      if(!el) return;
      var prev = el.querySelector('[data-prev]');
      var time = el.querySelector('[data-time]');
      var unread = el.querySelector('[data-unread]');
      if(prev && prev.textContent !== (d.last_message || 'Нет сообщений')){
        prev.textContent = d.last_message || 'Нет сообщений';
      }
      if(time) time.textContent = d.last_message_at || '';
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
    var total = list.reduce(function(s,d){ return s + (d.id === activeId ? 0 : (d.unread_count||0)); }, 0);
    var badge = document.querySelector('.client-list-hdr .unread');
    if(badge){
      if(total > 0){ badge.style.display = ''; badge.textContent = total; }
      else badge.style.display = 'none';
    } else if(total > 0){
      var hdr = document.querySelector('.client-list-hdr');
      if(hdr) hdr.insertAdjacentHTML('beforeend', '<span class="unread">'+total+'</span>');
    }
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
  var msgs = document.getElementById('msgs');
  if(msgs) msgs.scrollTop = msgs.scrollHeight;
  if(activeId){
    history.replaceState({chatId: activeId}, '', '/chats/' + activeId);
  } else {
    history.replaceState({chatId: null}, '', '/chats');
  }
  if(window.innerWidth < 768){
    if(activeId){
      document.getElementById('clientList').classList.add('mob-hidden');
      document.getElementById('chatWin').classList.remove('mob-hidden');
    } else {
      document.getElementById('clientList').classList.remove('mob-hidden');
      document.getElementById('chatWin').classList.add('mob-hidden');
    }
  }
  startPolling();
  loadQuickTemplates();
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
    }
  } else {
    activeId = null;
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
    total_unread = db.get_total_unread()
    return render(CHATS_TPL, title="Чаты", active="chats",
                  clients=clients, active_client=active_client,
                  messages=messages, active_id=client_id,
                  total_unread=total_unread)


# ── Клиенты ────────────────────────────────────────────────────────────────────

CLIENTS_TPL = """
<div class="scroll-page">
  <div class="page-hdr">
    <h2>👥 Клиенты <span style="font-size:13px;color:var(--muted);font-weight:400">({{clients|length}})</span></h2>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-ghost" onclick="openModal('catModal')" style="font-size:12px">🏷 Категории</button>
    </div>
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
    <table>
      <thead>
        <tr>
          <th>Клиент</th><th>Телефон</th><th>Дата рождения</th><th>Потрачено</th><th>Категории</th><th>Зарегистрирован</th><th>Действия</th>
        </tr>
      </thead>
      <tbody id="clientTbody">
      {% for c in clients %}
      <tr data-cats="{% for cat in c.categories %}{{cat.id}} {% endfor %}">
        <td>
          <div style="display:flex;align-items:center;gap:10px">
            <div class="av" style="width:32px;height:32px;font-size:12px;flex-shrink:0">{{(c.first_name or c.last_name or '?')[0]|upper}}</div>
            <div>
              <a id="cname{{c.id}}" class="cname-link" title="ФИО из YClients (по номеру)" style="font-weight:600;text-decoration:none;color:var(--text);cursor:default">{{ display_name(c) }}</a>
              <div style="font-size:11px;color:var(--muted)" title="Введено клиентом в боте">
                {% if c.username %}@{{c.username}} · {% endif %}{{ display_name(c) }}
              </div>
            </div>
          </div>
        </td>
        <td style="white-space:nowrap">{{c.phone or '—'}}</td>
        <td class="bd-cell" data-client="{{c.id}}" style="color:var(--muted);font-size:12px;white-space:nowrap">{{ c.birth_date.strftime('%d.%m.%Y') if c.birth_date else '—' }}</td>
        <td class="spent-cell" data-client="{{c.id}}" data-phone="{{c.phone or ''}}" style="white-space:nowrap;font-size:13px">{% if c.phone %}<span style="color:var(--muted)">…</span>{% else %}—{% endif %}</td>
        <td>
          <div style="display:flex;flex-wrap:wrap;gap:2px">
            <span class="vip-tag" data-client="{{c.id}}" style="display:none"></span>
            {% for cat in c.categories %}
            <span class="tag" style="background:{{cat.color}}">{{cat.name}}</span>
            {% endfor %}
          </div>
        </td>
        <td style="color:var(--muted);font-size:12px;white-space:nowrap">{{c.created_at.strftime('%d.%m.%Y') if c.created_at else '—'}}</td>
        <td>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn-sm" onclick="editClient({{c.id}},'{{(c.reg_first_name or c.first_name)|e}}','{{(c.reg_last_name or c.last_name)|e}}','{{(c.reg_patronymic or c.patronymic)|e}}','{{c.phone|e}}','{{c.notes|e}}')">✏️</button>
            <button class="btn-sm" onclick="editCategories({{c.id}},'{{(c.reg_first_name or c.first_name)|e}}')">🏷</button>
            <a href="/chats/{{c.id}}" class="btn-sm">💬</a>
          </div>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
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
    <div class="modal-head"><h3>🏷 Категории</h3><button class="modal-close" onclick="closeModal('catModal')">×</button></div>
    <div id="catList">
      {% for cat in categories %}
      <div style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--border)" id="catRow{{cat.id}}">
        <span class="tag" style="background:{{cat.color}}">{{cat.name}}</span>
        <span style="flex:1;color:var(--muted);font-size:12px">{{cat.color}}</span>
        {% if cat.protected %}
        <span style="color:var(--muted);font-size:14px" title="Системная категория — удалить нельзя, можно ставить вручную">🔒</span>
        {% else %}
        <button class="btn-sm btn-danger" onclick="deleteCat({{cat.id}})">✕</button>
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
      <h3>✏️ Редактировать клиента</h3>
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
  // Сверху — ФИО из YClients (по номеру), для сверки с тем, что клиент ввёл в боте.
  if(nameEl && data && data.found){
    if(data.name){ nameEl.textContent = data.name; }
    if(data.url){
      nameEl.href = data.url;
      nameEl.target = '_blank';
      nameEl.rel = 'noopener';
      nameEl.title = 'Открыть клиента в базе YClients';
      nameEl.style.color = 'var(--accent)';
      nameEl.style.cursor = 'pointer';
    }
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

function filterByCat(catId, btn){
  document.querySelectorAll('#clientTbody tr').forEach(function(r){
    if(catId === 0){ r.style.display = ''; return; }
    var cats = (r.dataset.cats || '').trim().split(' ');
    r.style.display = cats.includes(String(catId)) ? '' : 'none';
  });
  document.querySelectorAll('[id^=fCat],[id=fAll]').forEach(function(b){ b.style.background = 'var(--muted)'; });
  btn.style.background = 'var(--pink)';
}

async function editCategories(clientId, name){
  currentClientId = clientId;
  document.getElementById('ccTitle').textContent = '🏷 Категории: ' + name;
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
  if(j.ok){ showToast('✅ Категории сохранены', 'ok'); closeModal('clientCatModal'); setTimeout(function(){ location.reload(); }, 800); }
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
  if(j.ok){ showToast('✅ Категория добавлена', 'ok'); setTimeout(function(){ location.reload(); }, 600); }
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
        showToast('✅ Клиент обновлён', 'ok');
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


# ── Рассылка (напоминания о визите + поздравления с ДР) ───────────────────────

BROADCAST_TPL = """
<div class="scroll-page">
  <div class="page-hdr">
    <h2>📢 Рассылка</h2>
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
        <div class="bc-title">⏰ {{ reminder.label }}</div>
        <div class="bc-hint">Бот раз в час берёт записи на завтра из YClients и за сутки отправляет это сообщение с кнопками «Подтвердить/Отменить».<br>{{ reminder.hint }}</div>
      </div>
      <button class="btn btn-ghost btn-sm" onclick="resetTpl('reminder')">↩ Сбросить</button>
    </div>
    <textarea id="tpl_reminder" rows="6" style="width:100%;font-family:inherit;font-size:13px;line-height:1.5">{{ reminder.text }}</textarea>
    <button class="btn btn-primary" style="margin-top:10px" onclick="saveTpl('reminder')">Сохранить</button>
  </div>
  {% endif %}

  {% if birthday %}
  <div class="bc-card">
    <div class="bc-head">
      <div>
        <div class="bc-title">🎂 {{ birthday.label }}</div>
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
      <button class="btn btn-ghost" onclick="resetTpl('birthday')">↩ Сбросить</button>
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
  if(j.ok) showToast('✅ Шаблон сохранён', 'ok');
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
    showToast(enabled ? '✅ Поздравления включены' : 'Поздравления выключены', 'ok');
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

BOT_TEXTS_ORDER = [
    "bot_welcome", "bot_after_phone", "bot_reg_start", "bot_reg_firstname",
    "bot_reg_patronymic", "bot_reg_birth", "bot_reg_done",
    "bot_profile_caption", "bot_confirm",
]

BOT_TEXTS_TPL = """
<div class="scroll-page">
  <div class="page-hdr"><h2>📝 Шаблоны сообщений бота</h2></div>
  <p style="color:var(--text-sec);margin-bottom:20px;font-size:13px;max-width:760px">
    Здесь редактируются все тексты, которые бот отправляет клиенту. Подстановки в
    фигурных скобках заменяются автоматически (см. подсказку под заголовком).
    Сообщения уходят обычным текстом — без * и _ для оформления.
  </p>
  {% for t in items %}
  <div class="bt-card">
    <div class="bt-head">
      <div>
        <div class="bt-title">{{ t.label }}</div>
        <div class="bt-hint">{{ t.hint }}</div>
      </div>
      <button class="btn btn-ghost btn-sm" onclick="resetTpl('{{ t.key }}')">↩ Сбросить</button>
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
  if(j.ok) showToast('✅ Сохранено', 'ok');
  else showToast('Ошибка: ' + (j.error || 'неизвестная'), 'err');
}
function resetTpl(key){
  if(!confirm('Сбросить текст к значению по умолчанию?')) return;
  document.getElementById('tpl_' + key).value = tplDefaults[key] || '';
}
</script>
"""


@app.route("/templates")
@require_auth
def bot_texts_page():
    import json as _json
    by_key = {t["key"]: t for t in get_all_templates_for_ui()}
    items = [by_key[k] for k in BOT_TEXTS_ORDER if k in by_key]
    defaults = {t["key"]: t["default"] for t in items}
    return render(BOT_TEXTS_TPL, title="Шаблоны", active="templates",
                  items=items, defaults_json=_json.dumps(defaults, ensure_ascii=False))


# ── Быстрые ответы (шаблоны чатов) ─────────────────────────────────────────────

CHAT_TEMPLATES_TPL = """
<div class="scroll-page">
  <div class="page-hdr">
    <h2>⚡ Быстрые ответы (шаблоны для чата)</h2>
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
          <button class="btn-sm" onclick="editTemplate({{t.id}}, '{{t.name|e}}', '{{t.text|e}}')">✏️</button>
          <button class="btn-sm btn-danger" onclick="deleteTemplate({{t.id}})">🗑</button>
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
      <h3 id="templateModalTitle">➕ Добавить шаблон</h3>
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
        showToast('✅ Шаблон сохранён', 'ok');
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
    document.getElementById('templateModalTitle').innerText = '✏️ Редактировать шаблон';
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
            document.getElementById('templateModalTitle').innerText = '➕ Добавить шаблон';
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
        warning = None
        if tg_id and tg_id > 0:
            try:
                _send_tg_media(tg_id, media_type, local_path, caption=text, filename=raw_name)
            except Exception as e:
                warning = str(e)
        else:
            warning = "У клиента нет Telegram — сохранено только в CRM"
        db.save_message(
            client_id, "out", display_text,
            media_type=media_type, media_filename=raw_name,
            media_local_path=unique_name,
        )
        return jsonify({"ok": True, "warning": warning})

    data = request.json or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty"})

    tg_id = client.get("tg_id")
    warning = None
    if tg_id and tg_id > 0:
        try:
            threading.Thread(target=_send_tg, args=(tg_id, text), daemon=True).start()
        except Exception as e:
            warning = str(e)
    else:
        warning = "У клиента нет Telegram — сохранено только в CRM"

    db.save_message(client_id, "out", text)
    return jsonify({"ok": True, "warning": warning})


@app.route("/api/dialogs")
@require_auth
def api_dialogs():
    clients = db.get_all_clients()
    return jsonify([{
        "id": c["id"],
        "last_message": c.get("last_message") or "",
        "last_message_at": c["last_message_at"].strftime("%H:%M") if c.get("last_message_at") else "",
        "last_message_ts": c["last_message_at"].timestamp() if c.get("last_message_at") else 0,
        "unread_count": int(c.get("unread_count") or 0),
    } for c in clients])


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


@app.route("/api/stream")
@require_auth
def api_stream():
    """Server-Sent Events: раз в ~1.5с шлёт сводку непрочитанного (реалтайм-push)."""
    def gen():
        yield "retry: 3000\n\n"
        while True:
            try:
                yield "event: update\ndata: " + json.dumps(_realtime_payload()) + "\n\n"
            except Exception:
                try:
                    yield ": ping\n\n"
                except Exception:
                    break
            time.sleep(1.5)
    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


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


@app.route("/api/media/<int:message_id>")
@require_auth
def api_media(message_id):
    msg = db.get_message(message_id)
    if not msg:
        return jsonify({"ok": False}), 404

    if msg.get("media_local_path"):
        path = os.path.join(UPLOAD_FOLDER, msg["media_local_path"])
        if os.path.isfile(path):
            return send_file(path, as_attachment=(msg.get("media_type") == "document"),
                            download_name=msg.get("media_filename") or "file")

    if msg.get("media_file_id"):
        try:
            url = _tg_file_url(msg["media_file_id"])
            resp = _requests.get(url, timeout=30)
            if resp.status_code == 200:
                mimetype = resp.headers.get("Content-Type", "application/octet-stream")
                return Response(resp.content, mimetype=mimetype)
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


# ── Обновление приложения ──────────────────────────────────────────────────────

@app.route("/api/update/check")
@require_auth
def api_update_check():
    try:
        import updater
        return jsonify(updater.check_for_update())
    except Exception as e:
        return jsonify({"available": False, "error": str(e)})


@app.route("/api/update/apply", methods=["POST"])
@require_auth
def api_update_apply():
    import updater
    info = updater.check_for_update()
    if not info.get("available") or not info.get("url"):
        return jsonify({"ok": False, "error": "нет доступного обновления"})
    started = updater.download_and_run(info["url"])
    return jsonify({"ok": bool(started)})


# ── Запуск ─────────────────────────────────────────────────────────────────────

def run_web():
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run_web()