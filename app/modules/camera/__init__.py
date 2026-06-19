"""Módulo de cámara para consumo externo (Ignition).

Expone snapshots de cámara vía API REST formal, desacoplado del módulo LPR.
La fuente actual es una webcam USB local, pero el contrato (status, snapshot
JPEG, snapshot persistido como evidencia) está pensado para no cambiar cuando
se migre a una cámara IP RTSP.
"""
