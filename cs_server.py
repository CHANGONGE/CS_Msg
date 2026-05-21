"""
반죽과빵 CS 텔레그램 알림 서버 (port 5050)
- POST /notify-admin  : 고객 문의 접수 -> 관리자 알림 + notification_log INSERT
- POST /notify-customer : 처리완료 -> 고객 알림 + notification_log INSERT
- POST /embed         : Jina AI 임베딩 프록시 (Supabase Edge Fn용)
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

SUPABASE_URL  = 'https://cejujszwthlacdgsllzx.supabase.co'
SUPABASE_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNlanVqc3p3dGhsYWNkZ3NsbHp4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgwMjc5MjQsImV4cCI6MjA5MzYwMzkyNH0.Wx_DQhg7VxvqGSyyLYH0d31B77XmlrqwNy4M9afLQqY'
JINA_API_KEY  = 'jina_83411b42bc7d40529c1a8ba50e3649b74l6Vg0pxBePdFycvLPK0rQoKJAg6'


def sb_link_telegram(inquiry_id, chat_id):
    """unresolved_queries에 telegram_chat_id 저장.
    telegram_chat_id IS NULL인 row만 업데이트 (이미 연결된 경우 건너뜀).
    반환: 'linked' | 'already' | 'error'
    """
    try:
        r = requests.patch(
            SUPABASE_URL + '/rest/v1/unresolved_queries',
            params={
                'id': 'eq.' + str(inquiry_id),
                'telegram_chat_id': 'is.null'
            },
            json={'telegram_chat_id': str(chat_id), 'push_registered': 'Telegram'},
            headers={
                'apikey': SUPABASE_ANON,
                'Authorization': 'Bearer ' + SUPABASE_ANON,
                'Content-Type': 'application/json',
                'Prefer': 'return=representation'
            },
            timeout=10
        )
        if not r.ok:
            _log('sb_link_telegram http error: ' + str(r.status_code) + ' ' + r.text[:200])
            return 'error'
        rows = r.json()
        if isinstance(rows, list) and len(rows) > 0:
            return 'linked'   # 새로 연결 성공
        return 'already'      # 이미 연결돼 있었음 (조건 불일치 = 0 rows updated)
    except Exception as e:
        _log('sb_link_telegram error: ' + str(e))
        return 'error'


def sb_log_notification(ntype, recipient, message, status):
    """notification_log 테이블에 발송이력 기록 (비차단, 실패 시 무시)
    실제 컬럼: subscriber_id(uuid), channel, message, status, sent_at
    recipient 정보는 message 앞에 태그로 포함 (예: [관리자] 내용)
    """
    try:
        tagged_msg = '[' + ntype + '→' + str(recipient) + '] ' + message
        requests.post(
            SUPABASE_URL + '/rest/v1/notification_log',
            json={
                'channel': 'telegram',
                'message': tagged_msg[:500],
                'status': status,
            },
            headers={
                'apikey': SUPABASE_ANON,
                'Authorization': 'Bearer ' + SUPABASE_ANON,
                'Content-Type': 'application/json',
                'Prefer': 'return=minimal'
            },
            timeout=5
        )
    except Exception as e:
        _log('sb_log_notification error: ' + str(e))


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


@app.route('/embed', methods=['POST', 'OPTIONS'])
def embed():
    if request.method == 'OPTIONS':
        return '', 200
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    if not text:
        return jsonify({'error': 'text required'}), 400
    try:
        r = requests.post(
            'https://api.jina.ai/v1/embeddings',
            headers={
                'Authorization': 'Bearer ' + JINA_API_KEY,
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0'
            },
            json={
                'model': 'jina-embeddings-v3',
                'input': [text],
                'dimensions': 768,
                'task': 'retrieval.query'
            },
            timeout=30
        )
        if not r.ok:
            return jsonify({'error': r.text}), 500
        return jsonify({'embedding': r.json()['data'][0]['embedding']})
    except Exception as e:
        _log('embed error: ' + str(e))
        return jsonify({'error': str(e)}), 500


@app.route('/notify-admin', methods=['POST', 'OPTIONS'])
def notify_admin():
    if request.method == 'OPTIONS':
        return '', 200
    data    = request.get_json(silent=True) or {}
    content = data.get('content', '')
    contact = data.get('user_contact', 'none')
    tg_id   = data.get('telegram_chat_id', '')
    title   = data.get('title', '[CS] 미해결 문의 접수')

    lines = [title, '내용: ' + content, '연락처: ' + contact]
    if tg_id:
        lines.append('알림코드: ' + tg_id)

    msg = '\n'.join(lines)
    ok  = tg_send(ADMIN_CHAT_ID, msg)
    threading.Thread(
        target=sb_log_notification,
        args=('admin_alert', 'admin', msg, 'sent' if ok else 'failed'),
        daemon=True
    ).start()
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
    threading.Thread(
        target=sb_log_notification,
        args=('customer_reply', chat_id, message, 'sent' if ok else 'failed'),
        daemon=True
    ).start()
    return jsonify({'ok': ok})


# ── /start 폴링 ──────────────────────────────────
_last_update_id = 0

REPLY_LINKED = (
    '✅ 알림 연결 완료!\n\n'
    '담당자 답변 완료 시 이 채팅으로 바로 알림이 옵니다.\n'
    '조금만 기다려 주세요 😊'
)
REPLY_ALREADY = (
    'ℹ️ 이미 알림 연결이 완료된 문의입니다.\n\n'
    '담당자 답변 완료 시 이 채팅으로 알림이 전송됩니다.\n'
    '중복 연결은 필요하지 않습니다 😊'
)
REPLY_MANUAL = (
    '안녕하세요! 반죽과빵 CS 알림봇입니다.\n\n'
    '문의를 먼저 접수하신 후\n'
    '접수 완료 화면의 [텔레그램 알림 연결하기] 버튼을 눌러주세요.\n\n'
    '고객센터: https://banjukbang-cs.vercel.app'
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
                        parts = text.split(None, 1)
                        inquiry_id = parts[1].strip() if len(parts) > 1 else ''
                        if inquiry_id:
                            result = sb_link_telegram(inquiry_id, cid)
                            if result == 'linked':
                                reply = REPLY_LINKED
                            elif result == 'already':
                                reply = REPLY_ALREADY
                            else:
                                reply = REPLY_MANUAL
                            _log('[poll] /start inquiry_id=' + inquiry_id + ' cid=' + cid + ' result=' + result)
                        else:
                            reply = REPLY_MANUAL
                            _log('[poll] /start (no inquiry_id) cid=' + cid)
                        tg_send(cid, reply)

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
