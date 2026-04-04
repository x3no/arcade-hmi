# pyinstaller --noconfirm --onefile --windowed windows_server.py
import socket
import threading
import json
import ctypes
from flask import Flask, request, jsonify, send_file
import io
try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

app = Flask(__name__)

import comtypes

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
    comtypes.CoInitialize()
    devices = AudioUtilities.GetSpeakers()
    
    # Dependiendo de la versión de pycaw, puede devolver un wrapper AudioDevice o el COM interface directo
    if hasattr(devices, 'EndpointVolume'):
        # Pycaw moderno devuelve un wrapper object
        return devices.EndpointVolume
    else:
        # Pycaw clasico devuelve directamente el puntero IMMDevice
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
                    vol.SetMasterVolumeLevelScalar(min(1.0, current + 0.02), None)
                elif data['action'] == 'down':
                    vol.SetMasterVolumeLevelScalar(max(0.0, current - 0.02), None)
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
    # Intentar obtener el estado real desde la API UDP de RetroArch primero
    status = query_retroarch_udp("GET_STATUS", timeout=0.05)
    if status:
        clean_status = status.replace("GET_STATUS ", "").strip()
        status_parts = clean_status.split(" ", 1)
        st_cmd = status_parts[0].lower() if len(status_parts) > 0 else ""
        if st_cmd in ["playing", "paused"]:
            game_name = "Unknown"
            system_name = "Unknown"
            if len(status_parts) > 1:
                game_info = status_parts[1].split(",")
                if len(game_info) >= 1:
                    system_name = game_info[0].strip()
                if len(game_info) >= 2:
                    game_name = game_info[1].strip()
            is_paused = (st_cmd == "paused")
            return {"is_game_running": True, "is_game_paused": is_paused, "is_menu_running": False, "game": game_name, "system": system_name}

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
                return {"is_game_running": True, "is_menu_running": False, "game": partes[-1].strip()}
        elif "Big Box" in title or "LaunchBox" in title:
            return {"is_game_running": False, "is_menu_running": True, "game": None}
            
    return {"is_game_running": False, "is_menu_running": False, "game": None}

