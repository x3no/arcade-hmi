# Arcade Control Panel para Raspberry Pi Zero 2

Aplicación de control táctil a pantalla completa para máquina arcade. Permite enviar pulsaciones de teclado (monedas, volumen) y controlar el encendido/apagado del ordenador principal mediante GPIO.

## 🎮 Características

- **Interfaz táctil a pantalla completa** sin servidor gráfico (renderizado directo en framebuffer)
- **Pantalla de bloqueo** con PIN numérico (por defecto: 1234)
- **Control de monedas** para jugador 1 y 2
- **Control de volumen** (subir, bajar, silenciar)
- **Optoacopladores conectados a GPIO** para control del PC y detección de monedas
- **Modo standby** para pantalla OLED (pantalla negra cuando no se usa)
- **Arranque ultra rápido** (~2-3 segundos)
- **Confirmaciones** para acciones críticas (encender/apagar PC)

## 📋 Requisitos

### Hardware
- Raspberry Pi Zero 2 W
- Pantalla táctil USB (conectada al puerto USB de la Pi)
- Optoacoplador conectado a GPIO17 (pin 11)
- Host PC con Bluetooth (para recibir las pulsaciones de teclado)

### Software
- DietPi / Raspberry Pi OS Lite (sin entorno gráfico)
- Python 3.7+
- SDL2 y dependencias
- BlueZ 5 (incluido en el sistema)

## 🚀 Instalación

### 1. Preparar la Raspberry Pi

```bash
# Descargar Raspberry Pi OS Lite
# Flashear en microSD con Raspberry Pi Imager

# Primer arranque - configurar WiFi y SSH si es necesario
sudo raspi-config
```

### 2. Copiar archivos

```bash
# Copiar todos los archivos del proyecto a la Raspberry Pi
scp -r arcade-control/ pi@raspberrypi.local:~/

# Conectar por SSH
ssh pi@raspberrypi.local
```

### 3. Ejecutar instalación

```bash
cd ~/arcade-control
chmod +x setup.sh
sudo ./setup.sh
```

El script de instalación realizará:
- Actualización del sistema
- Instalación de dependencias (SDL2, Python, etc.)
- Configuración de USB HID Gadget
- Instalación de servicios systemd
- Optimización del arranque
- Configuración de la pantalla

### 4. Reiniciar

```bash
sudo reboot
```

La aplicación se iniciará automáticamente al arrancar.

## ⚙️ Configuración

Edita `config/settings.json` para personalizar:

```json
{
  "pin": "1234",                    // PIN de desbloqueo
  "gpio_power_pin": 17,              // Pin GPIO para optoacoplador
  "hid_device": "/dev/hidg0",        // Dispositivo HID
  "screen_width": 800,               // Ancho de pantalla
  "screen_height": 480,              // Alto de pantalla
  "screen_timeout": 300,             // Timeout en segundos
  "button_color": [50, 150, 255],    // Color de botones RGB
  "button_hover_color": [70, 170, 255],
  "button_text_color": [255, 255, 255],
  "bg_color": [0, 0, 0],             // Color de fondo (negro)
  "text_color": [255, 255, 255],
  "font_size": 32,
  "font_size_large": 48
}
```

### Cambiar el PIN

Edita `pin` en `config/settings.json`:
```json
"pin": "9876"
```

### Cambiar mapeo de teclas

Edita `src/keyboard_mapper.py`:
```python
KEYS = {
    'coin_p1': KeyCode.KEY_5,      # Cambiar a otra tecla
    'coin_p2': KeyCode.KEY_6,
    'volume_up': KeyCode.KEY_VOLUME_UP,
    # ...
}
```

### Cambiar pin GPIO

Edita `gpio_power_pin` en `config/settings.json` o pasa el parámetro al crear el controlador.

## 🔌 Conexión de Hardware

### ⚠️ Niveles de tensión — GPIO 3.3 V

La Raspberry Pi Zero 2 W opera a **3.3 V** en todos sus pines GPIO. Esto tiene dos implicaciones clave:

