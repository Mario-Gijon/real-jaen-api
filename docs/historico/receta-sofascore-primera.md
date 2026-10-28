# Primera receta real SofaScore: diseño para revisión

> Actualización: el contrato operativo del piloto, inventario dinámico y política de reintentos vigentes están en [piloto-sofascore-tecnico.md](../piloto-sofascore-tecnico.md). Quedan superadas aquí las condiciones de 380 IDs fijos, seis horas para fallos y el bundle sin respuestas originales.


> Diseño exploratorio anterior. La especificación vigente de la entrega offline está en [receta-sofascore-offline.md](receta-sofascore-offline.md). El manifiesto ya se ha fijado y la política 72h/6h está aprobada. Queda superada cualquier propuesta de confirmar ausencias por número de vacíos o por un 404: sin regla validada del endpoint siguen pendientes.

Estado: propuesta, sin implementación ni autorización para tráfico real. Preparada sobre api-real-jaen ab31453 y scraper 611e436a1528b0f487aa56a045f023e8ae8ae098 (3.1.0). No sustituye los invariantes de publicación, recuperación del perfil o aislamiento ya aprobados. No habilita el proveedor simulado en instalaciones habituales.

## Ámbito fijo y precondiciones

Receta propuesta `sofascore-ambito-v1`, sin URLs, comandos, rutas ni selecciones arbitrarias aportadas por el cliente. Comprende los 20 equipos de la competición/temporada declarada en ambito.json: unique_tournament_id=17073, grupo/tournament_id=94191, season_id=97382. En el catálogo del scraper el torneo único y la fase/grupo son campos diferentes: no intercambiar 17073 y 94191.

El archivo actual no enumera los 20 equipos. Antes de implementar hay que fijar y revisar un manifiesto de sus IDs externos, identidad y pertenencia al grupo, obtenido de evidencia existente o de una inicialización expresamente autorizada. No deducir el ámbito de todos los equipos almacenados: pueden incluir rivales externos. Una selección ambigua, grupo desconocido o equipo sin correspondencia impide declarar cobertura completa.

Actualizar significa haber consultado el calendario exigible, resuelto el plan de partidos/secciones debido y publicado una generación verificada. No significa haber vuelto a descargar toda la temporada ni todas las fichas. Registrar por recurso consulta efectiva, antigüedad de la evidencia reutilizada, cobertura y pendientes; no cambiar fetched_at al reutilizar caché.

Fuera de alcance: fichas personales masivas, plantillas/perfiles individuales, revisión histórica rotatoria, odds, h2h, gráficos, estadísticas de otros ámbitos y nuevas pantallas. Las fichas anteriores permanecen disponibles con su procedencia y antigüedad; no se borran por no incluirse en esta receta.

## Operaciones existentes que se reutilizan

Revisión de código, sin ejecutar la CLI contra la instalación habitual:

| Operación | Implementación existente | Uso y precaución |
|---|---|---|
| Descubrir temporada y calendario | services/sync.py, SyncService/SyncSpec/SyncSelection; services/listing.py, ListingService/ListingFetcher | Selecciones explícitas de competición/temporada; no sincronizar todos los follows configurados. |
| Planificar caché | services/planning.py, compute_need/work_item; services/query.py, RefreshPolicy | Política y reloj explícitos. El plan necesita una ampliación local para refrescar secciones ya guardadas. |
| Descargar detalles | services/pipeline.py, FetchPipeline y DetailPhase | Selección explícita lineups, statistics e incidents, más /event; no usar el conjunto core, que incluye recursos adicionales. |
| Límites y fallos | Client, request_context, throttle, breaker y JobHandle | Conservar control de tasa, reintentos, cancelación, progreso y errores; no ignorar el breaker ni eludir un rechazo. |
| Alternativa por equipo | services/follow_sync.py | next/last paginados y unión de event_id; no es la estrategia principal porque multiplica listados y puede recorrer otros ámbitos antes de filtrar. |
| Exportar originales | services/export.py, ExportService/DatasetSpec/DatasetFilter; store/export.py | JSONL raw conserva event_id, key, sub, fetched_at y payload. Revisar ExportReport.skipped: una exportación truncada no es completa. |
| Respaldo de caché | Store.backup.create y exclusividad del JobStore | El llamador debe adquirir el bloqueo: create por sí solo no lo garantiza. No copiar directamente archivos abiertos. |

