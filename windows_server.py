# pyinstaller --noconfirm --onefile --windowed windows_server.py
import socket
import threading
import json
import ctypes
from flask import Flask, request, jsonify
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

app = Flask(__name__)

# --- 1. UDP DISCOVERY ---
def udp_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', 50019))
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            if data == b"ARCADE_DISCOVER":
                sock.sendto(b"ARCADE_PC_HERE", addr)
        except Exception:
            pass

# --- 2. AUDIO CONTROL ---
def get_audio_volume():
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))

@app.route('/vol', methods=['GET', 'POST'])
def handle_vol():
    try:
        vol = get_audio_volume()
        if request.method == 'POST':
            data = request.json
            if 'action' in data:
                current = vol.GetMasterVolumeLevelScalar()
                if data['action'] == 'up':
                    vol.SetMasterVolumeLevelScalar(min(1.0, current + 0.05), None)
                elif data['action'] == 'down':
                    vol.SetMasterVolumeLevelScalar(max(0.0, current - 0.05), None)
                elif data['action'] == 'mute':
                    vol.SetMute(not vol.GetMute(), None)
            else:
                if 'volume' in data:
                    v = max(0.0, min(1.0, data['volume'] / 100.0))
                    vol.SetMasterVolumeLevelScalar(v, None)
                if 'mute' in data:
                    vol.SetMute(1 if data['mute'] else 0, None)
                    
        return jsonify({
            "status": "ok", 
            "mute": vol.GetMute() == 1, 
            "volume": round(vol.GetMasterVolumeLevelScalar() * 100)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- 3. RETROARCH CONTROL & INFO ---
def get_current_arcade_game():
    EnumWindows = ctypes.windll.user32.EnumWindows
    GetWindowText = ctypes.windll.user32.GetWindowTextW
    GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible

    titles = []
    def foreach_window(hwnd, lParam):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLength(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                GetWindowText(hwnd, buff, length + 1)
                titles.append(buff.value)
        return True

    EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))(foreach_window), 0)

    for title in titles:
        if "RetroArch" in title and "-" in title:
            partes = title.split("-")
            if len(partes) >= 2:
                return partes[-1].strip()
        elif "Big Box" in title or "LaunchBox" in title:
            return "Navegando en Menú (BigBox)"
            
    return "Ningún juego en ejecución"

@app.route('/game', methods=['GET'])
def get_game_status():
    return jsonify({"game": get_current_arcade_game()})

@app.route('/retroarch', methods=['POST'])
def send_retroarch_cmd():
    try:
        data = request.json
        cmd = data.get('command', '')
        if cmd:
            # Enviar comando UDP nativo al puerto por defecto de RetroArch (55355)
            # Asegúrate de habilitar "Network Commands" en RetroArch (network_cmd_enable = "true")
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(f"{cmd}\n".encode('utf-8'), ('127.0.0.1', 55355))
            sock.close()
            return jsonify({"status": "ok", "command": cmd})
        return jsonify({"error": "No command provided"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    threading.Thread(target=udp_server, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