| Módulo | Lado entrada optoacoplador | Lado salida optoacoplador |
|--------|---------------------------|---------------------------|
| #1, #2 (salida) | Alimentado por GPIO (3.3 V) — suficiente para disparar el LED IR | `VCC` sin conectar (actúa como interruptor pasivo) |
| #3–#6 (entrada) | Alimentado por señal externa (PC / monedero) | **`VCC` debe ir a 3.3 V**, NO a 5 V — si `Output` llegara a 5 V dañaría el GPIO |

> **Regla fácil:** los módulos cuyo `Output` va a un GPIO de la Pi deben tener su `VCC` en **3.3 V (Pin 1 ó Pin 17)**, nunca en 5 V.

### Optoacopladores — GPIO Raspberry Pi

Se utilizan **6 módulos optoacopladores** conectados al conector GPIO de 40 pines de la Raspberry Pi.

#### Pinout utilizado

| Pin físico | Nombre      | Dirección | Función              |
|-----------|-------------|-----------|----------------------|
| Pin 1     | 3.3 V ⚡    | —         | VCC módulos #3–#6    |
| Pin 11    | GPIO 17 🔴  | Salida    | Power Button         |
| Pin 12    | GPIO 18 🔴  | Salida    | Reset Button         |
| Pin 13    | GPIO 27 🟢  | Entrada   | Coin P1              |
| Pin 14    | GND 🟫      | —         | Tierra común (todos) |
| Pin 15    | GPIO 22 🟡  | Entrada   | Power LED            |
| Pin 16    | GPIO 23 🟡  | Entrada   | HDD LED              |
| Pin 18    | GPIO 24 🟢  | Entrada   | Coin P2              |

> Pin 2 (5 V) **no se usa** para los módulos. El 5 V solo podría usarse en el lado de la señal externa (p. ej. LED del chasis del PC), nunca como `VCC` del lado que va a los GPIO.

#### Distribución de alimentación 3.3 V

```
Pin 1 (3.3V) ──┬──► Módulo #3 VCC (Power LED)
               ├──► Módulo #4 VCC (HDD LED)
               ├──► Módulo #5 VCC (Coin P1)
               └──► Módulo #6 VCC (Coin P2)

Módulos #1 y #2 (Power Button / Reset Button): VCC sin conectar ✅
```

#### Tipo de optoacoplador y patillaje

El módulo utilizado dispone de dos lados:

- **Lado entrada (LED infrarrojo):** terminales `+` y `−`
- **Lado salida (fototransistor):** terminales `GND`, `VCC` y `Output`

#### Módulos de salida — Power Button y Reset Button (módulos #1 y #2)

La Pi **activa** el optoacoplador para simular la pulsación del botón. El GPIO opera a **3.3 V**, suficiente para disparar el LED infrarrojo del optoacoplador con la resistencia limitadora de corriente incluida en el módulo (típicamente 470 Ω–1 kΩ, lo que da ~1.5–4 mA).

```
Raspberry Pi                 Optoacoplador            Placa base PC
GPIO 17/18 (3.3V) ────────► +  [ LED IR ]  − ◄─── GND (pin 14)
                                    │
                             Output ──────────────► Power/Reset Button +
                             GND    ──────────────► Power/Reset Button −
                             VCC    (sin conectar)
```

> Los módulos #1 y #2 **no necesitan VCC externo** porque el fototransistor
> actúa como simple interruptor entre Output y GND.

#### Módulos de entrada — Power LED y HDD LED (módulos #3 y #4)

El PC activa el optoacoplador a través del LED del chasis; la Pi lee el estado.
`VCC` del módulo va a **3.3 V (Pin 1)** para que `Output` nunca supere los 3.3 V tolerados por el GPIO.

```
PC Chasis                    Optoacoplador            Raspberry Pi
Power/HDD LED + ──────────► +  [ LED IR ]  − ◄─── Power/HDD LED −
                                    │
                             VCC  ◄──── Pin 1 (3.3V)  ← ⚠️ no usar 5V
                             GND  ◄──── Pin 14 (GND)
                             Output ───► GPIO 22/23 (pin 15/16)
```