El scraper instalado mantiene un cambio ajeno en frontend/package-lock.json; queda fuera del empaquetado y de esta propuesta. Se conserva su revisión fijada.

## Recorrido mínimo

1. Adquisición acepta un único trabajo durable e idempotente mediante el contrato interno existente. La receta real será otro proveedor explícito; no reutiliza los marcadores de instalación simulada ni permite pasar fixtures como reales.
2. Adquiere exclusividad de su caché y, si utiliza navegador, propiedad exclusiva del perfil durante toda la sesión. Realiza un respaldo coherente de caché/estado mediante el mecanismo existente, sin incluir secretos ni el perfil en el bundle. Una caché vacía o incompatible exige inicialización explícita: no disparar silenciosamente una descarga histórica.
3. Refresca el calendario compartido de la temporada/grupo con el pipeline existente. Reutiliza su TTL de calendario de 15 minutos y de temporadas de 6 horas; documenta si el trabajo reutilizó un listado reciente. La topología puede consultar rondas y páginas, no una petición fija. Las rondas ya consideradas completas no equivalen a una revisión exhaustiva de todas sus correcciones históricas.
4. Filtra grupo, temporada y equipos antes de programar detalles. Une por event_id los partidos de los 20 equipos y el catálogo previo del mismo ámbito. Los encuentros compartidos tienen una sola unidad de descarga por trabajo; dos exportaciones de equipo no generan dos descargas.
5. Planifica /event y secciones con las reglas siguientes. Tras obtener un estado nuevo del evento, replantea sus secciones dentro del mismo trabajo. No dar por terminado un partido recién finalizado después de refrescar solamente su marcador.
6. Ejecuta el plan con cancelación cooperativa y registro de cada resultado. Conserva descargas parciales en la caché para reanudarlas en otro trabajo solicitado; el reinicio no reanuda ni publica automáticamente. Un nuevo trabajo recalcula qué falta y respeta los intervalos, sin repetir lo ya obtenido válidamente.
7. Exporta desde una vista estable de la caché. Prepara manifiesto y hashes, verifica los archivos y sella el bundle inmutable. Si hay fallos, secciones obligatorias pendientes, omisiones de exportación o cobertura deportiva incompleta, conserva el resultado parcial pero no entrega un bundle publicable.
8. El backend copia y verifica el bundle en su almacenamiento antes de entregarlo al coordinador. Solo él crea candidata, importa con las funciones existentes, verifica, prepara instantánea y publica mediante el protocolo durable ya aprobado. Adquisición nunca monta SQLite deportivo ni su journal. No se cambia el bloqueo ni la seguridad del backend.

## Nuevos finales y correcciones recientes

No es una receta de seguimiento en directo. Cada ejecución manual debe detectar cambios respecto al calendario/catálogo previo: nuevos IDs, fechas, participantes y estados. Los partidos conocidos pendientes/en juego se revisan también aunque una página de calendario reutilizada no permita conocer su transición final. No hacer polling continuo entre trabajos.

- Partido nuevo no finalizado: obtener el evento si hace falta para calendario/estado; las tres secciones no exigibles todavía se registran como no aplicables por fase, nunca como ausencia confirmada.
- Paso a finalizado, incluso si se descubre tarde: obtener evento y las tres secciones. Un payload de alineaciones obtenido antes del final no exime de esta primera consulta posterior. Registrar cuándo se observó la finalización; no inventar la hora real de final si no existe evidencia.
- Finalizados recientes: propuesta de ventana de 72 horas desde el inicio, con intervalo mínimo de 6 horas por sección. Reconsultar lineups, statistics e incidents ya guardados cuando vencen, aunque marcador y estado no cambien. El refresh existente solo consulta /event y no resuelve este requisito.
- Cierre de ventana: hacer la última observación debida a partir de su término si la anterior era anterior; no descartar un partido solo porque ahora hayan pasado 72 horas. Para un final descubierto fuera de ventana, obtener su primera versión final y proponer una comprobación adicional al menos 6 horas después antes de estabilizarlo.
- Secciones fallidas o vacías sin confirmar siguen pendientes y usan las reglas de reintento/confirmación; el timestamp del evento no acredita frescura de estadísticas. La planificación necesita fechas de comprobación por sección.
- Finalizados antiguos completos y estabilizados: reutilizar evidencia, sin reconsulta general. Un cambio explícito descubierto de estado/participantes/fecha invalida la planificación afectada y exige revisión dirigida; no implica recorrer el resto del histórico.

