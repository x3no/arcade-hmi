# Arcade HMI - Windows Host Server

Este servidor ligero de Flask en Python se ejecuta en el PC Windows del sistema Arcade para proporcionar:
- Descubrimiento automático de IP por UDP a la Raspberry Pi.
- Interfaz REST API para manipular el volumen maestro del sistema.
- Recuperación del título de ventana del emulador en uso.
- Reenvío de comandos nativos por UDP a RetroArch localmente (pausa, guardar/cargar, reset, etc.).

---

## Requisitos

- Windows 10/11.
- Python 3.8 o superior instalado.
- Asegúrate de marcar **"Add Python to PATH"** durante la instalación de Python en Windows.

## Compilar el binario paso a paso

Para integrar este servidor de forma invisible en tu PC y que arranque sin ventanas molestas:

**1. Instalar dependencias**
Abre una consola de comandos (`cmd`) dentro de esta carpeta (`windows_host/`) y ejecuta:
```cmd
pip install -r requirements.txt
```

**2. Compilar en un ejecutable**
Para empaquetarlo en un único archivo `.exe` que se ejecuta en segundo plano (sin consola):
```cmd
pyinstaller --noconsole --onefile windows_server.py
```

**3. Extraer tu ejecutable**
Cuando finalice, verás varias carpetas generadas. Tu binario final será `windows_server.exe` ubicado dentro de la carpeta `dist/`.

---

## Configuración y Autorun

Para que se ejecute siempre que inicies la máquina:
1. Pulsa `Win + R` en tu teclado.
2. Escribe `shell:startup` y tecla _Enter_.
3. Pega ahí tu archivo `windows_server.exe` o un acceso directo a él.

**⚠️ Importante - Firewall:**
La primera vez que se ejecute oculto o cuando intente interactuar por red, Windows Defender Firewall podría bloquearlo silenciosamente. Considera añadir manualmente una excepción en el Firewall para el ejecutable o para los puertos UDP 50019 y TCP 5000 a todas las redes (públicas y privadas).

## Activar control por red en RetroArch

Asegúrate de editar tu archivo `retroarch.cfg` en el PC de Windows para que RetroArch acepte los comandos. Busca y edita estas dos variables:

```ini
network_cmd_enable = "true"
network_cmd_port = "55355"
```