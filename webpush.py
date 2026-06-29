"""
webpush.py — отправка Web Push уведомлений админам.

Пуш прилетает в браузер/PWA админа, даже когда панель закрыта или экран
заблокирован. Подписки создаются в панели и хранятся в БД (push_subscriptions),
отправка — через pywebpush с VAPID-ключами из config.

Деградирует мягко: если pywebpush не установлен или приватный VAPID-ключ не задан —
функция просто ничего не делает (приложение не падает).
"""
import json
import logging

import database as db
from config import VAPID_PRIVATE_KEY, VAPID_CLAIM_EMAIL

logger = logging.getLogger(__name__)

try:
    from pywebpush import webpush, WebPushException
    _LIB_OK = True
except Exception:
    _LIB_OK = False
    logger.info("pywebpush не установлен — Web Push выключен (pip install pywebpush)")


def available() -> bool:
    return bool(_LIB_OK and VAPID_PRIVATE_KEY)


def send_push_to_admins(title: str, body: str, url: str = "/chats"):
    """Шлёт пуш всем подписанным браузерам админа. Протухшие подписки (404/410)
    удаляет. Функция СИНХРОННАЯ (pywebpush sync) — из async-кода вызывать через
    asyncio.to_thread(send_push_to_admins, ...)."""
    if not available():
        return
    try:
        subs = db.get_push_subscriptions()
    except Exception as e:
        logger.warning("Web Push: не прочитать подписки: %s", e)
        return
    if not subs:
        return

    payload = json.dumps({"title": title, "body": body, "url": url})
    for s in subs:
        sub_info = {
            "endpoint": s["endpoint"],
            "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
        }
        try:
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                # pywebpush мутирует claims (добавляет exp/aud) — даём свежую копию.
                vapid_claims={"sub": VAPID_CLAIM_EMAIL},
                ttl=120,
            )
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                # Подписка мертва (отписались / удалили приложение) — чистим.
                try:
                    db.delete_push_subscription(s["endpoint"])
                except Exception:
                    pass
            else:
                logger.warning("Web Push: ошибка отправки (%s): %s", code, e)
        except Exception as e:
            logger.warning("Web Push: непредвиденная ошибка: %s", e)