La política 72h/6h procede de los valores por defecto revisados del scraper, no de una garantía de SofaScore sobre cuándo acaba de corregir datos. Puede haber correcciones posteriores que esta primera receta no detecte. Aprobar esta limitación antes de activarla.

## Resultados y evidencia: no confundir vacíos

| Resultado | Tratamiento |
|---|---|
| Respuesta válida con datos | Conservar original y fetched_at; disponible. |
| Sección todavía no exigible por fase | not_applicable con regla y estado del evento; reevaluar al finalizar. |
| Respuesta vacía admisible o ausencia documentada del endpoint | Primera observación: pendiente. Propuesta: dos observaciones definitivas separadas al menos 60 segundos, siguiendo la confirmación existente. No esperar activamente ni repetir de inmediato para completar el trabajo. |
| Ausencia confirmada | Conservar ambas evidencias, contexto y regla específica del recurso; no implica cero en métricas. Un 404 de sección solo sirve si su semántica está validada. |
| Timeout, 403/429, 5xx, JSON malformado o forma inesperada | Fallo, con reintento limitado cuando corresponda; nunca ausencia ni cero. /event no encontrado tampoco demuestra eliminación deportiva. |
| Fin de paginación | Interpretar únicamente según el listado concreto; no extrapolar a ausencia de toda la competición. |

El planificador existente puede contar unverified_empty_count heredado en su umbral. Ese contador por sí solo no acredita ausencia confirmada para el nuevo manifiesto. La receta debe exigir evidencia verificable o dejar pendiente una comprobación dirigida.

Si falla una reconsulta, conservar el último payload válido como evidencia anterior, pero marcar fallida la comprobación actual: no publicar como actualización completa usando ese payload de respaldo. Los originales anteriores no se convierten en ceros ni se pierden. Confirmar ausencia tampoco autoriza a borrar una versión previa sin una regla explícita del importador.

## Adaptaciones necesarias, todavía no implementadas

1. Adaptador de receta fija con manifiesto revisado de 20 equipos, filtrado de fase/grupo y unión global de eventos. Reutiliza SyncService/FetchPipeline; no duplica la normalización deportiva ni permite argumentos libres.
2. Planificación reciente por sección y segunda planificación tras /event. Full/refill/refresh existentes no bastan por sí solos. Añadir un ledger durable de intentos, cobertura, confirmaciones y estabilización asociado al trabajo y caché; distinguirlo del estado deportivo del backend.
3. Exportación de los 20 equipos con semántica de exportación propia, más calendario y evidencia de disponibilidad. No sustituirlos silenciosamente por un único archivo sin equipo, pues alteraría la interpretación de cobertura del importador. Los filtros de ExportService permiten acotar eventos; el calendario requerirá un adaptador del listado almacenado al formato ya admitido por el backend, sin segunda lógica de normalización.
4. Evolución explícita del manifiesto/validador real: el contrato actual de prueba admite raw.jsonl de un solo equipo, bundle de 2 MiB y archivo de 512 KiB. No basta con cambiar el recipe_id o levantar límites. Medir exportaciones offline, fijar formatos, cardinalidad, tamaños y reglas de cobertura antes de implementar la variante real.
5. El raw exporta payloads, no un registro completo de intentos y ausencias sin payload. Añadir evidencia tipada al manifiesto y definir su consumo por la única implementación del importador. No fabricar un payload vacío para borrar datos ni afirmar que una ausencia nueva ya modifica correctamente la selección efectiva. Si esa transición no está soportada, impedir su publicación y explicar el pendiente.
6. Conservación: las secciones principales no tienen keep_history activado por defecto en el scraper. Reutilizar respaldo coherente previo y conservar exportaciones originales por trabajo, incluidas las parciales, antes de nuevas escrituras; no afirmar que la caché conserva todas las versiones automáticamente. El backend conserva sus originales/versiones/IDs/decisiones según las reglas existentes.
7. Integración del cliente/navegador: el BrowserBridge instalado no hereda automáticamente el wrapper endurecido de acquisition/browser.py, actualmente offline. Necesita una adaptación explícita que conserve sandbox, propiedad del perfil y ausencia de fallback vacío. No lanzar sin más el navegador por defecto del scraper. Recuperación administrativa sigue sin red y manual.
8. Contadores y cancelación: presupuesto previo a cada intento, tiempo máximo, progreso por eventos/secciones y drenado de escrituras antes de sellar el resultado. Interrupción o fallo de almacenamiento impiden completed/publicación; el reinicio solo reconcilia el estado durable.