La Pi configura GPIO 22 y GPIO 23 como entradas con **pull-down interno**.
`Output = HIGH (3.3V)` → LED encendido (PC encendido / disco activo).

#### Módulos de entrada — Coin P1 y Coin P2 (módulos #5 y #6)

El microswitch del monedero activa el optoacoplador; la Pi detecta el flanco.
`VCC` del módulo va a **3.3 V (Pin 1)** por el mismo motivo que los módulos LED.

```
Monedero                     Optoacoplador            Raspberry Pi
Switch + ─────────────────► +  [ LED IR ]  − ◄─── GND (común)
                                    │
                             VCC  ◄──── Pin 1 (3.3V)  ← ⚠️ no usar 5V
                             GND  ◄──── Pin 14 (GND)
                             Output ───► GPIO 27/24 (pin 13/18)
```

La Pi configura GPIO 27 y GPIO 24 como entradas con **pull-down interno**.
Cada flanco ascendente (`LOW → HIGH`) incrementa el contador de monedas correspondiente.

### Conexión Bluetooth

El puerto USB de la Pi queda libre para la pantalla táctil. Las pulsaciones de
teclado se envían al PC mediante Bluetooth HID.

```
Pi Zero 2W
  [USB OTG]   ──────────────────► Pantalla táctil (modo host)
  [mini-HDMI] ──────────────────► Pantalla (vídeo)
  [Bluetooth] ─ ─ ─ ─ ─ ─ ─ ─ ─► PC (teclado HID inalámbrico)
  [GPIO 5V]   ◄─────────────────── Alimentación externa
```

#### Emparejamiento inicial (una sola vez)

1. Asegúrate de que el Bluetooth del PC está activado
2. En la Raspberry Pi ejecuta:
   ```bash
   sudo ./bt-pair.sh
   ```
3. En el PC abre los ajustes Bluetooth, busca **"Arcade HID Keyboard"** y haz clic en **Emparejar** (sin PIN)
4. Una vez emparejado, el PC se reconectará automáticamente cada vez que arranque la Pi

> Tras el emparejamiento, reinicia el servicio para que el host conecte:
> ```bash
> sudo systemctl restart bt-hid-server
> ```

## 🎯 Uso

### Pantalla de inicio
- Aparece **pantalla negra** (ahorro energía en OLED)
- **Toca la pantalla** para despertar

### Desbloqueo
- Introduce el PIN con el teclado numérico
- PIN por defecto: **1234**
- Presiona **OK** para confirmar
- **C** para borrar

### Interfaz principal

Botones disponibles:
- **VOL +** - Aumentar volumen
- **VOL -** - Disminuir volumen
- **MUTE** - Silenciar/Dessilenciar
- **COIN P1** - Moneda jugador 1 (tecla 5)
- **COIN P2** - Moneda jugador 2 (tecla 6)
- **PANTALLA OFF** - Apagar pantalla (modo standby)
- **ENCENDER PC** - Pulsar botón de encendido (con confirmación)
- **APAGAR PC** - Pulsar botón de apagado (con confirmación)
- **BLOQUEAR** - Volver a pantalla de bloqueo

### Confirmaciones

Al presionar **ENCENDER PC** o **APAGAR PC**, aparece un diálogo:
- **SÍ** - Ejecutar acción
- **NO** - Cancelar

## 🛠️ Administración

### Ver logs en tiempo real
```bash
sudo journalctl -u arcade-control.service -f
```

### Reiniciar aplicación
```bash
sudo systemctl restart arcade-control
```

### Detener aplicación
```bash
sudo systemctl stop arcade-control
```

### Deshabilitar inicio automático
```bash
sudo systemctl disable arcade-control
```

### Probar manualmente
```bash
cd /root/arcade-control
sudo python3 src/main.py
```

## 🐛 Solución de problemas

### La pantalla no enciende
```bash
# Verificar framebuffer
ls -l /dev/fb0

# Verificar configuración
cat /boot/config.txt | grep framebuffer
```

### Rotación de pantalla (HDMI, kernel KMS)

