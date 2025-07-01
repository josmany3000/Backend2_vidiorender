# transiciones/transitions.py

from moviepy.editor import *

def fundido_cruzado(clip1, clip2, duracion):
    return crossfadein(clip2.set_duration(duracion), clip1)

def deslizamiento(clip1, clip2, duracion, direccion="izquierda"):
    w, h = clip1.size
    
    def pos_clip1(t):
        if direccion == "izquierda": return (-w * t / duracion, 0)
        elif direccion == "derecha": return (w * t / duracion, 0)
        elif direccion == "arriba": return (0, -h * t / duracion)
        else: return (0, h * t / duracion)

    def pos_clip2(t):
        if direccion == "izquierda": return (w - w * t / duracion, 0)
        elif direccion == "derecha": return (-w + w * t / duracion, 0)
        elif direccion == "arriba": return (0, h - h * t / duracion)
        else: return (0, -h + h * t / duracion)

    clip1_animado = clip1.set_position(pos_clip1)
    clip2_animado = clip2.set_position(pos_clip2)
    
    return CompositeVideoClip([clip2_animado, clip1_animado], size=(w,h)).set_duration(duracion)

# Puedes añadir aquí la función de 'wipe' y otras que creamos.
