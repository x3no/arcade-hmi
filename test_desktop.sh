#!/bin/bash
# Script para probar la aplicación en desktop

echo "Probando Arcade Control Panel en Desktop"
echo "========================================="
echo ""
echo "Instalando dependencias si es necesario..."

# Instalar pygame si no está
pip3 install --user pygame 2>/dev/null || echo "pygame ya instalado"

echo ""
echo "Iniciando aplicación de prueba..."
echo "  - Presiona ESC para salir"
echo "  - PIN por defecto: 1234"
echo ""

cd "$(dirname "$0")"
python3 src/main_desktop.py