## Límites propuestos y estimación sin red

Propuesta inicial conservadora para caché ya inicializada: concurrencia 1, como máximo 1 petición API por segundo, timeout de 10 segundos, hasta 2 intentos totales por operación reintentable, 15 minutos por trabajo y techo de 500 intentos API. Son límites de seguridad propuestos, no mediciones ni estimación del coste real. El timeout global cancela y deja parcial, sin publicar. El plan debe caber en el presupuesto con sus reintentos antes de empezar; si no cabe, solicitar una inicialización o partición explícita en vez de truncarlo como completo.

Los valores instalados son distintos (5 peticiones/s, concurrencia 10 y max_retries=3); la receta debe aplicar su configuración aislada, sin modificar la configuración habitual. Conservar throttle compartido, Retry-After y breaker. Si no puede acreditarse el presupuesto compartido, fallar antes de descargar. No desactivar esos mecanismos, rotar identidades ni ampliar permisos tras un rechazo. Un 403/429 en la prueba piloto detendrá la prueba para revisión.

Conteo lógico derivado del código, sin acceder a cachés ni ejecutar descargas:

| Unidad única planificada | Peticiones API lógicas de detalles |
|---|---:|
| Evento y tres secciones de fútbol | 4 |
| Refill de k secciones | 1 + k, porque _fetch obtiene /event antes de las secciones |
| Refresh existente de evento | 1, no incluye estadísticas |
| Reutilización sin trabajo debido | 0 |

Para el conjunto: L + E + suma de secciones solicitadas por evento único, donde L son las operaciones de listado planificadas y E las consultas de evento. No sumar otra vez partidos compartidos entre equipos. Los reintentos son intentos adicionales; sesión/warmup y recursos del navegador se contabilizan aparte y no están representados por esa fórmula. Una estimación de peticiones API no equivale a todas las solicitudes HTTP del navegador.

La estrategia alternativa por follows tiene un mínimo de 40 consultas de listado para 20 equipos (next y last), antes de páginas, detalles y reintentos; no es el coste de la estrategia elegida. La estrategia de calendario compartido depende de rondas/páginas y caché y no permite fijar L sin examinarlas. Tampoco debe adoptarse como exacto el contador genérico de refill de la CLI: el pipeline consulta además /event.

No se ha calculado un total real: no se abrió la base activa ni la caché habitual, ni se ejecutó una CLI que pudiera crear/migrar su estado. El preflight futuro deberá usar una copia coherente y aislada de caché, reloj fijo, manifiesto de 20 IDs y selección explícita; producir lista deduplicada de URLs lógicas permitidas, motivos, L/E/secciones, antigüedad reutilizada, límites inferior/superior y tamaños de exportación. Si no puede acotar una página o warmup, declararlo y detenerse al presupuesto, sin inventar cifras.

## Docker y almacenamiento

Adquisición dispone solo de caché, estado de trabajos/bundles y perfil propios. Sin SQLite deportivo, journal del backend, socket Docker o secretos ajenos. Conserva UID sin privilegios, capabilities cero, no-new-privileges y las cinco excepciones seccomp ya aprobadas exclusivamente en adquisición. No se necesitan permisos nuevos para esta propuesta.

