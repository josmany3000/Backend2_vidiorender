# app.py (para el servicio backend-render-videos)

import os
import requests
import uuid
import threading
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from google.cloud import storage
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip
from moviepy.config import change_settings

# --- CONFIGURACIÓN ---
# Descomenta la siguiente línea si necesitas especificar la ruta a ImageMagick en Render
# change_settings({"IMAGEMAGICK_BINARY": "/usr/bin/convert"})

load_dotenv()
app = Flask(__name__)
CORS(app)

# --- Configuración de Google Cloud Storage (igual que en el otro backend) ---
GCS_BUCKET_NAME = os.getenv('GCS_BUCKET_NAME')
GCS_CREDENTIALS_JSON = os.getenv('GCS_CREDENTIALS_JSON')
storage_client = None

if GCS_CREDENTIALS_JSON:
    try:
        storage_client = storage.Client.from_service_account_info(eval(GCS_CREDENTIALS_JSON))
        print("Cliente de Google Cloud Storage inicializado correctamente.")
    except Exception as e:
        print(f"Error al inicializar el cliente de GCS: {e}")
else:
    print("Advertencia: Las credenciales de GCS no están configuradas.")

# Diccionario en memoria para rastrear el estado de los trabajos de renderizado
jobs = {}

# --- FUNCIONES AUXILIARES ---

def download_file(url, local_path):
    """Descarga un archivo desde una URL a una ruta local."""
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return local_path
    except requests.exceptions.RequestException as e:
        print(f"Error descargando {url}: {e}")
        return None

def upload_to_gcs(local_path, gcs_filename):
    """Sube un archivo local a Google Cloud Storage y lo hace público."""
    if not storage_client or not GCS_BUCKET_NAME:
        raise Exception("GCS no está configurado para subir el archivo final.")
    
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(gcs_filename)
    
    # Determinar content type para el video
    content_type = 'video/mp4'
    
    blob.upload_from_filename(local_path, content_type=content_type)
    blob.make_public()
    return blob.public_url

def process_video(job_id, scenes, render_settings):
    """
    Función principal de renderizado. Se ejecuta en un hilo separado.
    """
    tmp_dir = f"/tmp/{job_id}"
    os.makedirs(tmp_dir, exist_ok=True)
    
    try:
        jobs[job_id]['status'] = 'processing'
        jobs[job_id]['progress'] = 5
        
        # 1. Descargar todos los recursos
        print(f"[{job_id}] Descargando recursos...")
        local_paths = []
        for i, scene in enumerate(scenes):
            progress = 5 + int(30 * (i + 1) / len(scenes))
            jobs[job_id]['progress'] = progress

            # Descargar imagen/video de la escena
            media_url = scene.get('imageUrl')
            if not media_url: continue
            
            ext = os.path.splitext(media_url.split('?')[0])[-1] or '.jpg'
            local_media_path = os.path.join(tmp_dir, f"scene_{i}{ext}")
            download_file(media_url, local_media_path)
            
            # Descargar audio de la escena (si existe)
            audio_url = scene.get('audioUrl')
            local_audio_path = None
            if audio_url:
                local_audio_path = os.path.join(tmp_dir, f"audio_{i}.mp3")
                download_file(audio_url, local_audio_path)

            local_paths.append({'media': local_media_path, 'audio': local_audio_path, 'duration': 5}) # Duración fija por ahora

        jobs[job_id]['progress'] = 35

        # 2. Crear clips de video con MoviePy
        print(f"[{job_id}] Creando clips de video...")
        video_clips = []
        for i, paths in enumerate(local_paths):
            progress = 35 + int(40 * (i + 1) / len(local_paths))
            jobs[job_id]['progress'] = progress

            # Crear clip de imagen y establecer su duración
            clip = ImageClip(paths['media']).set_duration(paths['duration'])
            
            # Añadir audio si existe
            if paths['audio']:
                audio_clip = AudioFileClip(paths['audio'])
                clip = clip.set_audio(audio_clip)

            # TODO: Añadir subtítulos y animaciones aquí si es necesario
            
            video_clips.append(clip)
        
        jobs[job_id]['progress'] = 75

        # 3. Concatenar clips
        final_clip = concatenate_videoclips(video_clips, method="compose")
        
        # TODO: Añadir música de fondo aquí
        
        # 4. Escribir el video final a un archivo
        print(f"[{job_id}] Escribiendo video final...")
        jobs[job_id]['progress'] = 85
        final_video_path = os.path.join(tmp_dir, "final_video.mp4")
        final_clip.write_videofile(final_video_path, codec="libx264", audio_codec="aac", fps=24)
        
        jobs[job_id]['progress'] = 95
        
        # 5. Subir el video final a GCS
        print(f"[{job_id}] Subiendo video a GCS...")
        public_url = upload_to_gcs(final_video_path, f"videos/{job_id}.mp4")
        
        # 6. Actualizar estado del trabajo a completado
        jobs[job_id].update({
            "status": "completed",
            "progress": 100,
            "videoUrl": public_url
        })
        print(f"[{job_id}] ¡Renderizado completado!")

    except Exception as e:
        print(f"[{job_id}] Error durante el renderizado: {e}")
        jobs[job_id].update({"status": "error", "error": str(e)})
    finally:
        # 7. Limpiar archivos temporales
        if os.path.exists(tmp_dir):
            for root, dirs, files in os.walk(tmp_dir, topdown=False):
                for name in files: os.remove(os.path.join(root, name))
                for name in dirs: os.rmdir(os.path.join(root, name))
            os.rmdir(tmp_dir)

# --- ENDPOINTS DE LA API ---

@app.route('/')
def home():
    return "Backend de Renderizado para Videos IA está activo."

@app.route('/api/render-video', methods=['POST'])
def render_video_endpoint():
    """Inicia un nuevo trabajo de renderizado en segundo plano."""
    data = request.get_json()
    if not data or 'scenes' not in data:
        return jsonify({"error": "Datos de escenas no proporcionados."}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "pending", "progress": 0}
    
    # Iniciar el proceso de renderizado en un hilo separado
    render_thread = threading.Thread(
        target=process_video,
        args=(job_id, data['scenes'], data.get('renderSettings', {}))
    )
    render_thread.start()
    
    return jsonify({"message": "Trabajo de renderizado iniciado.", "jobId": job_id}), 202

@app.route('/api/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """Devuelve el estado actual de un trabajo de renderizado."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Trabajo no encontrado."}), 404
    return jsonify(job)

if __name__ == '__main__':
    app.run(port=5002, debug=True)
