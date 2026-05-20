"""
반죽과빵 CS 텔레그램 알림 서버 (port 5050)
- POST /notify-admin  : 고객 문의 접수 -> 관리자 알림
- POST /notify-customer : 처리완료 -> 고객 알림
- GET  /ping          : 서버 상태 확인
- poll_bot (daemon)   : /start 수신 -> 알림코드 안내

실행: python cs_server.py
패키지: pip install flask flask-cors requests
"""
import threading
import time

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

BOT_TOKEN     = '8810822027:AAEXle-a-WGhUeO5v1StrihYvl-fa8DPWtw'
ADMIN_CHAT_ID = '6326062373'
TG_API        = 'https://api.telegram.org/bot' + BOT_TOKEN


def tg_send(chat_id, text):
    try:
        r = requests.post(TG_API + '/sendMessage', json={
            'chat_id': str(chat_id),
            'text': text
        }, timeout=10)
        return r.ok
    except Exception as e:
        _log('tg_send error: ' + str(e))
        return False


def _log(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


@app.route('/ping')
def ping():
    return jsonify({'status': 'ok'})


@app.route('/notify-admin', methods=['POST', 'OPTIONS'])
def notify_admin():
    if request.method == 'OPTIONS':
        return '', 200
    data    = request.get_json(silent=True) or {}
    content = data.get('content', '')
    contact = data.get('user_contact', 'none')
    tg_id   = data.get('telegram_chat_id', '')

    lines = ['[CS] 미해결 문의 접수', '내용: ' + content, '연락처: ' + contact]
    if tg_id:
        lines.append('알림코드: ' + tg_id)

    ok = tg_send(ADMIN_CHAT_ID, '\n'.join(lines))
    return jsonify({'ok': ok})


@app.route('/notify-customer', methods=['POST', 'OPTIONS'])
def notify_customer():
    if request.method == 'OPTIONS':
        return '', 200
    data    = request.get_json(silent=True) or {}
    chat_id = data.get('telegram_chat_id', '')
    message = data.get('message', '')
    if not chat_id:
        return jsonify({'ok': False, 'error': 'chat_id missing'})
    ok = tg_send(chat_id, message)
    return jsonify({'ok': ok})


# ── /start 폴링 ──────────────────────────────────
_last_update_id = 0

REPLY_TEXT = (
    '안녕하세요! 반죽과빵 CS 알림봇입니다.\n\n'
    '문의 접수 폼에 아래 알림코드를 입력하시면\n'
    '답변 완료 시 이 채팅으로 알림이 옵니다.\n\n'
    '알림코드: '
)

def poll_bot():
    global _last_update_id
    _log('[poll] started')
    while True:
        try:
            r = requests.get(TG_API + '/getUpdates', params={
                'offset': _last_update_id + 1,
                'timeout': 25
            }, timeout=30)

            if not r.ok:
                _log('[poll] getUpdates fail: ' + str(r.status_code))
                time.sleep(5)
                continue

            for upd in r.json().get('result', []):
                try:
                    uid  = upd['update_id']
                    _last_update_id = uid
                    msg  = upd.get('message', {})
                    text = (msg.get('text') or '').strip()
                    cid  = str(msg.get('chat', {}).get('id', ''))

                    _log('[poll] uid=' + str(uid) + ' cid=' + cid)

                    if text.startswith('/start') and cid:
                        reply = REPLY_TEXT + cid
                        ok = tg_send(cid, reply)
                        _log('[poll] /start -> cid=' + cid + ' ok=' + str(ok))

                except Exception as e:
                    _log('[poll] item error: ' + str(e))

        except Exception as e:
            _log('[poll] loop error: ' + str(e))

        time.sleep(2)


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5050))
    threading.Thread(target=poll_bot, daemon=True).start()
    _log('=== CS server port ' + str(port) + ' ===')
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
