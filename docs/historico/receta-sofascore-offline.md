# Receta SofaScore: entrega offline y contrato real v2

> Actualización: el contrato operativo del piloto, inventario dinámico y política de reintentos vigentes están en [piloto-sofascore-tecnico.md](../piloto-sofascore-tecnico.md). Quedan superadas aquí las condiciones de 380 IDs fijos, seis horas para fallos y el bundle sin respuestas originales.


Esta entrega queda deshabilitada en las instalaciones habituales. No incorpora proveedor HTTP, endpoints, botón ni integración de publicación real. El servicio y cliente existentes siguen admitiendo solo su simulador explícito de pruebas. La política temporal de esta entrega sustituye las opciones pendientes del diseño inicial; la política de ausencias de este documento sustituye su propuesta de dos vacíos/404.

## Manifiesto comprobado

[acquisition/sofascore-scope.json](../../acquisition/sofascore-scope.json) fija los 20 equipos, correspondencias públicas reales, exportación propia seleccionada, SHA256, testigo de pertenencia y 380 IDs de eventos conocidos. Solo contiene metadatos; ningún payload, jugador, cookie o secreto. Ámbito: torneo único 17073, grupo 94191, temporada 97382.

Se han contrastado offline las 20 exportaciones con el manifiesto archivado de importación `2026-10-22-grupo2-j38`, sus hashes, los eventos originales de cada exportación, equipos.json de esa importación y SOURCE_TEAM_IDS del frontend. Hay 38 eventos del grupo por exportación, unidos en 380 únicos. Cada evento de grupo tiene al propietario como participante y ambos participantes pertenecen al conjunto de 20. No se han usado nombres para emparejar IDs. Hay dos exportaciones de Real Jaén en la evidencia: se verifica y selecciona la última declarada, conservando el manifiesto histórico como procedencia.

| SofaScore/backend externo | Equipo | ID público de Next |
|---:|---|---|
| 2815 | Real Zaragoza | sofascore-team-2815 |
| 2840 | Gimnàstic de Tarragona | sofascore-team-2840 |
| 2841 | Real Jaén | team-real-jaen |
| 2877 | Hércules CF | sofascore-team-2877 |
| 4487 | Real Murcia | sofascore-team-4487 |
| 4489 | Algeciras CF | sofascore-team-4489 |
| 5069 | Real Madrid Castilla U21 | sofascore-team-5069 |
| 24265 | Huesca | sofascore-team-24265 |
| 24322 | AD Alcorcón | sofascore-team-24322 |
| 24323 | Antequera | sofascore-team-24323 |
| 24326 | Atlético Madrileño | sofascore-team-24326 |
| 24329 | FC Cartagena | sofascore-team-24329 |
| 24338 | Villarreal B U23 | sofascore-team-24338 |
| 24363 | UE Sant Andreu | sofascore-team-24363 |
| 24368 | UD Ibiza | sofascore-team-24368 |
| 43751 | CD Teruel | sofascore-team-43751 |
| 135672 | CE Europa | sofascore-team-135672 |
| 202074 | Rayo Majadahonda | sofascore-team-202074 |
| 217210 | CDA Águilas FC | sofascore-team-217210 |
| 263407 | Juventud Torremolinos CF | sofascore-team-263407 |

La correspondencia SQL interna no es el ID externo: sus uid y decisiones pertenecen a la persistencia. No se han consultado ni recalculado las asociaciones actuales de la base activa. La instalación de prueba tendrá que verificarlas sobre su snapshot reciente mediante el mecanismo existente. El bundle solo declara IDs del proveedor y el importador conserva/resuelve identidades y decisiones; el manifiesto no autoriza reasignaciones.

Auditor reproducible, sin abrir Store/SQLite ni ejecutar CLI del scraper (los argumentos son evidencia de lectura, la salida debe ser nueva):

```sh
python scripts/plan_sofascore_evidence.py --import-manifest /evidence/exportaciones.json --exports-dir /evidence/raw --frontend-source ../rj-analyst/src/server/local-api/local-api-source.ts --teams-json /evidence/equipos.json --output /tmp/sofascore-scope-audit.json
```

