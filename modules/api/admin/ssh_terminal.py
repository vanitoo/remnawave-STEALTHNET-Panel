"""
SSH Terminal API для админ-панели.

Позволяет подключаться к нодам через SSH прямо из браузера
и передавать/читать данные через короткие HTTP-запросы.
"""

import threading
import time
import uuid

import paramiko
from flask import request, jsonify

from modules.core import get_app
from modules.auth import admin_required

app = get_app()

# Хранилище активных SSH сессий (на процесс)
ssh_sessions = {}
session_lock = threading.Lock()


def _new_session_id() -> str:
    return str(uuid.uuid4())


@app.route('/api/admin/ssh/connect', methods=['POST'])
@admin_required
def ssh_connect(current_admin):
    """Инициализация SSH соединения"""
    data = request.get_json(silent=True) or {}

    host = data.get('host')
    port = int(data.get('port') or 22)
    username = data.get('username') or 'root'
    password = data.get('password')

    if not host or not password:
        return jsonify({"error": "Host and password are required"}), 400

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=10,
            allow_agent=False,
            look_for_keys=False
        )
    except paramiko.AuthenticationException:
        return jsonify({"error": "Неверный пароль"}), 401
    except paramiko.SSHException as e:
        return jsonify({"error": f"SSH ошибка: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Ошибка подключения: {str(e)}"}), 500

    channel = ssh.invoke_shell(term='xterm-256color', width=120, height=40)
    channel.settimeout(0.1)

    session_id = _new_session_id()
    now = time.time()

    with session_lock:
        ssh_sessions[session_id] = {
            'ssh': ssh,
            'channel': channel,
            'host': host,
            'username': username,
            'created_at': now,
            'last_activity': now,
            'admin_id': current_admin.id
        }

    return jsonify({
        "session_id": session_id,
        "message": "Connected successfully"
    }), 200


@app.route('/api/admin/ssh/send', methods=['POST'])
@admin_required
def ssh_send(current_admin):
    """Отправка данных в SSH сессию"""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    input_data = data.get('data', '')

    if not session_id:
        return jsonify({"error": "Session ID required"}), 400

    with session_lock:
        session = ssh_sessions.get(session_id)

    if not session:
        return jsonify({"error": "Session not found"}), 404

    if session['admin_id'] != current_admin.id:
        return jsonify({"error": "Unauthorized"}), 403

    if input_data:
        try:
            session['channel'].send(input_data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    session['last_activity'] = time.time()
    return jsonify({"success": True}), 200


@app.route('/api/admin/ssh/read', methods=['POST'])
@admin_required
def ssh_read(current_admin):
    """Чтение данных из SSH сессии"""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({"error": "Session ID required"}), 400

    with session_lock:
        session = ssh_sessions.get(session_id)

    if not session:
        return jsonify({"error": "Session not found", "disconnected": True}), 404

    if session['admin_id'] != current_admin.id:
        return jsonify({"error": "Unauthorized"}), 403

    channel = session['channel']
    output = ""

    try:
        while channel.recv_ready():
            chunk = channel.recv(4096).decode('utf-8', errors='replace')
            output += chunk
    except Exception:
        # ignore read errors (channel may not be ready)
        pass

    session['last_activity'] = time.time()

    if getattr(channel, 'closed', False):
        with session_lock:
            try:
                session['ssh'].close()
            except Exception:
                pass
            ssh_sessions.pop(session_id, None)
        return jsonify({"output": output, "disconnected": True}), 200

    return jsonify({"output": output}), 200


@app.route('/api/admin/ssh/resize', methods=['POST'])
@admin_required
def ssh_resize(current_admin):
    """Изменение размера терминала"""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    cols = int(data.get('cols') or 120)
    rows = int(data.get('rows') or 40)

    if not session_id:
        return jsonify({"error": "Session ID required"}), 400

    with session_lock:
        session = ssh_sessions.get(session_id)

    if not session:
        return jsonify({"error": "Session not found"}), 404

    if session['admin_id'] != current_admin.id:
        return jsonify({"error": "Unauthorized"}), 403

    try:
        session['channel'].resize_pty(width=cols, height=rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"success": True}), 200


@app.route('/api/admin/ssh/disconnect', methods=['POST'])
@admin_required
def ssh_disconnect(current_admin):
    """Закрытие SSH сессии"""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({"error": "Session ID required"}), 400

    with session_lock:
        session = ssh_sessions.get(session_id)

        if session:
            if session['admin_id'] != current_admin.id:
                return jsonify({"error": "Unauthorized"}), 403

            try:
                session['channel'].close()
                session['ssh'].close()
            except Exception:
                pass

            ssh_sessions.pop(session_id, None)

    return jsonify({"message": "Disconnected"}), 200


def cleanup_old_sessions():
    """Очистка неактивных сессий (старше 30 минут)"""
    current_time = time.time()
    timeout = 30 * 60

    with session_lock:
        to_remove = [
            sid for sid, session in ssh_sessions.items()
            if current_time - session.get('last_activity', current_time) > timeout
        ]

        for sid in to_remove:
            try:
                ssh_sessions[sid]['channel'].close()
                ssh_sessions[sid]['ssh'].close()
            except Exception:
                pass
            ssh_sessions.pop(sid, None)


def start_cleanup_thread():
    def cleanup_loop():
        while True:
            time.sleep(60)
            cleanup_old_sessions()

    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()


# Запускаем очистку при импорте модуля
start_cleanup_thread()