def query_retroarch_udp(cmd, timeout=0.15):
    """Envía un comando UDP a RetroArch y espera una respuesta."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(f"{cmd}\n".encode('utf-8'), ('127.0.0.1', 55355))
        data, _ = sock.recvfrom(1024)
        return data.decode('utf-8').strip()
    except Exception:
        return None
    finally:
        sock.close()

def get_retroarch_advanced_info():
    """Recopila toda la información posible deRetroArch combinando Títulos de Ventana y su protocolo UDP."""
    info = {
        "running": False,
        "title": None,
        "core": None,
        "version": None,
        "status": None,
        "system": None,
        "game": None,
        "raw_window_title": None,
        "raw_retroarch_status": None,
        "raw_retroarch_version": None
    }
    
    EnumWindows = ctypes.windll.user32.EnumWindows
    GetWindowText = ctypes.windll.user32.GetWindowTextW
    GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible

    def foreach_window(hwnd, lParam):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLength(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                GetWindowText(hwnd, buff, length + 1)
                title = buff.value
                if "RetroArch" in title or " - " in title:
                    # Validar si el título parece ser de RetroArch (ej: "1.15.0 - Snes9x - Super Mario")
                    status_str = query_retroarch_udp("GET_STATUS", timeout=0.05)
                    if status_str or "RetroArch" in title:
                        info["running"] = True
                        info["raw_window_title"] = title
                        return False # Detener la búsqueda
        return True

    EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))(foreach_window), 0)

    if info["running"]:
        # Analizar el título
        if info["raw_window_title"]:
            raw_title = info["raw_window_title"]
            if "-" in raw_title:
                partes = [p.strip() for p in raw_title.split("-")]
                if len(partes) >= 3:
                    info["version"] = partes[0]
                    info["core"] = partes[1]
                    info["title"] = " - ".join(partes[2:])
                elif len(partes) == 2:
                    if "RetroArch" in partes[0]:
                        info["title"] = partes[1]
                    else:
                        info["version"] = partes[0]
                        info["title"] = partes[1]
            else:
                info["title"] = raw_title.replace("RetroArch", "").strip() or raw_title

        # Consultar la API UDP nativa
        status = query_retroarch_udp("GET_STATUS")
        if status:
            clean_status = status.replace("GET_STATUS ", "").strip()
            info["raw_retroarch_status"] = status
            
            # Extraer el nombre del juego del estado (ej: "PLAYING msx,Salamander,crc32=b9b17d6d")
            status_parts = clean_status.split(" ", 1)
            if len(status_parts) > 0:
                info["status"] = status_parts[0].lower() # e.g. "playing"
            if len(status_parts) > 1:
                game_info = status_parts[1].split(",")
                if len(game_info) >= 1:
                    info["system"] = game_info[0].strip()
                if len(game_info) >= 2:
                    info["game"] = game_info[1].strip()
                    info["title"] = game_info[1].strip() # Fallback for title
            
        version = query_retroarch_udp("VERSION")
        if version:
            info["version"] = version
            info["raw_retroarch_version"] = version

    return info

@app.route('/game', methods=['GET'])
def get_game_status():
    return jsonify(get_current_arcade_game())

@app.route('/game/info', methods=['GET'])
def get_game_advanced_info():
    """Nuevo Endpoint que devuelve todo el contexto extraído de RetroArch"""
    return jsonify(get_retroarch_advanced_info())

@app.route('/game/preview', methods=['GET'])
def get_game_preview():
    """Toma una captura de pantalla en vivo de la ventana de RetroArch/BigBox."""
    if ImageGrab is None:
        return jsonify({"error": "Pillow no está instalado. Ejecuta: pip install Pillow"}), 501

    EnumWindows = ctypes.windll.user32.EnumWindows
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible
    GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
    GetWindowText = ctypes.windll.user32.GetWindowTextW

    hwnd_found = [0]
    def foreach_window(hwnd, lParam):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLength(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                GetWindowText(hwnd, buff, length + 1)
                title = buff.value
                if "RetroArch" in title or "Big Box" in title or "LaunchBox" in title:
                    hwnd_found[0] = hwnd
                    return False
        return True

    EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))(foreach_window), 0)

    if not hwnd_found[0]:
        return jsonify({"error": "Ventana de juego no encontrada"}), 404

    import ctypes.wintypes
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd_found[0], ctypes.pointer(rect))
    
    # Comprobar que la ventana no está minimizada y tiene dimensiones
    if rect.right - rect.left <= 0 or rect.bottom - rect.top <= 0:
         return jsonify({"error": "Ventana inactiva/minimizada"}), 400

    bbox = (rect.left, rect.top, rect.right, rect.bottom)
    
    try:
        # Capturamos la ventana y la redimensionamos para no saturar la red local o la Pi
        img = ImageGrab.grab(bbox)
        img.thumbnail((320, 240))  # Max resolution for the mini preview
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=75)
        buf.seek(0)
        return send_file(buf, mimetype='image/jpeg')
    except Exception as e:
        return jsonify({"error": f"Fallo al capturar: {e}"}), 500

# --- 4. MEDIA INFO (winsdk) ---
try:
    import asyncio
    from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager, GlobalSystemMediaTransportControlsSessionPlaybackStatus
    WINSDK_AVAILABLE = True
except ImportError:
    WINSDK_AVAILABLE = False

def get_media_status():
    """Usa la API nativa de Windows 10/11 de controles multimedia para estado global, duración y posición"""
    if not WINSDK_AVAILABLE:
        return {"error": "winsdk no está instalado. Ejecuta: pip install winsdk"}

    async def _get_info():
        try:
            # Obtener el gestor global de sesiones multimedia de Windows
            manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
            session = manager.get_current_session()
            
            if not session:
                return {"playing": False, "artist": None, "song": None, "position": 0, "duration": 0}
            
            # Extraer propiedades
            props = await session.try_get_media_properties_async()
            timeline = session.get_timeline_properties()
            playback = session.get_playback_info()
            
            # Status 4 = Playing, Status 5 = Paused
            is_playing = (playback.playback_status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING)
            is_paused = (playback.playback_status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PAUSED)
            
            # timeline (duración/posición actual en la canción o vídeo)
            pos_sec = timeline.position.total_seconds() if timeline else 0
            dur_sec = timeline.end_time.total_seconds() if timeline else 0
            
            return {
                "playing": is_playing or is_paused,
                "paused": is_paused,
                "artist": props.artist,
                "song": props.title,
                "position": int(pos_sec),
                "duration": int(dur_sec)
            }
        except Exception as e:
            return {"error": str(e)}

    # Ejecutamos asíncronamente
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(_get_info())

@app.route('/media', methods=['GET', 'POST'])
def handle_media_endpoint():
    if request.method == 'POST':
        if not WINSDK_AVAILABLE:
            return jsonify({"error": "winsdk no está instalado"}), 501
            
        data = request.json or {}
        action = data.get('action')
        value = data.get('value', 10) # 10 segundos por defecto para forward/rewind
        
        async def _control(act, val):
            try:
                manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
                session = manager.get_current_session()
                if not session:
                    return {"error": "No media session found"}
                    
                res = False
                if act == 'play':
                    res = await session.try_play_async()
                elif act == 'pause':
                    res = await session.try_pause_async()
                elif act == 'toggle':
                    res = await session.try_toggle_play_pause_async()
                elif act == 'next':
                    res = await session.try_skip_next_async()
                elif act == 'prev':
                    res = await session.try_skip_previous_async()
                elif act in ['forward', 'rewind', 'seek']:
                    timeline = session.get_timeline_properties()
                    if timeline:
                        current_pos = timeline.position.total_seconds()
                        new_pos = current_pos
                        if act == 'forward':
                            new_pos += val
                        elif act == 'rewind':
                            new_pos -= val
                        elif act == 'seek':
                            new_pos = val
                            
                        new_pos = max(0, min(new_pos, timeline.end_time.total_seconds()))
                        
                        try:
                            res = await session.try_change_playback_position_async(int(new_pos * 10000000))
                        except TypeError:
                            import datetime
                            res = await session.try_change_playback_position_async(datetime.timedelta(seconds=new_pos))
                
                return {"status": "ok", "action": act, "success": res}
            except Exception as e:
                return {"error": str(e)}

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return jsonify(loop.run_until_complete(_control(action, value)))

    return jsonify(get_media_status())

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