La pila interna actual sin salida no permite la receta real. Se necesitará salida controlada del servicio de adquisición y verificar DNS/TLS/destinos realmente usados, sin dar acceso al backend a más redes ni publicar adquisición al host. No prometer una lista de dominios de navegador completa sin observarla en una prueba autorizada. No modificar imágenes para incrustar rutas del anfitrión. Servidor Linux del grupo: verificar namespaces/seccomp/arquitectura compatible, permisos, bloqueo del almacenamiento, espacio y salida antes de desplegar; Docker Desktop no demuestra su compatibilidad.

El dimensionamiento debe incluir caché, respaldo coherente anterior, parciales, bundles y, en el backend, respaldo y candidata de publicación. Medir tamaños y comprobar espacio antes de escribir; no fijar una cifra de disco inventada. Disco lleno impide sellado/publicación y conserva fuente/respaldo. Retención de evidencias y expiración de bundles requieren política aprobada antes de operación habitual.

## Prueba real pequeña propuesta: requiere autorización posterior

No ejecutar aún. Primero implementar y validar offline las adaptaciones, incluidos fallos, cobertura, deduplicación y semántica de ausencias. No repetir suites por este documento.

Preparar proyecto, volúmenes, perfil, secreto y puerto exclusivos de prueba, con datos únicamente reales. Inicializar SQLite desde snapshot consistente y verificado obtenido mediante el bloqueo y copia existentes; inicializar caché desde respaldo soportado y coherente del scraper, sin compartir el directorio habitual. Nunca reutilizar la instalación simulada ni mezclar fixtures con originales reales. Identificar y aprobar previamente un partido ya finalizado del ámbito compartido por dos de los 20 equipos, sin inventar su ID.

Preflight sin red: seleccionar una ronda/página concreta disponible en la copia, con máximo dos operaciones de listado, un evento único y sus tres secciones. Nominal máximo de ese plan: seis operaciones API lógicas, sin contar warmup ni reintentos. Si el listado exige más, detenerse y presentar otro plan. No se prueba todavía la cobertura completa de los 20 equipos.

Propuesta de ejecución autorizable: concurrencia 1, tasa máxima 1/s, timeout 10s, máximo dos intentos por operación y techo de 16 intentos API contando warmup. El contador debe poder interceptarlos antes de iniciar el transporte; si no, no afirmar que el techo está garantizado. Primer piloto sin fallback automático tras 403/429 y sin consultas de fichas. Si se necesita navegador, autorizar expresamente su uso y presupuesto de recursos antes de lanzarlo; no tratar el cambio de transporte como permiso para eludir un rechazo.

Comprobar: una descarga por partido compartido, raw y hashes preservados, dos exportaciones de equipo sin doble tráfico, fetched_at reales, ausencias/fallos diferenciados, bundle completo o rechazo explícito, copia/verificación backend, candidata/publicación, IDs y decisiones conservados. Una actualización sin cambios también es resultado válido. Una prueba con un partido no acredita límites ni disponibilidad de toda la temporada; ampliación posterior a 20 equipos necesita su propio preflight y autorización.

## Decisiones pendientes antes de implementar

- Aprobar el manifiesto exacto de 20 equipos y la inicialización coherente de caché real.
- Aprobar ventana 72h/6h, comprobación tardía adicional, criterios de ausencia por endpoint y exclusión consciente de correcciones históricas fuera de esa política.
- Cerrar el contrato real de calendario/evidencia negativa, su aplicación por el importador y límites de bundle medidos.
- Aprobar presupuesto/retención y política de salida/fallback del navegador; acreditar que los contadores incluyen lo que declaran.
- Autorizar por separado la prueba real con ID, plan y almacenamiento concretos. Este documento no la autoriza.

Validación de esta entrega: inspección de fuentes y contratos locales y planificación aritmética del recorrido. Sin peticiones externas, descargas, ejecución de suites ni cambios de código, scraper, datos, configuración local o servicios.
