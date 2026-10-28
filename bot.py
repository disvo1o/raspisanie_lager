#!/usr/bin/env python3
"""Telegram reminders. Python 3.10+, no third party dependencies."""
import argparse
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Europe/Moscow')
LOG = logging.getLogger('reminders')


def load_events():
    events = json.loads((ROOT / 'schedule.json').read_text(encoding='utf-8'))
    for event in events:
        event['start'] = datetime.fromisoformat(event['at']).replace(tzinfo=TZ)
        event['remind'] = event['start'] - timedelta(minutes=10)
    return events


def message(event):
    return f"через 10 минут - {event['title']}!"


def api(token, method, **payload):
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        # Never log request URLs: they contain the token.
        raise RuntimeError(f'Telegram HTTP {error.code}') from None
    except urllib.error.URLError:
        raise RuntimeError('Нет соединения с Telegram') from None
    if not result.get('ok'):
        raise RuntimeError(result.get('description', 'Ошибка Telegram'))
    return result['result']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true', help='Показать напоминания без отправки')
    args = parser.parse_args()
    events = load_events()
    if args.preview:
        for event in events:
            print(event['remind'].strftime('%d.%m.%Y %H:%M'), message(event))
        return
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        parser.error('Задайте TELEGRAM_BOT_TOKEN')
    db = sqlite3.connect(ROOT / 'state.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS sent (event_id TEXT PRIMARY KEY)')
    db.commit()

    def get(key, default=None):
        row = db.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def put(key, value):
        db.execute('INSERT OR REPLACE INTO config VALUES (?,?)', (key, str(value)))
        db.commit()

    me = api(token, 'getMe')
    LOG.info('Бот @%s запущен. Добавьте его в группу и отправьте /bind', me['username'])
    while True:
        try:
            updates = api(token, 'getUpdates', offset=int(get('offset', '0')), timeout=0,
                          allowed_updates=['message'])
            for update in updates:
                msg = update.get('message', {})
                text = msg.get('text', '')
                command = text.split()[0] if text else ''
                parts = command.split('@', 1)
                targeted = len(parts) == 1 or parts[1].lower() == me['username'].lower()
                chat = msg.get('chat', {})
                if parts[0] == '/bind' and targeted and chat.get('type') in ('group', 'supergroup'):
                    member = api(token, 'getChatMember', chat_id=chat['id'], user_id=msg['from']['id'])
                    if member['status'] in ('creator', 'administrator'):
                        current = get('chat_id')
                        if current and current != str(chat['id']):
                            api(token, 'sendMessage', chat_id=chat['id'], text='Бот уже привязан к другой группе.')
                        else:
                            api(token, 'sendMessage', chat_id=chat['id'],
                                text='Группа подключена. Напоминания придут за 10 минут до событий 6–8 ноября 2026 года по Москве.')
                            put('chat_id', chat['id'])
                    else:
                        api(token, 'sendMessage', chat_id=chat['id'], text='Подключить группу может её администратор.')
                put('offset', update['update_id'] + 1)
            chat_id = get('chat_id')
            if chat_id:
                now = datetime.now(TZ)
                for event in events:
                    # On restart, send due reminders only while the event is still in the future.
                    if event['remind'] <= now < event['start']:
                        if db.execute('SELECT 1 FROM sent WHERE event_id=?', (event['id'],)).fetchone():
                            continue
                        api(token, 'sendMessage', chat_id=chat_id, text=message(event))
                        db.execute('INSERT INTO sent VALUES (?)', (event['id'],))
                        db.commit()
                        LOG.info('Отправлено: %s', event['id'])
        except (RuntimeError, TimeoutError, OSError) as error:
            LOG.warning('%s', error)
        time.sleep(5)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    main()
