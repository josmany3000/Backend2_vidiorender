# efectos_texto/text_fx.py

import math
from moviepy.editor import *

def crear_texto_suave(texto, duracion_total, duracion_fade, **kwargs):
    txt_clip = TextClip(txt=texto, **kwargs)
    return txt_clip.set_duration(duracion_total).fadein(duracion_fade).fadeout(duracion_fade)

def crear_texto_con_fondo(texto, padding=20, bg_color=(0,0,0), bg_opacity=0.6, **kwargs):
    clip_texto = TextClip(txt=texto, **kwargs)
    size_fondo = (clip_texto.w + padding * 2, clip_texto.h + padding * 2)
    clip_fondo = ColorClip(size=size_fondo, color=bg_color).set_opacity(bg_opacity)
    return CompositeVideoClip([clip_fondo.set_position('center'), clip_texto.set_position('center')])

def crear_texto_popup(texto, duracion_anim, **kwargs):
    def ease_out_back(t_norm):
        c1 = 1.70158
        c3 = c1 + 1
        return 1 + c3 * pow(t_norm - 1, 3) + c1 * pow(t_norm - 1, 2)
    def resize_func(t):
        return ease_out_back(t / duracion_anim) if t < duracion_anim else 1
    clip_texto = TextClip(txt=texto, **kwargs)
    return clip_texto.fx(vfx.resize, resize_func)

def crear_texto_maquina_escribir(texto, duracion_total, fps=30, **kwargs):
    clips_letras = []
    duracion_por_letra = duracion_total / len(texto)
    for i in range(len(texto)):
        texto_visible = texto[:i + 1]
        clip_letra = TextClip(txt=texto_visible, **kwargs)
        clips_letras.append(clip_letra.set_duration(duracion_por_letra))
    return concatenate_videoclips(clips_letras).set_fps(fps)

def crear_texto_karaoke(word_timestamps, video_size, posicion_y='center', **kwargs):
    clips_a_componer = []
    color_normal = kwargs.pop('color', 'white')
    color_resaltado = kwargs.pop('highlight_color', 'yellow')
    
    frase_completa = " ".join(d['word'] for d in word_timestamps)
    clip_frase_completa = TextClip(frase_completa, color=color_normal, **kwargs)
    
    x_inicial = (video_size[0] - clip_frase_completa.w) / 2
    clip_fondo = clip_frase_completa.set_position((x_inicial, posicion_y))
    clips_a_componer.append(clip_fondo)
    
    ancho_espacio = TextClip(" ", **kwargs).w
    x_actual = x_inicial
    
    for item in word_timestamps:
        palabra = item['word']
        clip_palabra_resaltada = TextClip(palabra, color=color_resaltado, **kwargs)
        clip_palabra_resaltada = clip_palabra_resaltada.set_start(item['start_time']).set_duration(item['end_time'] - item['start_time']).set_position((x_actual, posicion_y))
        clips_a_componer.append(clip_palabra_resaltada)
        x_actual += clip_palabra_resaltada.w + ancho_espacio
        
    duracion_total_karaoke = word_timestamps[-1]['end_time']
    return CompositeVideoClip(clips_a_componer, size=video_size).set_duration(duracion_total_karaoke)
