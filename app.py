# app.py (Renderizador Inteligente v4.0 - Impulsado por IA Generativa)
# -*- coding: utf-8 -*-

import os
import uuid
import json
import requests
import logging
import time
import threading
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# --- LIBRERÍAS DE IA Y NUBE ---
import google.generativeai as genai
from google.cloud import storage

# --- LIBRERÍA DE EDICIÓN DE VIDEO ---
from moviepy.editor import *
import numpy as np
import math

# --- 1. CONFIGURACIÓN INICIAL Y LOGGING ---
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)

app = Flask(__name__)
CORS(app)

JOBS = {} # Diccionario en memoria para rastrear el estado de los trabajos

# --- Configuración de Clientes de Google ---
try:
    # Intenta cargar credenciales desde la variable de entorno para Render.com
    if os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON'):
        credentials_json_str = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
        credentials_path = f'/tmp/{uuid.uuid4()}_gcp-credentials.json'
        with open(credentials_path, 'w') as f:
            f.write(credentials_json_str)
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_path
        logging.info("Credenciales de GCP cargadas desde variable de entorno para producción.")

    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    storage_client = storage.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
    GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
    
    model_text = genai.GenerativeModel('gemini-1.5-flash')
    logging.info("Clientes de Google (Gemini, Storage) configurados exitosamente.")
except Exception as e:
    logging.critical("ERROR FATAL AL CONFIGURAR CLIENTES DE GOOGLE.", exc_info=True)


# ==============================================================================
# === MÓDULO DE EFECTOS VISUALES (Integrado)                                 ===
# ==============================================================================

def vfx_aplicar_viñeta(clip, radio=0.7, suavizado=0.4, color=(0, 0, 0)):
    w, h = clip.size
    y, x = np.ogrid[0:h, 0:w]
    dist_centro = np.sqrt(((x - w / 2) / (w / 2))**2 + ((y - h / 2) / (h / 2))**2)
    mascara = np.clip((dist_centro - radio) / suavizado, 0, 1)
    viñeta_overlay = ColorClip(size=(w, h), color=color, duration=clip.duration)
    viñeta_con_mascara = viñeta_overlay.set_mask(ImageClip(mascara * 255, ismask=True))
    return CompositeVideoClip([clip, viñeta_con_mascara])

def vfx_crear_efecto_ken_burns(clip, duracion, video_size, zoom_dir='in', pan_dir='derecha', factor_zoom=1.15):
    from moviepy.video.fx.all import crop, resize
    img_w, img_h = clip.size
    if zoom_dir == 'in':
        w_inicial, h_inicial, w_final, h_final = img_w, img_h, img_w / factor_zoom, img_h / factor_zoom
    else:
        w_inicial, h_inicial, w_final, h_final = img_w / factor_zoom, img_h / factor_zoom, img_w, img_h
    margen_x, margen_y = w_final / 2, h_final / 2
    puntos_x = {'izquierda': margen_x, 'centro': img_w / 2, 'derecha': img_w - margen_x}
    puntos_y = {'arriba': margen_y, 'centro': img_h / 2, 'abajo': img_h - margen_y}
    x_centro_inicial = puntos_x.get(pan_dir, puntos_x['centro'])
    y_centro_inicial = puntos_y.get(pan_dir, puntos_y['centro'])
    x_centro_final, y_centro_final = puntos_x['centro'], puntos_y['centro']
    def interp(val_inicial, val_final, t):
        return val_inicial + (val_final - val_inicial) * (t / duracion)
    def transformar_frame(get_frame, t):
        ancho_actual = interp(w_inicial, w_final, t)
        alto_actual = interp(h_inicial, h_final, t)
        x_centro_actual = interp(x_centro_inicial, x_centro_final, t)
        y_centro_actual = interp(y_centro_inicial, y_centro_final, t)
        frame_recortado = crop(get_frame(t), x_center=x_centro_actual, y_center=y_centro_actual, width=ancho_actual, height=alto_actual)
        return resize(frame_recortado, newsize=video_size)
    return clip.set_duration(duracion).fl(transformar_frame)

# ==============================================================================
# === MÓDULO DE EFECTOS DE TEXTO (Integrado)                                 ===
# ==============================================================================

def text_fx_crear_texto_con_fondo(texto, padding=20, bg_color=(0,0,0), bg_opacity=0.6, **kwargs):
    clip_texto = TextClip(txt=texto, **kwargs)
    size_fondo = (clip_texto.w + padding * 2, clip_texto.h + padding * 2)
    clip_fondo = ColorClip(size=size_fondo, color=bg_color).set_opacity(bg_opacity)
    return CompositeVideoClip([clip_fondo.set_position('center'), clip_texto.set_position('center')])