Las rutas anteriores son ejemplos configurables, no dependencias de la imagen. El catálogo archivado no acredita las últimas importaciones de la base Windows.

## Contrato real v2, separado del simulador v1

Verificador offline: [acquisition/real_bundle.py](../../acquisition/real_bundle.py). No se ha conectado al servidor ni al cliente del backend. La futura conexión deberá conservar autenticación, request_id idempotente, descriptor SHA256/bytes y comparación con el trabajo solicitado, copia privada inmutable y verificación antes del coordinador. No basta con aceptar schema=2 en el cliente actual.

Envelope JSON exacto: `manifest` y `files` (mapa de nombre fijo a contenido base64). Manifiesto:

- schema=2, recipe_id=sofascore-ambito-v1, provider=sofascore, is_simulated=false, provider_revision fijada a 611e436a1528b0f487aa56a045f023e8ae8ae098.
- job_id y request_id UUID canónicos; el receptor debe proporcionar expected_context desde su solicitud durable. Descriptor y ETag se verifican fuera del contenido antes de analizarlo.
- scope_sha256 del manifiesto versionado, rules_sha256 de las fuentes del adaptador/verificador/manifiesto y revisión/configuración; policy contiene window_s e interval_s. Un cambio de código o política no se disfraza de selección de versiones. El coordinador conserva además su comprobación independiente de reglas de importación/publicación.
- started_at y finished_at con zona; no se fabrican fechas de consulta al reutilizar caché. errors debe estar vacío para publicar.
- files: exactamente 20 `team_ID.jsonl` de tipo own_export con team_external_id explícito y `calendar.jsonl` de tipo calendar, sin propietario. Descriptores: name, kind, team_external_id, bytes, sha256. Sin TAR ni rutas libres, sin perfiles, fichas o configuración.
- coverage: una entrada por evento y recurso calendar/event/lineups/statistics/incidents, sin duplicados ni omisiones. Campos exactos: event_id, resource, status, checked_at, evidence y rule. Una evidencia de datos apunta a file y line concretos, comprobados contra el recurso y la fecha original. El SHA del archivo fija los bytes de esa línea.

Las exportaciones raw conservan event_id/key/sub/fetched_at/payload y solo las cuatro secciones de la receta. Se filtra el ámbito antes de exportar. Los dos propietarios de un partido reciben la misma versión y fecha de sus recursos, aunque la descarga haya sido única. Un desacuerdo se rechaza; no se selecciona silenciosamente una versión.

El calendario usa el formato normalizado events que ya admite importar.py: torneo único en tournament_id y grupo en stage.id; participantes, estado y quality.observed_at_utc verificables. No crear otro normalizador. El verificador comprueba identidad/ámbito/procedencia mínimos; la validación deportiva y equivalencia siguen siendo responsabilidad del importador y la candidata. Exige los 380 IDs conocidos y, al conectarse, el backend deberá pasar required_event_ids con su inventario publicado independiente, incluidos nuevos IDs posteriores. Así una lista más corta no se convierte en un calendario completo por contener los 20 equipos.

| Estado de cobertura | Admisión |
|---|---|
| data | Payload válido según las reglas existentes del scraper y referencia exacta a su original. Conservar antigüedad si se reutiliza. |
| confirmed_absence | Exige regla específica del endpoint validada y evidencias. Registro de reglas vacío en esta entrega: ninguna ausencia de estas secciones queda autorizada. |
| not_applicable | Regla de receta pre-or-live-v1, acreditada con la fase original: la receta difiere estas secciones hasta el final; no afirma que SofaScore no pueda tenerlas. event sin raw puede usar calendar-only-pre-v1 solo en fase previa, según el soporte existente de calendario. |
| pending | Vacío, 404, evidencia insuficiente, regla ausente o comprobación pendiente: impide publicar. |
| failed | Timeout, rechazo, parseo incorrecto, cancelación, error de almacenamiento u omisión: impide publicar. |

