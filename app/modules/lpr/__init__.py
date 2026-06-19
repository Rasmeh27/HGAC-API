"""Módulo LPR: lectura de placa sobre un frame de cámara.

Separado del módulo `camera` (que captura frames/snapshots/stream) y del futuro
Decision Engine (que decidirá el acceso). El LPR toma un frame de `CameraService`,
detecta/lee la placa, normaliza, guarda evidencia en `evidence/lpr/` y devuelve
un resultado estructurado. No abre la cámara directamente ni decide cruces.
"""