def text_fx_crear_texto_popup(texto, duracion_anim, **kwargs):
    from moviepy.video.fx.all import resize
    def ease_out_back(t_norm):
        c1 = 1.70158
        c3 = c1 + 1
        return 1 + c3 * pow(t_norm - 1, 3) + c1 * pow(t_norm - 1, 2)
    def resize_func(t):
        return ease_out_back(t / duracion_anim) if t < duracion_anim else 1
    clip_texto = TextClip(txt=texto, **kwargs)
    return clip_texto.fx(resize, resize_func)

def text_fx_crear_texto_maquina_escribir(texto, duracion_total, fps=30, **kwargs):
    clips_letras = []
    duracion_por_letra = duracion_total / len(texto) if texto else 0
    for i in range(len(texto)):
        texto_visible = texto[:i + 1]
        clip_letra = TextClip(txt=texto_visible, **kwargs)
        clips_letras.append(clip_letra.set_duration(duracion_por_letra))
    if not clips_letras: return ColorClip(size=(1,1), color=(0,0,0), duration=duracion_total).set_opacity(0)
    return concatenate_videoclips(clips_letras).set_fps(fps)


# ==============================================================================
# === EL CEREBRO: GENERACIÓN DE RECETA CON IA (GEMINI)                       ===
# ==============================================================================

def get_ai_prompts():
    """Almacena los prompts de sistema para los diferentes estilos de edición."""
    prompts = {
        "cinematico": """
        Eres un director de cine y post-productor experto. Tu tarea es crear una receta de edición en JSON para un video con un estilo cinematográfico, emocional y visualmente impactante. Usa paneos lentos (Ken Burns), correcciones de color sutiles (aumento de contraste, baja saturación), viñetas, y efectos de sonido para crear atmósfera. El texto debe ser elegante, con apariciones y desapariciones suaves.
        """,
        "youtuber_dinamico": """
        Eres un editor de videos para un Youtuber de éxito, estilo MrBeast o similar. Tu objetivo es crear una receta de edición en JSON que sea extremadamente dinámica y retenga la atención. Usa zooms rápidos, efectos de sonido de impacto (whoosh, click, pop), textos con efecto Pop-Up, y transiciones de deslizamiento rápidas. La legibilidad es clave, así que usa fondos para los textos.
        """,
        "documental": """
        Eres un editor de documentales para canales como National Geographic o Discovery. Tu tarea es crear una receta de edición en JSON que sea informativa, clara y profesional. Usa el efecto Ken Burns para dar vida a las imágenes, textos claros con fondo para datos importantes, y transiciones de fundido cruzado (crossfade) suaves. Los efectos de sonido deben ser ambientales y no distraer.
        """
    }
    return prompts

def safe_json_parse(text):
    text = text.strip().replace('```json', '').replace('```', '')
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logging.error(f"Fallo al decodificar JSON. Error: {e}. Texto problemático: {text[:500]}")
        return None

