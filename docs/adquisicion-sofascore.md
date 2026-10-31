# Adquisición desde SofaScore: estado vigente

Actualizado el 29-10-2026. Punto de entrada; los informes de los pilotos están en [historico/](historico/README.md).
Despliegue: **Docker Compose**, con el overlay `app-real-jaen/compose.acquisition.yaml` (procedimiento en
`dashboard/docs/docker.md`, «Adquisición»). Contrato y garantías: [contrato-adquisicion-interno.md](contrato-adquisicion-interno.md).
Desde la interfaz, el operador lanza por defecto la receta del Grupo 2, «Actualizar Grupo 2»
(`dashboard/docs/contrato-actualizaciones.md`). Consulta los resultados que faltan, los aplazados, los cambios de
calendario y las correcciones de las últimas 72 h, y publica con cobertura parcial
([diseno-receta-grupo2.md](diseno-receta-grupo2.md)). Primera ejecución real el 29-10-2026, una sola vez,
sobre una copia aislada y con los detalles restringidos a la jornada 9: [prueba-real-grupo2.md](prueba-real-grupo2.md).
La receta del piloto (calendario de la jornada 1 y un partido) se conserva para las pruebas.
Perfil del navegador: [perfil-adquisicion-diseno-final.md](perfil-adquisicion-diseno-final.md).

## Flujo que funciona

El flujo normal del scraper (revisión `611e436`, sin modificar): curl_cffi con impersonación y, ante un 403 con
challenge, su BrowserBridge (Scrapling `StealthySession`, Turnstile resuelto automáticamente) hace las peticiones
con `fetch()` desde una página del sitio. Ejecuciones reales autorizadas, cada una una sola vez, con perfil y caché
nuevos y aislados:

| | 27-10 13:05, Windows, tráfico histórico | 27-10 16:36, Windows, tráfico reducido | 28-10 16:16, Docker Linux, tráfico reducido |
|---|---|---|---|
| Calendario ronda 1 | 200 directo por curl | 403 con challenge → navegador, 200 | 403 con challenge → navegador, 200 |
| Evento 16599363 y sus `statistics`, `lineups`, `incidents` | navegador, 200 | navegador, 200 | navegador, 200 |
| Envíos API propios | 6 | 6 | 6 (1 curl + 5 navegador) |
| Peticiones de las páginas | 878 | 17 | 15 (Turnstile, `/robots.txt`, los 5 `fetch()`) |

Las respuestas deportivas del 28-10 son idénticas a las del 27-10 16:36; solo cambian contadores de seguidores
(`userCount`). La ejecución en Docker usó la imagen `sha256:dd733f26…` construida desde `git archive` del commit
`69ac18f`, sin código montado, con sandbox acreditado y HTTPS verificado; su bundle se conserva sellado en el
volumen `rj-real-sdk-37b257e89b03-exchange` (informes en `%TEMP%\rj-real-sdk-docker-real`).

## Componentes

- `acquisition/sdk_adapter.py`: el flujo del SDK con verificación HTTPS, service workers bloqueados, sandbox de
  Chromium acreditado por `/proc` (`acquisition.browser.verify_sandbox`), un único perfil (nunca uno temporal),
  tráfico reducido (`/robots.txt` como página y `redirectUrl` del captcha, página del captcha aparcada tras la
  cookie, dominios de terceros bloqueados en las páginas de Scrapling, sin Media Router) y lista cerrada de rutas
  con presupuesto: un 403 sin challenge, un challenge repetido, un 429/503, un fallo del navegador o una ruta fuera
  de la lista detienen todo envío.
- `acquisition/transport.py`: presupuesto durable anotado antes de cada envío, reintentos, bloqueo administrativo
  persistente tras 403/429, plazo de 300 s, cancelación y evidencia de cada respuesta. `SdkTransport` envía por el
  adaptador.
- `acquisition/recipe.py`: receta fija `sofascore-pilot-16599363-v1` (ronda 1 y partido 16599363), caché inicializada
  desde el inventario, bundle sellado con cobertura y procedencia; `producer_rules()` es la huella del productor.
- `acquisition/job.py`: un trabajo en su propio proceso (un intento por ruta, 8 envíos físicos) y registro de
  conexiones. `acquisition/service.py`: servicio HTTP interno. `persistencia/adquisicion.py`: cliente y receptor del
  backend. `persistencia/bundle.py`: inventario y verificación.

## Salida de red y lo que se observa

