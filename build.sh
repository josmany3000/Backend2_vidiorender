#!/usr/bin/env bash
# build.sh

# Salir inmediatamente si un comando falla
set -o errexit

echo "Instalando dependencias de Python..."

# Render se encarga de instalar las dependencias de tu archivo pyproject.toml (usando Poetry)
# o requirements.txt (usando pip) automáticamente ANTES de ejecutar este script.
# Si por alguna razón necesitas forzar la instalación, puedes usar la siguiente línea,
# pero usualmente no es necesaria.
pip install -r requirements.txt

echo "El script de compilación finalizó exitosamente."

# Si tienes migraciones de base de datos u otros comandos para preparar tu app,
# puedes agregarlos aquí. Por ejemplo:
# python manage.py migrate