def create_ai_recipe(job_id, scenes_data, style):
    logging.info(f"[{job_id}] CEREBRO IA: Iniciando creación de receta. Estilo solicitado: '{style}'.")

    # 1. Leer el catálogo de efectos de sonido desde GCS, como solicitaste.
    sound_effects_catalog = "No hay efectos de sonido disponibles."
    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob("sound_effects.json") # Ruta del archivo que tú creas.
        sound_effects_json_str = blob.download_as_string()
        sound_effects_catalog = json.loads(sound_effects_json_str)
        logging.info(f"[{job_id}] CEREBRO IA: Catálogo de SFX cargado desde GCS.")
    except Exception as e:
        logging.warning(f"[{job_id}] CEREBRO IA: No se pudo cargar 'sound_effects.json' desde GCS: {e}. Se continuará sin SFX.")

    # 2. Construir el prompt para Gemini
    system_prompt = get_ai_prompts().get(style, get_ai_prompts()['documental']) # 'documental' por defecto
    
    user_prompt = f"""
    {system_prompt}

    **DATOS DE ENTRADA:**
    1.  **Datos de Escenas:** {json.dumps(scenes_data, indent=2)}
    2.  **Catálogo de Efectos de Sonido Disponibles:** {json.dumps(sound_effects_catalog, indent=2)}

    **TAREA Y FORMATO DE SALIDA (CRÍTICO):**
    Analiza los datos de entrada y devuelve **ÚNICAMENTE un objeto JSON válido** que represente la "receta" de edición completa. La estructura del JSON debe ser la siguiente:

    ```json
    {{
      "scenes": [
        {{
          "scene_id": "ID_de_la_escena",
          "visual_effects": [
            {{
              "type": "ken_burns",
              "params": {{ "zoom_dir": "in", "pan_dir": "derecha", "factor_zoom": 1.1 }}
            }},
            {{
              "type": "vignette",
              "params": {{ "radio": 0.7, "suavizado": 0.5 }}
            }}
          ],
          "text_overlays": [
            {{
              "text": "Texto del Título",
              "start_time": 0.5,
              "duration": 4.0,
              "position": "center",
              "effect": {{ "type": "popup", "anim_duration": 0.5 }},
              "style": {{ "fontsize": 80, "color": "yellow" }},
              "background": {{ "padding": 20, "bg_color": [0,0,0], "bg_opacity": 0.7 }}
            }}
          ],
          "sound_effects": [
            {{
              "sfx_id": "id_del_catalogo",
              "start_time": 0.5,
              "volume": 0.8
            }}
          ],
          "transition_to_next": {{
            "type": "slide",
            "duration": 0.5,
            "direction": "izquierda"
          }}
        }}
      ]
    }}
    ```
    **REGLAS:**
    - Cada escena en la salida debe corresponder a una escena de entrada.
    - Elige efectos visuales, de texto y de sonido que encajen con el estilo solicitado.
    - `start_time` para los SFX debe tener sentido dentro de la duración de la escena.
    - La última escena no debe tener `transition_to_next`.
    """

    try:
        response = model_text.generate_content(user_prompt)
        ai_recipe = safe_json_parse(response.text)
        if not ai_recipe or 'scenes' not in ai_recipe:
            raise ValueError(f"La respuesta de la IA no es un JSON de receta válido. Respuesta: {response.text}")
        
        logging.info(f"[{job_id}] CEREBRO IA: Receta generada exitosamente por Gemini.")
        return ai_recipe
    except Exception as e:
        logging.error(f"[{job_id}] CEREBRO IA: Fallo catastrófico al generar la receta.", exc_info=True)
        raise e


# ==============================================================================
# === LOS BRAZOS: EJECUCIÓN PRECISA DEL RENDERIZADO                          ===
# ==============================================================================

def download_file(url, local_path):
    """Descarga un archivo desde una URL a una ruta local."""
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return local_path
    except Exception as e:
        logging.error(f"Error descargando {url}: {e}", exc_info=True)
        raise e

