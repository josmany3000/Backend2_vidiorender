# efectos_visuales/vfx.py

import numpy as np
from moviepy.editor import *
from moviepy.video.fx.all import *
import requests
import os

def aplicar_correccion_color(clip, brillo=0, contraste=0, saturacion=1.0, tinte=None):
    clip_procesado = clip
    if saturacion != 1.0:
        clip_procesado = clip_procesado.fx(colorx, factor=saturacion)
    if brillo != 0 or contraste != 0:
        clip_procesado = clip_procesado.fx(lum_contrast, lum=brillo, contrast=contraste)
    if tinte:
        color_tinte, opacidad_tinte = tinte
        tinte_clip = ColorClip(size=clip.size, color=color_tinte, duration=clip.duration).set_opacity(opacidad_tinte)
        clip_procesado = CompositeVideoClip([clip_procesado, tinte_clip])
    return clip_procesado

def aplicar_filtro(clip, tipo_filtro='b&n'):
    if tipo_filtro == 'b&n':
        return clip.fx(blackwhite)
    elif tipo_filtro == 'sepia':
        clip_bn = clip.fx(blackwhite)
        tinte_sepia = ColorClip(size=clip.size, color=[112, 66, 20], duration=clip.duration).set_opacity(0.4)
        return CompositeVideoClip([clip_bn, tinte_sepia])
    elif tipo_filtro == 'invertir':
        return clip.fx(invert_colors)
    return clip

def aplicar_viñeta(clip, radio=0.7, suavizado=0.4, color=(0, 0, 0)):
    w, h = clip.size
    y, x = np.ogrid[0:h, 0:w]
    dist_centro = np.sqrt(((x - w / 2) / (w / 2))**2 + ((y - h / 2) / (h / 2))**2)
    mascara = np.clip((dist_centro - radio) / suavizado, 0, 1)
    viñeta_overlay = ColorClip(size=(w, h), color=color, duration=clip.duration)
    viñeta_con_mascara = viñeta_overlay.set_mask(ImageClip(mascara * 255, ismask=True))
    return CompositeVideoClip([clip, viñeta_con_mascara])

def aplicar_cambio_velocidad(clip, factor=1.0):
    return clip.fx(speedx, factor=factor)

def generar_overlay_grano(w, h, duracion, fps=30, intensidad=0.08):
    num_frames = int(duracion * fps)
    fuerza_ruido = int(255 * intensidad)
    frames = []
    for _ in range(num_frames):
        ruido = np.random.normal(loc=128, scale=fuerza_ruido, size=(h, w))
        ruido = np.clip(ruido, 0, 255).astype(np.uint8)
        frame_grano = np.dstack([ruido, ruido, ruido])
        frames.append(frame_grano)
    return ImageSequenceClip(frames, fps=fps)

def aplicar_overlay_textura(clip, tipo_textura='grano', intensidad=0.08, opacidad=0.15):
    if tipo_textura == 'grano':
        textura_overlay = generar_overlay_grano(w=clip.w, h=clip.h, duracion=clip.duration, fps=clip.fps, intensidad=intensidad)
        return CompositeVideoClip([clip, textura_overlay.set_opacity(opacidad)])
    return clip

def crear_efecto_ken_burns(clip, duracion, video_size=(1280, 720), zoom_dir='in', pan_dir='derecha', factor_zoom=1.5):
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
