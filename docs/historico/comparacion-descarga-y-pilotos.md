# Descarga histórica y pilotos 403: comparación offline

Conclusión: los dos pilotos no reprodujeron el transporte completo de la descarga histórica. Hay evidencia de que aquel flujo gestionó un challenge y descargó datos, pero no de que funcione ahora ni de que pueda trasladarse sin cambios a nuestro aislamiento Linux. Reutilizar adquisición/exportación del SDK es más sencillo que seguir ampliando transportes diagnósticos. El acceso fresco al proveedor sigue sin acreditarse.

No se han hecho consultas al proveedor ni ejecutado cliente, CLI, doctor, sync, export o servidor del scraper. Solo lectura de código, originales, manifiestos, logs y metadatos de paquetes; se contrastaron versiones en un contenedor network:none. No se abrieron bases activas ni caché del scraper con sus funciones. No se inspeccionaron cookies o valores de secretos. No se modifican datos, servicios, scraper o .env.local; sin commit ni push.

## Evidencia histórica concreta

Fuente: C:/Users/broke/dev/sofascore_scraper/sofascore_scraper/logs/sofascore_scraper.log. Números de línea de esta lectura, no una transcripción de secretos:

- 11–17, 21-10: 403 en /api/v1/search/all -> detección de Turnstile -> Scrapling StealthySession headless -> registro de resolución -> GET200 a captcha.html -> mensaje de uso de navegador durante 600 s.
- 101, 21-10: Match16599363 stored, seis ficheros escritos.
- 136–142, 22-10: 403 en /team/24368/events/next/0 -> detección de challenge -> nueva sesión headless -> registro de resolución -> captcha.html200 -> navegador durante 600 s.
- 291–295: exportación raw con skipped=0 y otro cambio al navegador tras 403 al listar el equipo24322. El registro también conserva fallos posteriores: no significa acceso universal o que todos los recursos acabaran completos.

La exportación real-jaen-raw_2026-10-21_gfmykcat.jsonl coincide con el manifiesto importado. Para16599363 conserva event(12:07:26UTC), statistics y team_streaks(12:07:30), lineups/incidents/h2h(12:07:31). El manifiesto de importación fecha la importación el22-10; algunas exportaciones del22 reutilizan originales del21. Fecha de exportación/importación no equivale a nueva consulta de cada recurso. Las horas del log se dejan como impresas, sin atribuirles zona no declarada.

No encontré en ese registro evidencia de una respuesta200 para la URL exacta del calendario de ronda1. Los listados exitosos del flujo por equipos y un evento almacenado no prueban que se consultara esa otra ruta con éxito.

## Matriz de diferencias demostradas

| Aspecto | Descarga histórica / cliente normal disponible | Piloto urllib | Piloto navegador |
|---|---|---|---|
| Ejecución | Proceso Windows con servidor y FetchPipeline, acreditado por logs | Linux Docker, transporte propio | Linux Docker, runner diagnóstico propio |
| Acceso inicial | Logs de listados por equipo/búsqueda; detalles almacenados | Calendario ronda1 | Mismo calendario ronda1 |
| Cliente | Código normal usa curl_cffi y BrowserBridge; logs acreditan cambio al bridge | urllib/OpenSSL, User-Agent del piloto | Patchright directo, navegación de documento |
| Contexto | Bridge abre página del sitio y hace fetch desde ella; cabeceras de cliente, cookies/token cuando disponibles | Sin sesión del sitio ni cabeceras dinámicas del SDK | Perfil nuevo sin sesión previa; sin home, captcha o probes |
| Challenge | Los logs registran detección y resolución antes de continuar | Prohibido, parada403 | Prohibido; rechazo antes de ejecutar challenge/contenido no JSON |
| Solicitudes auxiliares | Warmup, home, captcha, probe y recursos de página posibles; no equivalen a solo5GET | Allowlist5rutas, máximo10, se paró tras1 | Una ruta y un canal TLS; tráfico de fondo denegado localmente |
| Protocolo | No conservado en captura histórica | TLS de urllib | HTTP/1.1; sin HTTP/2 ni QUIC para acotar canales |
| Perfil | Mecanismo del scraper; no conocemos perfil/cookies exactos usados entonces | Sin navegador | Propietario SO y generación exclusivos, sin perfil alternativo |
| Resultado | Originales y exportaciones existentes, junto a algunos fallos en logs | 403 con cuerpo JSON Forbidden; no challenge en ese cuerpo | 403, application/json, Retry-After0; cuerpo abortado, no permite clasificar challenge |
| Certificados/sandbox | No existe acreditación /proc del navegador histórico | TLS estándar urllib | Verificación nativa, sandbox acreditado, capabilities0 y no-new-privileges |

