#!/usr/bin/env bash
# build.sh

# Salir inmediatamente si un comando falla
set -e

# Instalar dependencias del sistema (ffmpeg e ImageMagick)
apt-get update
apt-get install -y ffmpeg imagemagick

# Instalar dependencias de Python
pip install -r requirements.txt
