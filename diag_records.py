"""
diag_records.py — диагностика: что YClients отдаёт по записям клиента.

Запуск:
    python diag_records.py +79991234567

Покажет: нашёлся ли клиент, сколько записей вернулось, их даты/статусы и
полный «сырой» вид первой записи (чтобы свериться с названиями полей).
"""

import asyncio
import json
import sys
import logging
from datetime import date, timedelta

import aiohttp
import yclients as y

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


async def main(phone):
    async with aiohttp.ClientSession() as s:
        ok = await y.authenticate(s)
        print("auth:", "ok" if ok else "FAIL", "| company_id:", y.COMPANY_ID)
        cid = await y.search_client_id(s, phone)
        print("client_id:", cid)
        if not cid:
            print("Клиент по телефону не найден — проверь номер.")
            return

        today = date.today()

        # 1) Записи строго по client_id за всю историю + будущее
        params = {
            "client_id": cid,
            "start_date": "2010-01-01",
            "end_date": (today + timedelta(days=365)).isoformat(),
            "count": 1000,
            "page": 1,
        }
        data = await y._request(s, "GET", f"/records/{y.COMPANY_ID}", params=params)
        print("\n=== /records?client_id ===")
        if not data:
            print("Пустой ответ или ошибка (смотри строку WARNING выше).")
        else:
            print("success:", data.get("success"))
            recs = data.get("data") or []
            print("кол-во записей:", len(recs))
            for r in recs[:15]:
                print(f"  - id={r.get('id')} | datetime={r.get('datetime')} "
                      f"| deleted={r.get('deleted')} | attendance={r.get('attendance')}")
            if recs:
                print("\n--- первая запись (raw) ---")
                print(json.dumps(recs[0], ensure_ascii=False, indent=2)[:2500])

        # 2) На всякий случай — записи на сегодня/завтра по всей компании
        params2 = {
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=2)).isoformat(),
            "count": 50,
            "page": 1,
        }
        data2 = await y._request(s, "GET", f"/records/{y.COMPANY_ID}", params=params2)
        print("\n=== /records (вся компания, сегодня..+2 дня) ===")
        if not data2:
            print("Пустой ответ или ошибка.")
        else:
            recs2 = data2.get("data") or []
            print("success:", data2.get("success"), "| всего записей:", len(recs2))
            for r in recs2[:10]:
                cl = r.get("client") or {}
                print(f"  - id={r.get('id')} | datetime={r.get('datetime')} "
                      f"| client.phone={cl.get('phone')} | client.id={cl.get('id')}")


if __name__ == "__main__":
    phone = sys.argv[1] if len(sys.argv) > 1 else ""
    if not phone:
        print("Использование: python diag_records.py +79991234567")
    else:
        asyncio.run(main(phone))