En DietPi/Raspberry Pi OS Bookworm con el driver KMS (`vc4-kms-v3d`), el parámetro `display_rotate` de `config.txt` **no tiene efecto**. La rotación debe hacerse a nivel de kernel añadiendo al final de `/boot/firmware/cmdline.txt` (en una sola línea, sin salto):

```
video=HDMI-A-1:800x480,rotate=180
```

Para saber el nombre exacto de la salida HDMI en tu sistema:
```bash
ls /sys/class/drm/
# Busca algo tipo card1-HDMI-A-1 → el nombre es HDMI-A-1
```

Valores válidos de `rotate`: `0`, `90`, `180`, `270`.

> El táctil se puede rotar desde el propio menú físico de la pantalla, independientemente de la rotación del sistema.

### Bluetooth HID no funciona
```bash
# Verificar que el servicio bt-hid-server está activo
sudo systemctl status bt-hid-server

# Ver el log en tiempo real
sudo journalctl -u bt-hid-server -f

# Verificar el adaptador BT
hciconfig hci0

# Listar dispositivos emparejados
bluetoothctl paired-devices

# Repetir el emparejamiento si es necesario
sudo ./bt-pair.sh
```

### GPIO no responde
```bash
# Verificar permisos
ls -l /dev/gpiomem

# Probar GPIO manualmente
python3 -c "import RPi.GPIO as GPIO; GPIO.setmode(GPIO.BCM); GPIO.setup(17, GPIO.OUT); GPIO.output(17, GPIO.HIGH)"
```

### Pantalla táctil no funciona
```bash
# Listar dispositivos de entrada
ls /dev/input/

# Probar eventos táctiles
sudo evtest /dev/input/event0
```

## 📁 Estructura del proyecto

```
arcade-control/
├── src/
│   ├── main.py              # Aplicación principal
│   ├── bluetooth_hid.py     # Cliente Bluetooth HID (API compatible con USBHID)
│   ├── bt_hid_server.py     # Servidor Bluetooth HID (daemon systemd)
│   ├── usb_hid.py           # Keycodes y Modifiers HID
│   ├── gpio_controller.py   # Control GPIO
│   ├── keyboard_mapper.py   # Mapeo de teclas
│   └── config.py            # Cargador de configuración
├── config/
│   └── settings.json        # Configuración
├── systemd/
│   └── arcade-control.service  # Servicio systemd
├── requirements.txt         # Dependencias Python
├── setup.sh                 # Script de instalación
└── README.md                # Este archivo
```

## 🔧 Personalización

### Añadir nuevos botones

1. Edita `src/main.py`, añade a la lista `actions`:
```python
actions = [
    # ... botones existentes ...
    ("MI BOTON", self.mi_funcion),
]
```

2. Implementa la función:
```python
def mi_funcion(self):
    """Mi acción personalizada"""
    try:
        with USBHID() as hid:
            hid.send_key(KeyCode.KEY_X)
    except Exception as e:
        print(f"Error: {e}")
```

### Cambiar resolución

Edita `/boot/config.txt`:
```
framebuffer_width=1024
framebuffer_height=600
```

Y actualiza `config/settings.json`:
```json
"screen_width": 1024,
"screen_height": 600
```

## 📝 Notas técnicas

### Stack tecnológico
- **Python 3** - Lenguaje principal
- **Pygame + SDL2** - Renderizado gráfico en framebuffer
- **RPi.GPIO** - Control de GPIO
- **USB HID Gadget** - Emulación de teclado
- **systemd** - Gestión de servicios

### Optimizaciones de arranque
- Sin servidor gráfico (X11/Wayland)
- Servicios innecesarios deshabilitados
- Kernel configurado para arranque silencioso
- Console blanking deshabilitado

### Consumo de energía
- Pantalla negra en OLED = mínimo consumo
- Sin servidor gráfico = menos CPU
- GPIO en LOW cuando no se usa

## 📜 Licencia

MIT License - Úsalo libremente en tus proyectos.

## 🤝 Contribuciones

¿Mejoras o sugerencias? ¡Abre un issue o pull request!

---

**¡Disfruta de tu máquina arcade! 🕹️**