En los DOS entornos actuales, Windows instalado e imagen Docker auditada, se verificaron patchright1.63.0, Scrapling0.4.15 y curl_cffi0.16.3. Esto no prueba las versiones instaladas en la fecha histórica ni identidad de binarios entre Windows/Linux. HEAD611e436 es del23-10, posterior a las descargas21/22. El cliente no cambia en el diff de commits dd9f061..611e436; sigue sin ser una captura del entorno que ejecutó aquellos trabajos. El cambio c697852 del23-10 corrige paginación de próximos partidos por equipo: no reproducir inadvertidamente limitaciones antiguas de inventario.

El primer piloto sustituyó el transporte normal aunque reutilizaba FetchPipeline/Store/exportación. El segundo probó deliberadamente un navegador nuevo bajo límites estrictos, NO el BrowserBridge normal. No se pueden presentar como dos pruebas fallidas del cliente completo del scraper.

## Qué sigue siendo hipótesis

- Que la causa sea ausencia de contexto/cookies/cabeceras, protocolo/TLS, reputación de IP, diferencia de plataforma o cambio de reglas del proveedor. No hubo experimento que aislara una variable ni captura de cabeceras/huella/IP del trabajo antiguo.
- Que ambos pilotos y Windows histórico compartieran IP pública/proxy. Docker Desktop no basta para afirmarlo; no se consultaron servicios externos para medirla ni secretos/configuraciones históricas inexistentes.
- Que copiar cookies o habilitar resolución de challenges garantizara acceso; no está demostrado y no se propone para superar el rechazo.
- Que SofaScore rechace hoy todos los endpoints o que el calendario solicitado sea inválido. Los403 solo acreditan esos intentos; no validan o invalidan toda la API.
- Que repetir el cliente normal ahora vaya a funcionar. Los logs acreditan éxito pasado, no disponibilidad actual, autorización o estabilidad para producción.

## Operaciones existentes que sí se pueden aprovechar

| Necesidad | Operación existente | Encaje real |
|---|---|---|
| Planificación sin red | ssc sync --dry-run; planificación services/planning.py | Permite estimar desde un almacén identificado; no ofrece una cifra exacta de todos los recursos auxiliares del navegador |
| Calendario | services.sync.list_schedule; ListingService.schedule | Calendario de torneo/temporada, con caché y paginación; no una consulta fija a ronda1 |
| Actualizar ámbito | SyncService / FollowsSyncSpec; ssc sync --follow team:ID o fetch tournament ID --season ID | Operaciones reales existentes; fijar20equipos y grupo94191/temporada97382 antes de descargar; los defaults pueden incluir otras temporadas/competiciones |
| Detalles | FetchPipeline/DetailPhase; ssc fetch event ID | Reutilizar selección explícita event, lineups, statistics, incidents; el conjunto por defecto puede pedir extras |
| Consulta de bajo nivel | Client.get y transporte normal | Existe; no equivale a una vía fiable hoy ni cumple el presupuesto de un único GET sin adaptar warmup/bridge |
| Refresco de caché | ssc refresh / SyncSpec(mode=refresh) | Revisa eventos temporales; NO refresca por sí solo todas las estadísticas ya válidas según72h/6h |
| Exportaciones | ExportService/DatasetSpec; ssc export --schema raw --format jsonl; events normalizados | Reutilización directa sin consultar proveedor; conserva payload/fetched_at. Verificar skipped, scope, original e inventario. Exportar no es actualizar |
| Progreso/cancelación | JobManager y API /api/v1/jobs, /{id}, /{id}/cancel, SSE | Ya existen. Un wrapper interno de receta fija puede delegar; no exponer la API genérica o comandos arbitrarios al dashboard |

