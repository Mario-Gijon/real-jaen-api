# Adquisición desde SofaScore: estado vigente

Actualizado el 27-10-2026. Sustituye como punto de entrada a los informes de los pilotos, que quedan en
[historico/](historico/README.md). Objetivo de despliegue: **Docker Compose** (rj-analyst/compose.yaml),
con la adquisición en su propio servicio según [contrato-adquisicion-interno.md](contrato-adquisicion-interno.md)
y [perfil-adquisicion-diseno-final.md](perfil-adquisicion-diseno-final.md).

## Lo que funciona hoy (demostrado en Windows nativo, todavía NO en Docker)

El flujo normal del scraper (revisión `611e436`, sin modificar): curl_cffi con impersonación y, ante un 403 con
challenge, su BrowserBridge (Scrapling `StealthySession`, Turnstile resuelto automáticamente, autorizado por el
usuario) hace las peticiones con `fetch()` desde una página del sitio. Dos ejecuciones reales autorizadas, cada una
una sola vez, con caché, perfil, log y configuración nuevos y aislados:

| | 13:05 UTC, tráfico histórico | 16:36 UTC, tráfico reducido |
|---|---|---|
| Calendario `/unique-tournament/17073/season/97382/events/round/1` | 200 directo por curl | 403 con challenge → navegador, 200 |
| `/event/16599363` + `statistics`, `lineups`, `incidents` | navegador, 200 | navegador, 200 |
| Peticiones API propias / warm-ups | 6 / 2 | 6 / 2 |
| Peticiones del navegador | 878 (112 llamadas API de la propia portada, publicidad, analítica) | 17 (Turnstile, canje del token, `/robots.txt`, nuestros 5 `fetch()`) |
| Conexiones TCP salientes | 18, incluida Cast en la red local | 5 (sitio y servicios de fondo de Chromium) |

Los cuerpos de ambas ejecuciones son idénticos byte a byte. Frente a la exportación del 21-10 solo cambian
contadores de seguidores, un nombre de equipo en el calendario y 1090 ceros explícitos nuevos en la alineación
(tratados por la regla de conflictos de [normalizacion-estadisticas-individuales.md](normalizacion-estadisticas-individuales.md), 4.3).
La exportación raw JSONL del SDK la consumen sin cambios `importar.py` y `python -m persistencia importar`
(validado en una copia aislada: originales y selección conservados, verificación sin cambios en seco).

### Ajustes al SDK aplicados en memoria (sin modificar el scraper)

1. Sesión del navegador con `additional_args` `ignore_https_errors=False`, `service_workers='block'`,
   `permissions=[]`. Scrapling 0.4.15 fija lo contrario en `StealthySessionMixin`; la prueba local con certificado
   no confiable y el control con los valores por defecto demuestran la diferencia.
2. `profile_lock.secondary_profile` prohibido: perfil ocupado = parada, nunca un perfil temporal hermano.
3. Tráfico reducido: `BrowserBridge` usa `/robots.txt` (mismo origen, sin subrecursos) como página y como
   `redirectUrl` del captcha (aceptado por el sitio el 27-10); tras obtener la cookie, la página del captcha queda en
   `about:blank`; `blocked_domains` de Scrapling para publicidad y analítica (solo actúa en las páginas de su
   `fetch()`); `--disable-features=MediaRouter,GlobalMediaControls`.
4. Configuración por variables `SOFASCORE_*`: datos, perfil, log, throttle y configuración propios;
   `RETRIES=0`, `RATE=1`, `MAX_CONCURRENT=1`, `TIMEOUT_SECONDS=10`, `DEFAULTS__SLICES=statistics,lineups,incidents`.
5. Guardas en los tres puntos de envío del SDK (sesión curl asíncrona, curl síncrono, `BrowserBridge._api_fetch`):
   lista cerrada de recursos, presupuesto, plazo global con cierre forzado y parada sin reintento ante 403 sin
   challenge, challenge repetido en la página, 429/503, error de red o de certificado, ruta no permitida o perfil
   ocupado.