Ni dos vacíos ni un 404 son una regla de ausencia. empty_count y unverified_empty_count del scraper no se usan como prueba de ausencia. Un resultado vacío se conserva como intento pendiente; nunca se fabrica un payload ni un cero. Si una ausencia nueva contradice datos previos, se rechaza explícitamente porque el importador aún no tiene semántica para resolverla. El verificador recibe prior_data del inventario independiente del backend; además rechaza hoy toda ausencia, incluso sin contradicción. Fuente y originales previos quedan intactos.

## Medidas reales y límites propuestos

Medición de archivos existentes, SHA verificados; filtrado y serialización UTF-8 compacta en memoria, sin crear exportaciones en la instalación habitual:

| Magnitud | Bytes |
|---|---:|
| 20 exportaciones propias originales seleccionadas | 23.185.044 |
| Mayor exportación original | 1.284.748 |
| 20 exportaciones filtradas a ámbito y cuatro recursos | 22.919.569 |
| Mayor exportación filtrada | 1.275.294 |
| Calendario complementario disponible, solo dos filas | 1.617 |

Comprobación adicional con body_state/classify_status del SDK en un contenedor sin red, leyendo únicamente exportaciones archivadas montadas RO: 80 eventos finalizados, con datos en las tres secciones para los 80; 300 eventos previos. Las dos filas del calendario complementario carecen de observed_at_utc: se conserva esa limitación, no se inventa una fecha. Antes de sellar un bundle habrá que exportar el calendario completo desde una caché aislada inicializada y preservar las fechas disponibles; lo desconocido seguirá pendiente.

Límites de verificador propuestos: 21 archivos exactos, 4 MiB por archivo, 64 MiB de suma decodificada, 2 MiB por línea, 4 MiB de cobertura y 96 MiB de envelope HTTP. Margen actual: aproximadamente 3,29 veces el mayor raw filtrado y 2,93 veces su suma. Base64 necesita alrededor de un tercio adicional; 96 MiB deja margen al envelope de 64 MiB de archivos más cobertura. No es una estimación medida del calendario completo, memoria máxima o temporada totalmente finalizada. Si se supera cualquier límite, rechazar antes de importar y revisar medidas; nunca truncar ni subir límites automáticamente. El cliente v1 conserva sus límites antiguos de 2 MiB y no admite este bundle.

## Planificador offline y política aprobada

[acquisition/sofascore_offline.py](../../acquisition/sofascore_offline.py) solo admite FixtureTransport exacto y contenedor con interfaz lo (network:none). No construye un Client HTTP, no acepta URLs y no tiene selector para activar transporte real. No está registrado en el servicio. Las respuestas simuladas y Stores de prueba viven exclusivamente en directorios temporales; no se importa ningún fixture en datos reales.

Reutiliza ListingFetcher, FetchPipeline, WorkItem/Ref, expected_slice_keys, phase_of y classify_status del scraper empaquetado, cuya revisión y fuentes relevantes contrasta por hash el runner. No duplica normalización deportiva. collect mantiene el lease writer de la caché; run es una primitiva de detalles cuyo llamador debe tener exclusividad. Uniones por event_id, conflicto de evidencia duplicada rechazado, una consulta de evento memoizada solo dentro del trabajo para la replanificación posterior al final.

La configuración inicial es Policy(window_s=259200, interval_s=21600). Reloj explícito en planificación:

- Primera consulta posterior al final, aunque se descubra tarde; las secciones de antes del final no satisfacen esa obligación.
- Intervalo mínimo de seis horas por sección, incluido un intento fallido. Una sección cuyo intento reciente falló sigue pendiente durante la espera, aunque conserve datos viejos.
- Ventana desde el inicio, 72 horas. Reconsulta secciones guardadas aunque el marcador no cambie. A las seis horas exactas se habilita; un segundo antes, no.
- Comprobación de cierre en la primera ejecución debida: la última consulta válida debe alcanzar el cierre. Si el último intento fue reciente, se respeta el mínimo aunque cierre más tarde de las 72 horas.
- Final descubierto tarde: cierre no anterior a seis horas después de observarlo por primera vez. No confundir instante de observación con hora real del final.
- Una vez comprobado el cierre, no hay revisión histórica rotatoria. Sin fichas personales ni extras. Los encuentros pendientes conocidos con inicio futuro usan calendario/caché; los que ya deberían haber empezado se vuelven a consultar en una ejecución solicitada para detectar el final.