Las operaciones se comprobaron en código, no se ejecutaron sobre la instalación habitual. El SDK ExportService no toma el bloqueo por el llamador: exportar bajo lease/vista consistente y comprobar omisiones sigue siendo necesario. Las exportaciones por equipo no sustituyen a la cobertura/calendario/procedencia del bundle. La receta mantiene IDs originales, versiones, decisiones, nulos y rechaza descargas parciales.

## Por qué no conectar sin cambios el cliente completo

El BrowserBridge normal configura AsyncStealthySession(solve_cloudflare=True), páginas home/captcha y un probe a /unique-tournament/17/seasons. Su profile_lock permite perfil secundario temporal si el principal está ocupado. No son opciones compatibles con los límites/recuperación aprobados.

Además, la fuente de Scrapling0.4.15 instalada, engines/_browsers/_base.py en StealthySessionMixin, fija ignore_https_errors=True, service_workers=allow y permisos geolocation/notifications; BrowserBridge no los anula en su llamada. Nuestro wrapper exige verificación TLS, service_workers bloqueados y permisos vacíos. No afirmamos que esa diferencia causara el403 ni qué sandbox tuvo el proceso antiguo; es un impedimento concreto para reutilizar el lanzamiento sin revisión/adaptación. No cambiar estas garantías para imitar un éxito pasado.

No existe por tanto una operación única de refresco que sea a la vez completa para nuestro ámbito, compatible con nuestro perfil/sandbox y acreditada con acceso real hoy. Sí existen las piezas deportivas y de exportación necesarias: no hay motivo para duplicarlas ni sustituirlas por más transportes diagnósticos.

## Camino mínimo recomendado

1. Mantener el backend y el protocolo ya probado: bundle verificado -> candidata -> importación/verificación -> publicación. Adquisición separada, sin volumen deportivo/journal. No tocar frontend ni añadir un segundo framework de trabajos.
2. Reutilizar como frontera el productor de exportaciones del scraper. Hoy se puede integrar de manera fiable la recepción de exportaciones existentes/coherentes y su publicación offline, identificando fecha/cobertura; NO anunciar datos recién consultados. Esa parte ya dispone de recorrido offline validado.
3. Para actualización automática fresca, el servicio de adquisición debe delegar calendario, planificación, FetchPipeline y exportaciones al SDK/operaciones existentes, con receta fija del ámbito y las políticas72h/6h. No continuar ampliando urllib, el runner de un GET o el gateway diagnóstico como implementación deportiva. No usar sync global con configuración personal ni montar caché/perfil Windows en Docker.
4. Antes de habilitar red en ese adaptador, necesitamos un acceso estable y admitido al origen. El cliente normal es candidato por evidencia histórica, pero su transporte requiere conservar el propietario/sandbox, verificación TLS, límites, cancelación y parada ante rechazos. Su éxito actual no se ha probado ni se presume. No recomendar integración directa del bridge con sus defaults.

Si no se puede obtener ese acceso de SofaScore, necesitamos un canal autorizado/documentado o exportación acordada del proveedor, o un proveedor alternativo con licencia/permisos y cobertura expresa de esta competición: calendario, eventos, alineaciones, estadísticas e incidencias, IDs y procedencia/versiones. No se afirma aquí que exista una oferta concreta ni se ha contactado a un proveedor. Una fuente federativa/club puede servir para calendario/resultados o aportación manual autorizada, pero solo si se verifica su cobertura; no sustituye automáticamente estadísticas, pierna dominante o incidencias. Mantener desconocidos y pendientes, no fabricar ceros.

La decisión pendiente es acceso/fuente y cobertura, no otro cambio del bloqueo SQLite. Con lo comprobado hoy no se puede prometer un botón que descargue de SofaScore de forma fiable. La opción más pequeña y fiable disponible es actualización por exportaciones verificadas; automatizar la consulta queda bloqueado por el acceso real hasta resolverlo.

## Huellas de la evidencia leída

- Log histórico SHA-256: 5bad72ee9ffdd729284a84bc1eb9082df3b9d4152e2012fccfcade78364705ab
- Exportación original E1 SHA-256: 67d41ccc6249741cb4c30544046c417a3f4e06cbeb8ce6cd217ddca4759a29f0
- Informes de los intentos: piloto-sofascore-real-20261027.json y calendario-https-real-20261027.json.