Prototipo y banco de pruebas local (15 escenarios, 82 comprobaciones, más la comparación de tráfico), FUERA del
repositorio: `C:\Users\broke\dev\prueba-scraper-normal\` (`prueba_scraper.py`, `servidor_local.py`,
`probar_local.py`, `probar_trafico.py`). Informes de las ejecuciones reales: carpetas
`rj-prueba-real-20261027-a` y `-b` del directorio temporal del usuario. Pendiente de llevar al repositorio (abajo).

### Correcciones a conclusiones anteriores

- La URL del calendario de la jornada 1 SÍ se obtuvo el 22-10 con el flujo normal: la caché del scraper conserva
  `round_1` con `created_at = fetched_at = 2026-10-22T18:02:18Z`, tras un 403 con challenge resuelto por el
  navegador ([historico/comparacion-descarga-y-pilotos.md](historico/comparacion-descarga-y-pilotos.md) decía lo contrario).
- Los dos 403 de los pilotos no prueban un bloqueo del flujo normal: el piloto HTTP no tenía navegador y el
  segundo prohibía resolver el challenge.
- El acceso es variable: el mismo calendario dio 200 por curl a las 13:05 y challenge a las 16:36. La solución
  debe contar siempre con el paso por el navegador.

### Abierto

- Chromium abre conexiones de fondo a Google (también el puerto 5228) que no pide ninguna página; las opciones
  actuales no las evitan. El monitor no ve QUIC/UDP y puede perder conexiones TCP muy breves.
- Una sola muestra (un partido, una IP); sin datos de estabilidad con el inventario completo (unos 380 eventos).

## Experimentos sustituidos

| Experimento | Sustituido por | Dónde está |
|---|---|---|
| Piloto HTTP directo de cinco GET (urllib), 403 el 27-10 | Cliente normal del SDK | Código `acquisition/pilot_transport.py` (conservado: lo usan el protocolo v2 y sus pruebas); informes en `historico/` |
| Runner de calendario con navegador limitado, pasarela TLS/proxy de un único GET, 403 el 27-10 | BrowserBridge del SDK con tráfico reducido | Código retirado del repositorio a `C:\Users\broke\dev\archivo-api-real-jaen\2026-10-27-pasarela-calendario\` (manifiesto SHA-256 y diff de los ganchos que añadía a `acquisition/browser.py`, que vuelve a su versión validada); informes en `historico/` |
| Recetas iniciales «primera» y «offline» | [piloto-sofascore-tecnico.md](piloto-sofascore-tecnico.md) y este documento | `historico/` |
| Recuperación de perfil con adaptador diagnóstico | Gestión de perfil implementada ([gestion-perfil-adquisicion.md](gestion-perfil-adquisicion.md)) | `historico/recuperacion-perfil-linux.md` |
| Comparación descarga/pilotos | Este documento (con la corrección anterior) | `historico/` |

## Protocolo v2: se conserva hasta igualar sus garantías

[piloto-sofascore-tecnico.md](piloto-sofascore-tecnico.md) describe el protocolo; validaciones en
[piloto-sofascore-validacion.json](piloto-sofascore-validacion.json) y [sofascore-offline-validacion.json](sofascore-offline-validacion.json).

- Código: `persistencia/bundle_v2.py`, `persistencia/adquisicion_v2.py`, `acquisition/pilot_bundle.py`,
  `acquisition/pilot_transport.py`, `acquisition/sofascore_offline.py`, `acquisition/real_bundle.py`,
  `acquisition/sofascore-scope.json`, `generacion_esperada` en `persistencia/actualizacion.py` (sin commit).
- Pruebas y ejecutores: `docker/acquisition/{Dockerfile.pilot,pilot_backend_check.py,test_pilot_v2.py,test_sofascore_offline.py}`,
  `scripts/{build_pilot_image.py,plan_sofascore_evidence.py,test_pilot_v2.py,test_sofascore_offline.py}`.
- No entran en las imágenes de producción ni de adquisición: exclusiones en `.dockerignore` y
  `acquisition/.dockerignore` (comprobado exportando los contextos de construcción). `Dockerfile.pilot` prepara
  su propio contexto.

Garantías que la solución definitiva debe demostrar antes de retirarlo:

1. Inventario tomado de la generación vigente (IDs vigentes e históricos, sustituciones locales), no de una lista fija.
2. Cobertura por recurso `complete/partial/none`: un parcial nunca se publica; la ausencia no se infiere de un vacío o 404.
3. Control de generación: si la generación cambia entre preparar y publicar, se rechaza (con prueba en la suite).
4. Bundle inmutable verificado (huella, identidad, límites, nombres y tipos) antes del coordinador.
5. Procedencia de cada original (`fetched_at`, intento) y reutilización sin refechar lo no consultado.

## Docker: receta y sandbox

- Imagen base del scraper (auditada, ID `e13cef…`): `docker/scraper/Dockerfile`, `export_source.py`,
  `offline_probe.py`, `validate.sh`; procedimiento en [scraper-empaquetado-linux.md](scraper-empaquetado-linux.md).
  Sobre ella: `docker/acquisition/Dockerfile` y `Dockerfile.integration` (versionados).
- Reproducción y validación del sandbox (se conservan): `docker/scraper/policies/` (con su README),
  `trace_chromium.py`, `trace_offline_browser.py`, `propose_userns_policy.py`, `sandbox_diagnostic.py`,
  `verify_userns_artifacts.py`, `profile_owner_probe.py`, `test_profile_recovery.py`, `test_full_sandbox.py`
  y [diagnostico-sandbox-linux.md](diagnostico-sandbox-linux.md). Política fijada:
  `docker/acquisition/seccomp-linux-amd64.json`.

## Pendiente para integrar y validar en Docker

1. Llevar al adaptador de `acquisition/` los ajustes 1-5 anteriores, con sus pruebas locales (servidor de
   127.0.0.1, sin red externa), y una receta fija sobre el protocolo v1 versionado (`acquisition/service.py`,
   `persistencia/adquisicion.py`) que aporte las garantías 1-5.
2. Prueba real autorizada DENTRO del contenedor de adquisición: el acceso solo se ha demostrado en Windows nativo.
   Comprobar challenge, red de salida, sandbox con la política fijada y tráfico reducido.
3. Revisar la imagen base: `offline_probe.py` como punto de entrada y dependencias SQL innecesarias.
4. Decidir qué hacer con las conexiones de fondo de Chromium.
5. Con 1-2 cumplidos, retirar el protocolo v2 y sus pruebas.