**No es un registro exhaustivo del tráfico.** En el overlay, solo `acquisition` está en la red con salida
(`acquisition-egress`). Docker no filtra por dominio y no se ha añadido proxy: desde ese contenedor cualquier
destino TCP/UDP es alcanzable, también el anfitrión y la red local, y la resolución DNS usa el resolvedor interno de
Docker. Lo que se limita es lo que inicia el código: curl solo a las rutas permitidas de `www.sofascore.com/api/v1`,
el navegador a `/robots.txt`, el captcha y el challenge de Cloudflare, y `fetch()` a las rutas permitidas. Chromium
abre además conexiones de fondo que ninguna página pide (el 28-10: varias IP de Google, también el puerto 5228, y
una de Fastly de origen desconocido) y no se pueden impedir con las opciones actuales.

Cada trabajo deja en su informe (`GET …/{id}/report`, sin cuerpos ni cookies):

- los envíos físicos del adaptador con su estado (completo para curl y `fetch()`);
- las peticiones de las páginas del navegador (dominio, ruta, estado), no las internas de Chromium;
- una **muestra** de los sockets del espacio de red del contenedor leída de `/proc/net` cada 0,2 s: IP, puerto y
  estados TCP, no nombres. Puede perder una conexión que el servidor cierre en menos de 0,2 s (el 28-10 no se vio
  la primera conexión de curl, que recibió un 403) y no ve UDP sin conectar; no registra DNS ni contenido cifrado,
  ni el tráfico de la máquina virtual de Docker Desktop.

## Limitaciones abiertas

- Una sola muestra real por entorno (un partido, una IP); sin datos de estabilidad con el inventario completo.
- El acceso es variable (el mismo calendario dio 200 por curl y, horas después, challenge): la solución cuenta
  siempre con el paso por el navegador.
- Una receta fija. Ampliar el ámbito (más partidos, política histórica) requiere otra receta revisada y su huella
  aprobada en `productores-adquisicion.json`.

## Correcciones a conclusiones anteriores

- La URL del calendario de la jornada 1 SÍ se obtuvo el 22-10 con el flujo normal (la caché del scraper conserva
  `round_1` con `fetched_at = 2026-10-22T18:02:18Z`), en contra de [historico/comparacion-descarga-y-pilotos.md](historico/comparacion-descarga-y-pilotos.md).
- Los dos 403 de los pilotos no prueban un bloqueo del flujo normal: el piloto HTTP no tenía navegador y el
  segundo prohibía resolver el challenge.

## Experimentos sustituidos y retirados

| Experimento | Sustituido por | Dónde está |
|---|---|---|
| Piloto HTTP directo de cinco GET (urllib), 403 el 27-10 | `SdkTransport` por el flujo del SDK | El transporte urllib sigue en `acquisition/transport.py` solo para pruebas en loopback; informes en `historico/` |
| Runner de calendario con navegador limitado y pasarela TLS de un GET | BrowserBridge del SDK con tráfico reducido | Fuera del repositorio: `C:\Users\broke\dev\archivo-api-real-jaen\2026-10-27-pasarela-calendario\` |
| Protocolo v2 del piloto (bundle, receptor, ejecutores en contenedores sueltos, receta offline) | Protocolo unificado del servicio | Retirado el 28-10 con sus garantías cubiertas por pruebas del definitivo ([contrato-adquisicion-interno.md](contrato-adquisicion-interno.md), «Lo retirado»); documentación en `historico/` |
| Integración simulada v1 (proveedor de fixtures, bundle de un archivo) | Proveedor `fixture-site` del mismo servicio | `historico/contrato-adquisicion-interno-v1-simulado.md` |
| Prueba real única en Docker (`scripts/prueba_real_sdk.py`, `real_sdk.py`, `Dockerfile.real`) | Servicio `acquisition` | En el commit `69ac18f`; su evidencia, en el volumen y la carpeta citados arriba |
| Recuperación de perfil con adaptador diagnóstico | [gestion-perfil-adquisicion.md](gestion-perfil-adquisicion.md) | `historico/recuperacion-perfil-linux.md` |

Prototipo y banco de pruebas en Windows, fuera del repositorio: `C:\Users\broke\dev\prueba-scraper-normal\`.

## Docker: receta y sandbox

- Imagen base del scraper (auditada, ID `e13cef…`): `docker/scraper/`, procedimiento en
  [scraper-empaquetado-linux.md](scraper-empaquetado-linux.md). Sobre ella, `docker/acquisition/Dockerfile`
  (`runtime` para el servicio y la administración del perfil, `test` con los instrumentos).
- Reproducción y validación del sandbox: `docker/scraper/policies/` y los diagnósticos de `docker/scraper/`
  ([diagnostico-sandbox-linux.md](diagnostico-sandbox-linux.md)). Política fijada: `docker/acquisition/seccomp-linux-amd64.json`.
- Pendiente de revisión de la imagen base: `offline_probe.py` como punto de entrada y dependencias SQL innecesarias.