Checkpoint durable: primera observación final por evento y prueba independiente de calendario. Antes de listar se marca el calendario como no confirmado; caída o fallo no permite convertir una caché parcialmente escrita en éxito fresco al reiniciar. No hay ejecución automática al construir/reabrir el adaptador. El catálogo se filtra también por stage_id, además de torneo/temporada. Las nuevas respuestas /event se comprueban antes de escribir en Store.

El baseline inicial de finalizaciones debe proceder de evidencia válida en una copia de la caché, explícitamente preparado. La ausencia de checkpoint se considera primera observación, no prueba de estabilización: no activar el adaptador sobre toda una caché antigua sin esa inicialización. La caché temporal de fixtures es explícita; la instalación habitual no cambia.

Calendario: conserva TTL global de 15 minutos del SDK y sus reglas propias de reutilización por ronda (hasta seis horas para rondas incompletas; completas pueden reutilizarse sin revisión histórica). Esa política limita detección de nuevas correcciones de calendario; revisar eventos pendientes conocidos ayuda a detectar finales, pero no garantiza descubrimiento instantáneo de cualquier cambio o nueva asignación. La ventana 72h tampoco garantiza ausencia de correcciones posteriores.

Navegador: OfflineRecipe.browser delega sin cambios al managed_browser ya validado. Sandbox explícito, dueño del perfil, sin perfil vacío alternativo y sin fallback de red. Se probó contenido data: local y chrome://sandbox más /proc a través del wrapper. No se ha adaptado BrowserBridge para tráfico real ni relajado require_offline. Perfil y recuperación administrativa siguen las reglas anteriores.

## Preflight concreto del piloto, todavía NO autorizado ni ejecutado

Plan legible por m?quina: [preflight-sofascore-piloto.json](preflight-sofascore-piloto.json). Identidad verificada en originales de ambos propietarios:

- Event ID 16599363, CE Europa (135672)–Real Jaén (2841), ronda 1, inicio 2026-08-30 17:15 UTC, estado finished en la evidencia archivada.
- Testigos: ce-europa-raw_2026-10-22_135672.jsonl y real-jaen-raw_2026-10-22_v627pscv.jsonl; hashes en el manifiesto fijado. Las reglas existentes reconocen datos en lineups, statistics e incidents; sus fechas conservadas son del 21 de octubre, no de hoy.
- Es antiguo respecto a la ventana. Probarlo implica autorizar una reconsulta dirigida excepcional de UN partido, en instalación de prueba; no habilita revisión histórica en la receta habitual.

Allowlist propuesta para ese piloto: una página fija de calendario y cuatro recursos. No recorrer /rounds ni otros equipos/páginas si esa respuesta exige más contexto: detener y revisar el plan.

```text
GET /unique-tournament/17073/season/97382/events/round/1
GET /event/16599363
GET /event/16599363/lineups
GET /event/16599363/statistics
GET /event/16599363/incidents
```

Cinco operaciones API lógicas como máximo, antes de warmup/reintentos; al reutilizar la página podrían ser cuatro. Los endpoints de secciones se obtienen de endpoints.event_slice del SDK. El SDK de calendario general no se ejecutará sin acotar: puede recorrer muchas rondas. El piloto deberá utilizar sus primitivas de listado para esa página fija y conservar las demás desde el baseline, registrando qué fue consultado y qué se reutilizó.

Propuesta de límites para autorizar después: concurrencia 1, tasa máxima 1/s, timeout 10 s, hasta dos intentos totales por operación y 16 intentos API contando warmup, máximo cinco minutos. El contador actual solo valida operaciones simuladas; NO garantiza todavía intentos HTTP reales ni recursos del navegador. Antes de autorizar habrá que instrumentar el transporte real y acreditar presupuesto, cancelación y parada ante 403/429/breaker sin cambio automático de transporte. No hay tráfico real habilitado en esta entrega.

Nombres reservables propuestos, comprobados inexistentes mediante inspect, pero NO creados ni reservados:

