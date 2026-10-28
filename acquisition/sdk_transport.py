"""Transporte del protocolo v2 que envía cada petición por el flujo normal del SDK (acquisition.sdk_adapter).

Hereda de PilotTransport todo lo demás: lista fija de rutas, presupuesto durable anotado antes de cada envío,
reintentos y esperas, parada administrativa ante 403/429, plazo, cancelación y evidencia de cada respuesta. Solo
cambia cómo sale una petición: curl_cffi del SDK y, ante un challenge, su navegador, en lugar de urllib.
"""
from .pilot_transport import PilotTransport


class SdkTransport(PilotTransport):
    via = 'sdk'

    def __init__(self, root, *, adapter, **kwargs):
        super().__init__(root, **kwargs)
        if adapter.api != self.base:
            raise ValueError('El adaptador y el transporte deben usar la misma raíz API')
        self.adapter = adapter

    def _request(self, path):
        return self.adapter.request(path)