def process_video_from_recipe(job_id, original_scenes, ai_recipe):
    tmp_dir = f"/tmp/{job_id}_render"
    os.makedirs(tmp_dir, exist_ok=True)
    
    try:
        JOBS[job_id]['status'] = 'processing'
        scene_clips = []
        
        recipe_scenes = ai_recipe['scenes']
        
        for i, scene_data in enumerate(original_scenes):
            JOBS[job_id]['progress'] = f"{i + 1}/{len(original_scenes)}"
            logging.info(f"[{job_id}] BRAZOS: Componiendo escena {i+1}...")
            
            # Encuentra la receta correspondiente a esta escena
            recipe_for_scene = next((r for r in recipe_scenes if r.get("scene_id") == scene_data.get("id")), {})

            # 1. Descargar y crear clip base
            media_path = download_file(scene_data['mediaUrl'], os.path.join(tmp_dir, f"media_{i}"))
            narration_path = download_file(scene_data['audioUrl'], os.path.join(tmp_dir, f"audio_{i}"))
            
            narration_clip = AudioFileClip(narration_path)
            duration = narration_clip.duration + 0.5 # Duración basada en audio + 0.5s de margen

            # Admite tanto imágenes como videos de entrada
            if scene_data.get('mediaType', 'image') == 'video':
                base_clip = VideoFileClip(media_path).set_duration(duration).set_audio(None) # Quitar audio original
            else:
                base_clip = ImageClip(media_path, duration=duration)
            
            video_size = base_clip.size

            # 2. Aplicar efectos visuales de la receta
            for effect in recipe_for_scene.get('visual_effects', []):
                logging.info(f"  -> Aplicando efecto visual: {effect['type']}")
                if effect['type'] == 'vignette':
                    base_clip = vfx_aplicar_viñeta(base_clip, **effect.get('params', {}))
                elif effect['type'] == 'ken_burns':
                    base_clip = vfx_crear_efecto_ken_burns(base_clip, duration, video_size, **effect.get('params', {}))

            # 3. Preparar el audio (Narración + SFX)
            audio_clips_to_compose = [narration_clip]
            for sfx in recipe_for_scene.get('sound_effects', []):
                 logging.info(f"  -> Añadiendo SFX: {sfx['sfx_id']}")
                 # Aquí descargarías el sfx desde GCS usando su URL del catálogo
                 # sfx_path = download_file(sfx_url, ...)
                 # sfx_clip = AudioFileClip(sfx_path).set_start(sfx['start_time']).volumex(sfx['volume'])
                 # audio_clips_to_compose.append(sfx_clip)
            
            final_audio = CompositeAudioClip(audio_clips_to_compose)
            base_clip = base_clip.set_audio(final_audio)

            # 4. Aplicar overlays de texto de la receta
            text_clips_to_add = []
            for text_info in recipe_for_scene.get('text_overlays', []):
                logging.info(f"  -> Creando texto: '{text_info['text'][:20]}...'")
                style = text_info.get('style', {})
                effect = text_info.get('effect', {})
                background = text_info.get('background')
                
                # Crear texto con fondo si se especifica
                if background:
                    text_clip = text_fx_crear_texto_con_fondo(text_info['text'], **style, **background)
                else: # Crear texto simple
                    text_clip = TextClip(text_info['text'], **style)

                # Aplicar animación de aparición si se especifica
                if effect.get('type') == 'popup':
                    text_clip = text_fx_crear_texto_popup(text_info['text'], duracion_anim=effect['anim_duration'], **style)
                elif effect.get('type') == 'typewriter':
                    text_clip = text_fx_crear_texto_maquina_escribir(text_info['text'], duracion_total=text_info['duration'], **style)
                
                text_clip = text_clip.set_start(text_info['start_time']).set_duration(text_info['duration']).set_position(text_info['position'])
                text_clips_to_add.append(text_clip)

            # Componer la escena final
            final_scene_clip = CompositeVideoClip([base_clip] + text_clips_to_add, size=video_size)
            scene_clips.append(final_scene_clip)

        # 5. Ensamblaje final (AÚN SIMPLIFICADO - SIN TRANSICIONES)
        final_video = concatenate_videoclips(scene_clips)
        
        logging.info(f"[{job_id}] BRAZOS: Renderizado completado. Subiendo a GCS...")
        final_video_path = os.path.join(tmp_dir, "final_video.mp4")
        final_video.write_videofile(final_video_path, codec="libx264", audio_codec="aac", fps=24)
        
        public_url = upload_to_gcs(open(final_video_path, 'rb').read(), f"videos_inteligentes/{job_id}.mp4", 'video/mp4')
        
        JOBS[job_id].update({"status": "completed", "videoUrl": public_url, "progress": "100%"})
        logging.info(f"[{job_id}] ¡TRABAJO COMPLETADO! URL: {public_url}")

    except Exception as e:
        logging.error(f"[{job_id}] ERROR FATAL en los BRAZOS.", exc_info=True)
        JOBS[job_id].update({"status": "error", "error": str(e)})
    finally:
        if os.path.exists(tmp_dir):
            os.system(f"rm -rf {tmp_dir}")


# ==============================================================================
# === API ENDPOINTS Y FUNCIONES DE SOPORTE                                   ===
# ==============================================================================

@app.route("/")
def index():
    return "Renderizador Inteligente v4.0 con IA Generativa está activo."

@app.route('/api/render-video', methods=['POST'])
def render_video_endpoint():
    """
    NUEVO ENDPOINT PRINCIPAL: Recibe los medios y el estilo, y la IA crea el video.
    """
    try:
        data = request.get_json()
        if not data or 'scenes' not in data or 'style' not in data:
            return jsonify({"error": "La solicitud debe incluir 'scenes' y 'style'."}), 400
        
        scenes = data['scenes']
        style = data['style']
        
        job_id = str(uuid.uuid4())
        JOBS[job_id] = {"status": "pending_brain", "progress": "0%"}
        
        # El flujo ahora es asíncrono desde el inicio
        thread = threading.Thread(target=run_full_process, args=(job_id, scenes, style))
        thread.start()
        
        return jsonify({"message": "Trabajo de renderizado inteligente aceptado.", "jobId": job_id}), 202
    except Exception as e:
        logging.error("Error al iniciar /api/render-video", exc_info=True)
        return jsonify({"error": f"Error interno del servidor: {e}"}), 500

def run_full_process(job_id, scenes, style):
    """Función que encapsula el cerebro y los brazos para correr en un hilo."""
    try:
        # 1. El Cerebro con IA crea la receta
        ai_recipe = create_ai_recipe(job_id, scenes, style)
        JOBS[job_id]['status'] = 'pending_render'
        
        # 2. Los Brazos ejecutan la receta
        process_video_from_recipe(job_id, scenes, ai_recipe)
    except Exception as e:
        logging.error(f"[{job_id}] Fallo en el hilo principal del proceso.", exc_info=True)
        JOBS[job_id].update({"status": "error", "error": f"Fallo en la fase de IA: {e}"})

@app.route('/api/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Trabajo no encontrado."}), 404
    return jsonify(job)

# --- EJECUCIÓN DEL SERVIDOR ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