- Proyecto: rj-sofascore-pilot-20261026.
- Volúmenes: rj-sofascore-pilot-20261026-cache, rj-sofascore-pilot-20261026-profile, rj-sofascore-pilot-20261026-sport y rj-sofascore-pilot-20261026-backups.
- Sin publicación de adquisición al host, sin puerto necesario para el piloto interno. Backend de prueba con sus restricciones actuales; adquisición sin acceso a sport/backups/journal. Perfil y caché exclusivos, secreto nuevo mediante archivo RO, nunca dentro del bundle.

Repetir comprobación de identidad/ausencia antes de crearlos, con etiquetas de instalación y UID/GID adecuados. No sobrescribir un volumen si aparece entre tanto. Datos de prueba solo reales: snapshot actual de Windows consistente y verificado por el mecanismo existente, más respaldo soportado de caché bajo su bloqueo; nunca copiar la SQLite activa directamente ni reutilizar la pila simulada. No se ha obtenido ese snapshot ni detenido servicios hoy. Si el bloqueo impide obtenerlo, queda pendiente de una ventana autorizada; las exportaciones antiguas no prueban equivalencia con la base actual.

El bundle de piloto debe mantener 20 exportaciones propias y calendario completo del baseline, aunque solo se consulte un partido. El backend lo copia/verifica y valida candidata antes de publicar exclusivamente en su SQLite de prueba. No se habilita ese handoff real todavía. Espacio: medir snapshot/caché y reservar sus copias, candidata, bundle y margen; los tamaños actuales del raw no permiten inventar el espacio total de esa instalación.

## Validación y límites de esta entrega

Runner portable y focalizado (imagen local previamente preparada, sin pulls ni instalación de dependencias):

```sh
python scripts/test_sofascore_offline.py --image rj-profile-admin:runtime-20261026 --scraper-source ../sofascore_scraper/sofascore_scraper --result /tmp/sofascore-offline-results.json
```

Requiere Docker/Compose instalado según la entrega anterior, motor Linux amd64 disponible, imagen local de runtime auditada con SDK y Patchright/navegador, permisos para Docker y acceso de lectura al checkout fijado. El runner compara hashes del SDK empaquetado, usa network:none, UID 10001, capabilities cero, no-new-privileges, init y el seccomp de cinco excepciones sin modificar. Monta RO solo acquisition y el archivo de pruebas Docker; Stores, perfil y configuración generada por imports del SDK quedan en el contenedor efímero, sin volúmenes habituales. No debe usarse este runner para crear una instalación real.

Resultados propios se registran en [sofascore-offline-validacion.json](../sofascore-offline-validacion.json). Se han probado bordes con reloj fijo, deduplicación, reconsulta de solo la sección debida, final descubierto durante ejecución, caché, fallo con datos anteriores preservados sin falso éxito posterior, vacío/404/parseo inválido, cancelación y cierre del transporte, presupuesto, reinicio sin trabajo automático, calendario incompleto/interrumpido, hashes/tamaños/referencias, contexto de trabajo, huella de reglas y navegador local con sandbox. Resultado final registrado: 30 pruebas focalizadas satisfactorias. Sin suite completa de API o frontend, sin peticiones externas. Las pruebas dependientes del SDK/navegador est?n en docker/acquisition, fuera del descubrimiento habitual de tests de API; el modo JSON no incorpora estas dependencias.

Límites expresos: no ensamblador productivo de bundle desde la caché, transporte HTTP real ni adaptación online del navegador; no consumo/publicación real v2 por el backend. El verificador necesita las reglas deportivas del SDK en esta validación offline; al integrar el receptor habrá que reutilizar su validación existente sin imponer al backend el navegador o la imagen de adquisición. El contrato y sus guardas no sustituyen importación transaccional, verificación y recuperación ya existentes. No se ha probado un piloto real, generación de un calendario completo actualizado, compatibilidad del servidor Linux del grupo ni dimensionamiento final de memoria/disco.

No se ha modificado la base activa, servicios, scraper, frontend o .env.local. Sin commit ni push. Detenerse para revisión antes de cualquier transporte real o integración posterior.
