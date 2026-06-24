# LPR continuo con SimpleLPR

Este monitor es el proveedor principal temporal del PoC. Mantiene abierto el
RTSP, usa el tracker de SimpleLPR y publica cada placa o rotulo en
`C:\Users\Public\hgac_lpr.json`. El Timer Script de Ignition que ya consume ese
archivo no necesita cambios para placas.

## Preparacion

1. Instalar el SDK de SimpleLPR compatible con Python 3.8-3.12.
2. Copiar las variables de `.env.example` al `.env` de la raiz.
3. Colocar la URL RTSP real solamente en `.env`.
4. Ejecutar este monitor como proceso independiente durante el PoC. El endpoint
   OpenCV/EasyOCR permanece disponible como respaldo bajo demanda.

## Ejecucion

```powershell
.\.venv\Scripts\python.exe .\scripts\lpr\simplelpr_rtsp_monitor.py
```

El proceso queda monitoreando hasta presionar `Ctrl+C`. Cada evento alterna el
booleano `trigger`, evitando que Ignition pierda lecturas consecutivas.

Las placas llenan el contrato actual. Los rotulos se publican en `rotulo` y
`raw_result`; para mostrarlos como tag dedicado se debe agregar `LPR/Rotulo` al
UDT y al Timer Script de ingesta.

## Limitaciones conocidas

- SimpleLPR entra en evaluacion si no se configura `SIMPLELPR_PRODUCT_KEY`.
- Republica Dominicana no tiene plantilla nativa. Se aceptan estrictamente
  `A123456`, `AB12345` y rotulo `A123`.
- Una correccion OCR reduce la confianza; nunca se agregan caracteres faltantes.
