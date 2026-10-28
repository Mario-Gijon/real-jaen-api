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

### Adaptador integrado: `acquisition/sdk_adapter.py` (sin modificar el scraper)

1. Sesión del navegador con `additional_args` `ignore_https_errors=False`, `service_workers='block'`,
   `permissions=[]` y `chromium_sandbox=True`. Scrapling 0.4.15 fija lo contrario en `StealthySessionMixin`.
2. Un único perfil, el indicado (en Linux, la generación de la sesión gestionada de `acquisition/profile.py`):
   `profile_lock.secondary_profile` prohibido y arranque rechazado si el bridge usa otra ruta. En Linux, el sandbox
   de ese Chromium se acredita por `/proc` con `acquisition.browser.verify_sandbox` (la misma comprobación de la
   gestión de perfil).
3. Tráfico reducido: `BrowserBridge` usa `/robots.txt` (mismo origen, sin subrecursos) como página y como
   `redirectUrl` del captcha (aceptado por el sitio el 27-10); tras obtener la cookie, la página del captcha queda en
   `about:blank`; `blocked_domains` de Scrapling para publicidad y analítica (solo actúa en las páginas de su
   `fetch()`); `--disable-features=MediaRouter,GlobalMediaControls`.
4. `configure_environment()`: datos, log, throttle y configuración del SDK propios; `RETRIES=0`, `RATE=1`,
   `MAX_CONCURRENT=1`, `TIMEOUT_SECONDS=10`; rechaza variables de proxy o certificados.
5. Cada envío físico (curl síncrono o `fetch()` del navegador) pasa por la lista cerrada de rutas y un presupuesto;
   la sesión curl asíncrona (con su warm-up) no se usa. Un 403 sin challenge, un challenge repetido en la página,
   un 429/503, un fallo del navegador o una ruta fuera de la lista detienen el adaptador sin más envíos.

`request(path)` devuelve (estado, cabeceras, cuerpo). Con el protocolo v2, `acquisition/sdk_transport.py` sustituye
solo el envío de `PilotTransport`, que conserva lista fija, presupuesto durable, reintentos, paradas, plazo,
cancelación y evidencia; inventario, cobertura y generación siguen en `pilot_bundle`, `bundle_v2` y
`adquisicion_v2`. `producer_rules()` incluye ambos módulos.

Validación sin red, con los cuerpos descargados el 27-10 (`round_1.json` y `raw-16599363.jsonl` de la segunda prueba
real) servidos por `docker/acquisition/sdk_fixture.py` (challenge, captcha con cookie, `/robots.txt`):

- `docker/acquisition/test_sdk_adapter.py`, 8 escenarios en procesos nuevos con el navegador real: challenge
  resuelto con tráfico reducido, 403 sin challenge, challenge irresoluble, 429 en el navegador, ruta no permitida,
  perfil ocupado sin perfil temporal y TLS del navegador y de curl con certificado no confiable. Pasan en Windows
  (venv del scraper; sin acreditar sandbox) y dentro del contenedor con `RJ_REQUIRE_SANDBOX=1`.
- `scripts/test_pilot_v2.py --sdk-data <carpeta>`: recorrido completo en contenedores sin red. Adquisición con
  la política seccomp fijada, perfil gestionado inicializado, sesión exclusiva y sandbox acreditado; dos trabajos;
  recepción con autenticación, cuatro bundles manipulados rechazados, fallo tras la candidata sin publicar,
  publicación, duplicado idempotente, generación obsoleta rechazada, identificadores y decisiones conservados y
  2792 rutas equivalentes. Sin `--sdk-data`, el recorrido con urllib sigue igual.

```sh
python scripts/test_pilot_v2.py --snapshot <copia inactiva identificada> --backend-image <backend de integración> \
  --acquisition-image <imagen con SDK y Chromium> --scraper-source <checkout 611e436> \
  --sdk-data <carpeta con round_1.json y raw-16599363.jsonl> --output <carpeta nueva>
```

Prototipo y banco de pruebas que precedieron a la integración, FUERA del repositorio:
`C:\Users\broke\dev\prueba-scraper-normal\`. Informes de las ejecuciones reales: carpetas `rj-prueba-real-20261027-a`
y `-b` del directorio temporal del usuario.

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

Hecho el 28-10-2026: adaptador integrado y validado sin red en local y en Docker (arriba).

Prueba real en Docker, preparada (`scripts/prueba_real_sdk.py`, etapa `docker/acquisition/real_sdk.py`,
`Dockerfile.real`, verificación `pilot_backend_check.py verify-real`):

1. `construir`: imagen desde `git archive` de un commit (sin red, sin código montado), con etiqueta del commit;
   comprueba que `/opt/app` contiene exactamente los archivos del commit previstos (ningún ensayo, sitio simulado ni
   instrumento) y que el SDK es el de `611e436`. Deja `build-record.json` con los ID.
2. `ejecutar --modo ensayo`: todo el recorrido con la adquisición en red `none` (la primera petición falla en DNS y
   la cadena de paradas termina sin bundle). `--modo real --confirmar sofascore-16599363-una-ejecucion`: red bridge
   propia de esa ejecución y registro local de una sola vez (`%LOCALAPPDATA%\rj-acquisition\real-runs`).
3. Límites: 5 rutas fijas, un intento por ruta (cualquier fallo detiene el trabajo), 8 envíos físicos, 300 s de
   trabajo, vigilante de 420 s en el contenedor y 480 s en el ejecutor; un arranque del navegador, sandbox
   acreditado, HTTPS verificado y tráfico reducido. El contenedor de adquisición solo monta intercambio y perfiles.
4. Salida: Docker no filtra por dominio y no se añade proxy. Se registran los envíos del adaptador, las peticiones
   de las páginas y todas las conexiones TCP/UDP del espacio de red del contenedor (`/proc/net`, cada 0,2 s), también
   las de fondo de Chromium. No se registran nombres DNS.
5. Incertidumbre que solo resuelve la prueba: si el challenge se resuelve igual en Chromium headless de Linux
   dentro del contenedor (misma IP de salida que Windows a través de Docker Desktop).

Después, para Docker Compose: receta fija real en el protocolo v1 versionado (`acquisition/service.py`,
`persistencia/adquisicion.py`) con las garantías 1-5, servicio en el overlay de Compose, revisión de la imagen base
(`offline_probe.py` como punto de entrada, dependencias SQL innecesarias) y, con todo ello, retirar el protocolo v2.
