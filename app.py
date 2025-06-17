import os
import uuid
import threading
import traceback
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from moviepy.editor import *
from moviepy.audio.fx.all import audio_loop, volumex
from moviepy.video.fx.all import fadeout
import requests

# --- CONFIGURACIÓN INICIAL ---
app = Flask(__name__)
CORS(app)

# Directorios para archivos temporales y videos finales
VIDEO_DIR = os.path.join(os.getcwd(), "final_videos")
TEMP_DIR = os.path.join(os.getcwd(), "temp_assets")
os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# "Base de datos" en memoria para el estado de los trabajos de renderizado
jobs = {}

# --- RUTAS DE LA API ---

@app.route('/api/render-video', methods=['POST'])
def render_video_request():
    """
    Paso 5: Recibe la solicitud para iniciar un renderizado.
    Inicia el trabajo en segundo plano y devuelve un ID de trabajo.
    """
    data = request.json
    job_id = str(uuid.uuid4())
    
    # Guarda el trabajo en nuestra "base de datos"
    jobs[job_id] = {"status": "queued", "progress": 0, "videoUrl": None}
    
    # Inicia la tarea de renderizado en un hilo separado para no bloquear el servidor
    render_thread = threading.Thread(target=render_task, args=(job_id, data))
    render_thread.start()
    
    return jsonify({"jobId": job_id})

@app.route('/api/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """
    Paso 5: Permite al frontend preguntar por el estado de un trabajo.
    """
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)

@app.route('/videos/<filename>')
def serve_video(filename):
    """Sirve el archivo de video final una vez completado."""
    return send_from_directory(VIDEO_DIR, filename)

# --- TAREA DE RENDERIZADO (SE EJECUTA EN SEGUNDO PLANO) ---

def render_task(job_id, data):
    """
    La función que hace el trabajo pesado de crear el video con MoviePy.
    Ahora con transiciones, música de fondo y control de volúmenes.
    """
    try:
        jobs[job_id]['status'] = 'processing'
        
        # Extraer las configuraciones y escenas del request
        config = data.get('renderSettings', {})
        scenes_data = data.get('scenes', [])

        # Obtener nuevas configuraciones con valores por defecto
        narration_volume = float(config.get('narrationVolume', 1.0))
        music_volume = float(config.get('musicVolume', 0.25))
        music_url = config.get('backgroundMusicUrl')
        transition_type = config.get('transitionType', 'none')
        transition_duration = float(config.get('transitionDuration', 0.5))
        
        final_clips = []
        total_scenes = len(scenes_data)
        
        for i, scene in enumerate(scenes_data):
            # 1. Descargar los assets (imagen y audio)
            image_path = download_file(scene['imageUrl'], f"{job_id}_{scene['id']}.jpg")
            audio_path = download_file(scene['audioUrl'], f"{job_id}_{scene['id']}.mp3")

            # 2. Crear clips de MoviePy y aplicar volumen a la narración
            audio_clip = AudioFileClip(audio_path).fx(volumex, narration_volume)
            image_clip = ImageClip(image_path).set_duration(audio_clip.duration)
            
            # 3. Aplicar configuraciones de imagen
            w, h = (1080, 1920) if config.get('resolucion') == '9:16' else (1920, 1080)
            
            if config.get('cubrirImagen'):
                image_clip = image_clip.resize(height=h).crop(x_center=image_clip.w/2, width=w)
            else:
                image_clip = image_clip.resize(width=w)

            image_clip = image_clip.set_position('center')
            image_clip.audio = audio_clip # Asignar el audio con el volumen ya ajustado

            # 4. Añadir subtítulos si es necesario
            if config.get('subtitulos'):
                text_clip = TextClip(scene['script'], fontsize=70, color='white', 
                                     bg_color='rgba(0,0,0,0.5)', size=(w*0.9, None), method='caption')
                text_clip = text_clip.set_position(('center', 'bottom')).set_duration(image_clip.duration)
                final_clip = CompositeVideoClip([image_clip, text_clip], size=(w,h))
            else:
                final_clip = image_clip
            
            final_clips.append(final_clip)
            
            # 5. Actualizar el progreso (hasta 90% para dejar margen al ensamblaje)
            jobs[job_id]['progress'] = int(((i + 1) / total_scenes) * 90)

        # 6. Ensamblar el video final con transiciones
        # Para un fundido cruzado (crossfade), superponemos los clips y aplicamos un fadeout.
        if transition_type == 'fade' and len(final_clips) > 1 and transition_duration > 0:
            video = concatenate_videoclips(final_clips, 
                                           method="compose", 
                                           transition=fadeout.FadeOut(duration=transition_duration),
                                           padding=-transition_duration)
        else:
            # Sin transición
            video = concatenate_videoclips(final_clips, method="compose")
        
        # 7. Añadir música de fondo si se especificó
        if music_url:
            print(f"Descargando música de fondo desde: {music_url}")
            music_path = download_file(music_url, f"bg_music_{job_id}.mp3")
            if music_path:
                try:
                    music_clip = AudioFileClip(music_path)
                    music_clip = music_clip.fx(volumex, music_volume) # Aplicar volumen a la música
                    music_clip = music_clip.fx(audio_loop, duration=video.duration) # Ajustar duración
                    
                    # Combinar audio del video (narración) con la música
                    final_audio = CompositeAudioClip([video.audio, music_clip])
                    video.audio = final_audio
                except Exception as music_error:
                    print(f"Advertencia: No se pudo procesar la música de fondo. Error: {music_error}")

        jobs[job_id]['progress'] = 95 # Progreso antes de escribir el archivo final

        # 8. Escribir el archivo de video
        output_filename = f"{job_id}.mp4"
        output_path = os.path.join(VIDEO_DIR, output_filename)
        video.write_videofile(output_path, codec='libx264', audio_codec='aac', preset='ultrafast', ffmpeg_params=["-crf", "23"])
        
        # 9. Marcar el trabajo como completado
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['progress'] = 100
        jobs[job_id]['videoUrl'] = f"{request.host_url}videos/{output_filename}"

    except Exception as e:
        jobs[job_id]['status'] = 'error'
        # Imprimir el traceback completo en la consola del servidor para una depuración fácil
        print(f"Error CRÍTICO en el trabajo de renderizado {job_id}:")
        traceback.print_exc()

def download_file(url, filename):
    """Función auxiliar para descargar un archivo desde una URL."""
    path = os.path.join(TEMP_DIR, filename)
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return path
    except Exception as e:
        print(f"Error descargando {url}: {e}")
        return None

if __name__ == '__main__':
    # Usar '0.0.0.0' para hacerlo accesible en la red local si es necesario
    app.run(debug=True, host='0.0.0.0', port=5002)

