# Piloto SofaScore: preparación técnica, sin autorización de red externa

> Estado 27-10-2026: el transporte HTTP del piloto quedó sustituido por el flujo normal del SDK con tráfico
> reducido ([adquisicion-sofascore.md](../adquisicion-sofascore.md)). Este documento sigue siendo la referencia del
> protocolo v2 (inventario, cobertura, control de generación, bundle), que se conserva hasta que la solución
> definitiva demuestre las mismas garantías. Resultados y preflights del piloto: [historico/](README.md).

## Aclaraciones previas a la implementación

Los 380 IDs de acquisition/sofascore-scope.json son el conjunto de eventos con payload raw de las 20 exportaciones archivadas auditadas. No son el inventario completo ni equivalen a encuentros vigentes. La importación archivada 2026-10-22-grupo2-j38 conserva 382 eventos del grupo: incluye además 16599776 y 16599788, solo de calendario de J38. Los 380 ya incluyen los dos aplazados y sus reemplazos. Decisiones locales: 16599956 -> 17118209 y 16599919 -> 16969545. Tras esas decisiones hay 380 vigentes y dos históricos en esa evidencia, conservados todos. No se ha abierto la base activa para comprobar su inventario actual.

El receptor obtiene inventario de su propia generación de prueba bajo el coordinador: IDs, estado vigente/histórico, reemplazo, participantes, originales seleccionados y procedencia. Su huella vincula solicitud, bundle y generación. Las decisiones son del backend, nunca se infieren de SofaScore ni se importan del servicio. Histórico sustituido se conserva por referencia al inventario, sin exigirle descargas ni volverlo vigente. Ningún contador fijo sustituye al inventario; los 20 equipos sí siguen siendo el ámbito fijado.

Seis horas es el mínimo de refresco de secciones válidas, no el de reintento. Política inicial separada: timeout/red y 5xx, espera de 30 s con backoff exponencial hasta 15 min; parseo, vacío o 404 sin regla, 15 min y pendiente; 429, máximo de Retry-After y 15 min; 403, detener y requerir revisión administrativa. Retry-After válido se respeta también con 503 y nunca se recorta para entrar en el trabajo. Dos intentos máximos por recurso y trabajo. Agotados, no volver a intentarlo en ese trabajo. Fechas de siguiente intento y bloqueos se conservan durables; repetir solicitudes/reiniciar no reinicia el contador ni el plazo. Un 403/429 detiene el piloto completo, además del breaker del SDK; no hay cambio de transporte para eludirlo. Esperas interrumpibles y plazo global: si no cabe, dejar pendiente, sin esperar indefinidamente ni publicar.

El transporte del piloto tiene cinco rutas fijas: una página de ronda 1 y evento/lineups/statistics/incidents de 16599363. El presupuesto se comprueba y se persiste ANTES de cada intento HTTP, también los reintentos. Sin redirects, proxies, warmup implícito ni navegador en este piloto. No aceptar host/URL/ruta del solicitante. Por defecto solo loopback simulado; la salida a SofaScore queda deshabilitada y su habilitación requiere autorización posterior. Se reutiliza FetchPipeline para interpretación y almacenamiento; no se reimplementan reglas deportivas en el transporte.


## Implementación y contrato vigente

Esta especificación supera el borrador v2 de receta-sofascore-offline.md en inventario y reintentos. El manifiesto de equipos sigue fijando 20 correspondencias verificadas; sus 380 IDs son testigos raw archivados, no una condición de cobertura. Ni 380 ni 382 están fijados en el receptor. El productor y receptor se vinculan por job_id/request_id, generación, SHA del inventario y huella de productor aprobada.

Archivos principales:

- acquisition/pilot_transport.py: cinco rutas fijas, propietario flock durante la sesión, ledger durable, presupuesto previo a cada intento, tasa compartida entre trabajos del mismo almacenamiento y fechas de reintento persistentes. El archivo de propietario no se borra. Estado ambiguo tras caída consume presupuesto y queda pendiente; no hay ejecución automática al construir o reabrir el transporte.
- acquisition/pilot_bundle.py: inicialización explícita de caché desde evidencia seleccionada de la base de prueba, lease writer del SDK, copia soportada y verificada antes, Listing/FetchPipeline existentes y exportación raw/normalizada mediante el SDK. No monta SQLite ni journal deportivo. Copia posterior verificada antes de sellar; fallo de mantenimiento detiene el flujo. Los parciales quedan como evidencia, sin bundle publicable. Una nueva ID o una corrección deportiva de otro evento descubierta en la ronda detiene el piloto sin consultar más recursos.
- persistencia/bundle_v2.py: inventario y verificador compartido, solo biblioteca estándar y las funciones existentes de api/importar. Sin dependencias del scraper, Patchright o Chromium. La ausencia confirmada sigue sin regla válida: se rechaza, particularmente si contradice datos anteriores.
- persistencia/adquisicion_v2.py: recepción privada explícita, deshabilitada por defecto; descarga interna con secreto y sin redirects/proxies, límite de bytes y ETag, copia privada/fsync, verificación y entrega de exportaciones/calendario al coordinador existente. No añade endpoints ni registra una receta en el servicio habitual. El modo loopback exige enable_test y un marcador de instalación de prueba; una base marcada no admite el modo real.
- persistencia/actualizacion.py: generación esperada comprobada bajo la puerta al solicitar y de nuevo antes de preparar candidata. Se mantiene el bloqueo original durante todo el proceso y la misma única vía de restauración. La publicación conserva la confirmación durable y el intercambio de instantánea anteriores; lectores que capturaron la anterior la conservan.

Bundle schema=2/contract=pilot-v2, distinto del borrador offline anterior: 20 archivos propios team_ID.jsonl, calendar.jsonl completo respecto al inventario y provenance.jsonl con los cuerpos HTTP originales exitosos en base64, ruta, número y fecha de recepción. El manifiesto conserva todos los intentos, hashes, cobertura y referencias exactas a archivo/línea. La copia privada conserva también ese manifiesto y la procedencia antes de importar. La SQLite deportiva solo la escribe el backend con su importador único.

La cobertura de los vigentes exige calendar/event/lineups/statistics/incidents; los diferidos por fase se identifican como no aplicables. Los históricos se declaran retained según las decisiones del backend y no disparan descargas ni adquieren vigencia. Las dos filas de calendario sin fecha se conservan exactamente desde el inventario y se declaran de origen inventory, con fecha desconocida; no se presentan como consultas recientes. El resto no consultado se identifica como reutilizado del inventario. Completar este piloto NO significa refrescar todos los partidos o estadísticas de los 20 equipos.

Cuando el SDK recibe un payload idéntico puede conservar su fetched_at original. Por eso checked_at acredita la consulta nueva y se contrasta con el intento y su cuerpo original, mientras fetched_at continúa describiendo la versión conservada. No se inventa una versión ni se fecha de nuevo el histórico por reutilizarlo. Una pulsación/trabajo repetido con el mismo bundle vuelve al resultado durable, sin más peticiones ni otra publicación.

Se mantienen límites propuestos de 4 MiB por archivo, 64 MiB decodificados y 96 MiB de envelope; ahora son 22 archivos exactos por la procedencia original añadida. La medición de esta prueba fue 31.587.177 bytes de envelope. El calendario exportado desde caché tenía 380 filas y se completó con las dos originales de solo calendario: 382 conservadas, sin inventar raw para ellas. Ese tamaño no garantiza cubrir una temporada totalmente finalizada; un exceso se rechaza sin truncar.

## Corrección necesaria encontrada en la persistencia

La recepción realista detectó que JSON con iguales valores y distinto orden de claves comparte content_hash, pero la derivación provisional calculaba ordinales desde el orden nuevo y la verificación posterior desde content.payload_text ya conservado. Se rechazaba el COMMIT con 3403 grupos call_up_stat discrepantes. No se eliminó la verificación ni se ordenaron o regeneraron datos deportivos.

estado_deseado ahora enlaza cada hash con la representación que realmente conserva content antes de invocar el importador único. Conserva valores, null, arrays, identidades y evidencia; la representación almacenada y sus ordinales siguen siendo los mismos. La nueva prueba test_orden_payload.py invierte claves, importa la observación nueva y verifica COMMIT, coherencia y ordinales intactos. No se ha reconstruido la base activa.

## Resultado propio y reproducción offline

Informe de metadatos: [piloto-sofascore-validacion.json](piloto-sofascore-validacion.json). Preflight: [preflight-sofascore-piloto.json](preflight-sofascore-piloto.json).

- 16 pruebas de transporte y artefactos: presupuesto antes de socket, backoff/Retry-After, rechazo global 403/429, ausencia no inferida, límite por recurso, cierre/reapertura, exclusión, disco lleno antes de petición o al conservar respuesta, cancelación, cobertura incompleta y corrección fuera del plan.
- 31 pruebas del adaptador, caché y bordes temporales con reloj fijo, incluyendo el límite de 30 segundos de reintento transitorio separado de las seis horas de refresco válido; navegador local con su sandbox anterior, sin permisos nuevos.
- Recorrido en dos contenedores sin red, con volúmenes nuevos y etiquetados: inventario de la copia -> cinco HTTP GET loopback -> caché -> bundle -> descarga interna autenticada -> candidata -> verificación -> publicación. Dos trabajos independientes permiten comprobar primero un fallo después del COMMIT de candidata y luego éxito. Rechazo de cuatro manipulaciones, generación antigua y proveedor sin habilitar. Reinicio conserva generación confirmada y repetir publicación es idempotente.
- 2792 respuestas de la instantánea publicada equivalen al lector SQL/API independiente usado por la prueba. IDs de equipos/jugadores/partidos, observaciones anteriores y decisiones locales conservados. Integridad correcta. Backend sin SDK ni navegador instalados.
- Suite API: 240 pruebas con SQL, dos omitidas; 240 sin SQL en entorno temporal sin pip, 93 omitidas. ResourceWarning tratado como error. Además, 21 focalizadas de sincronización y regresión específica del orden JSON satisfactorias.

Se usó un snapshot consistente archivado del 25-10-2026, nunca la SQLite activa ni una copia directa de ella. La copia de prueba pasó una reconstrucción explícita para acreditar el código actual antes de abrir el coordinador; no se debilitó esa comprobación y no se ejecutó mantenimiento automático en el arranque habitual. No se mezclaron valores deportivos sintéticos con los datos archivados: el recorrido usó sus originales, servidos por HTTP local de prueba y marcado como loopback-fixture. Los tests unitarios sintéticos usan otro almacenamiento temporal.

Los contenedores de adquisición no montaron el volumen deportivo ni el journal. Backend mantuvo usuario 10001, capabilities cero y no-new-privileges, sin perfil seccomp de navegador. No se cambió ninguna de las cinco excepciones aprobadas de adquisición. Todos los contenedores de este ensayo usaron network:none y no publicaron puertos. Se retiraron solo sus contenedores/volúmenes tras comprobar las etiquetas; sin limpieza global.

Comando portable de reproducción, con rutas de evidencia adaptadas y directorio de salida nuevo:

```sh
python scripts/test_pilot_v2.py --snapshot /evidence/inactive-snapshot.sqlite --backend-image IMAGE_BACKEND_LOCAL --acquisition-image IMAGE_ACQUISITION_LOCAL --scraper-source ../sofascore_scraper/sofascore_scraper --output /tmp/pilot-v2-report-new
```

Requiere Docker Linux, imágenes locales auditadas, checkout fijado de scraper de solo lectura para contraste de hashes y SQLAlchemy/Alembic en el Python del runner. No instala ni descarga dependencias. El SDK se contrasta contra 611e436a1528b0f487aa56a045f023e8ae8ae098; el cambio ajeno de package-lock.json no se empaqueta. El runner copia mediante el mecanismo SQLite existente, crea almacenamiento exclusivo, se detiene ante cualquier fallo y no arranca servicios habituales. El informe exportado no contiene SQLite, cuerpos, cookies, secretos ni journal.

Empaquetado opcional source-only ya construido y comprobado sin montar código: Dockerfile.pilot y scripts/build_pilot_image.py incorporan el módulo compartido y api/importar al runtime de adquisición, sin SQL ni instalaciones nuevas. Imagen local de revisión: rj-pilot-package:review-20261026-final; ID en el informe. Arranque por defecto sigue siendo administración offline, no proveedor real. Ejemplo portable:

```sh
python scripts/build_pilot_image.py --runtime-image RUNTIME_LOCAL_AUDITADO --tag TAG_LOCAL_NUEVO
```

La herramienta exige imagen ya local, usa su ID inmutable, contexto temporal con lista explícita de fuentes y constructor clásico sin pull ni red. DOCKER_BUILDKIT=0 se limita al subproceso, no cambia configuración global. El constructor clásico está deprecado: si no está disponible en el servidor, este helper fallará; habrá que preparar un build local equivalente de BuildKit con base ya disponible y volver a validarlo, sin asumir acceso a registros. Las imágenes de runtime se deben cargar o construir según la entrega anterior; no prometer instalación sin esos requisitos. Ninguna imagen fija rutas Windows ni monta código/node_modules del anfitrión en producción.

## Obtención del snapshot: antecedente de la entrega anterior

En la entrega anterior no se intentó obtenerlo ni se detuvo/reinició ningún servicio. El 27-10 el usuario aportó la copia indicada en la adenda siguiente; ya no es un requisito pendiente para esa instalación de prueba. La API SQL mantiene su bloqueo aunque ya haya cerrado la conexión SQLite: una copia administrativa necesita una ventana sin la API propietaria ni otro escritor. La ventana termina al finalizar la copia coherente y su comprobación; duración dependiente del almacenamiento, no estimada aquí. El usuario detiene/reanuda sus servicios cuando autorice esa ventana.

PowerShell, una sola línea, con directorio y rutas explícitos. NO ejecutado:

```powershell
Push-Location -LiteralPath 'C:\Users\broke\dev\api-real-jaen' -ErrorAction Stop; try { & 'C:\Users\broke\dev\api-real-jaen\.venv\Scripts\python.exe' -B -m persistencia copiar --base 'C:\Users\broke\dev\datos-importados\base\real-jaen.sqlite' --motivo 'piloto-16599363'; if ($LASTEXITCODE -ne 0) { throw 'Copia rechazada: no continuar' } } finally { Pop-Location }
```

La ayuda confirma --base y --motivo. copiar genera y verifica una copia en copias y devuelve ruta, revisión y huella lógica contrastada con el origen bajo bloqueo. Si está en uso se rechaza: no detenerla desde el script ni copiar su fichero directamente. Guardar ese informe; verificar después la ruta devuelta con `python -m persistencia verificar --base RUTA_DEVUELTA`. Exigir revisión 0004, integridad, equivalencia lógica y ningún error antes de preparar almacenamiento de prueba. Cualquier adopción de código/reconstrucción posterior se limita a esa copia identificada, jamás al origen activo. No continuar tras una operación fallida.

## Condiciones antes de autorizar red real

El preflight autoriza proponer, todavía no ejecutar, cinco GET del evento 16599363 y ronda 1. Host único https://www.sofascore.com/api/v1, sin proxies, redirects, calentamiento ni fallback de navegador. Concurrencia 1, tasa máxima 1/s, timeout 10 s, dos intentos por recurso, presupuesto global 16 y plazo 300 s; con cinco rutas y dos intentos el máximo efectivo es diez. No hay llamadas auxiliares ocultas. Los recursos pueden ordenarse según el registro del SDK; calendario y evento preceden a sus secciones. Es una excepción dirigida a un partido antiguo, no revisión histórica rotatoria.

Antes de ejecutar: snapshot reciente verificado e inventario de su generación, copia/caché y marcadores de prueba, huella de productor aprobada, secreto interno nuevo por archivo y revisión de identidad de los volúmenes propuestos (aún no creados). El receptor v2 solo se habilita en esa instalación de prueba. No conectar este piloto al control ni al servicio habitual; requerirá una invocación interna explícita de PilotTransport/collect_pilot y Receptor con ese contexto, no un endpoint o botón público.

Validado en Docker Desktop Linux amd64. Pendientes: servidor del grupo, sus permisos/bloqueos/espacio, imágenes compatibles y DNS/TLS/salida permitida, además de la respuesta real de SofaScore. HTTP del piloto no requiere excepciones de sandbox; si posteriormente se usara navegador, seguiría su revisión y recuperación manual ya aprobadas, sin ampliar permisos ni eludir rechazos. El receptor v2 se ha ejercitado en Linux; esta entrega no afirma validación de su recepción directa fuera de Docker en Windows. El bloqueo es cooperativo y no coordina programas externos que lo ignoren.

Sin peticiones externas, cambios de base activa, servicios, frontend, scraper o .env.local. Sin commit ni push. Detenerse para revisión antes del piloto real.

## Adenda 27-10-2026: copia aportada y preparación de prueba

Resultado propio en [piloto-sofascore-copia-20261027.json](piloto-sofascore-copia-20261027.json); preflight actualizado en [preflight-sofascore-piloto.json](preflight-sofascore-piloto.json). Se utilizó exclusivamente `real-jaen.20261027-083912-846469.piloto-16599363.sqlite`, aportada por el usuario. No se examinó la base activa: esta comprobación acredita la copia suministrada, no certifica por sí sola cuándo se generó respecto a las últimas importaciones del origen activo.

Copia SQLite coherente bajo bloqueo hacia un directorio temporal nuevo, retenido para revisión. Antes de modificarla se comprobaron revisión 0004, integridad, claves foráneas y equivalencia de la huella lógica completa con la copia suministrada. SHA-256 del archivo suministrado sin cambios antes/después. Reconstrucción explícita solo en la base de prueba para adoptar reglas actuales, con respaldo previo del mecanismo existente: cero cambios deportivos. IDs de equipos, jugadores y partidos, observaciones originales y sustituciones locales conservados. La verificación posterior no detecta diferencias.

Inventario de la generación confirmada: 20 equipos (correspondencia exacta con el manifiesto), 382 eventos del ámbito, 380 vigentes y dos históricos: 16599919 -> 16969545 y 16599956 -> 17118209. Dos eventos solo de calendario: 16599776 y 16599788. La base completa contiene 393 partidos; los once fuera del ámbito se conservan y no se consultarán. Estados del inventario: 80 finalizados, 300 no iniciados, dos aplazados históricos. El evento 16599363, CE Europa–Real Jaén, sigue vigente y finalizado, con event, lineups, statistics e incidents originales. Inventario completo con payloads solo fuera de Git, en el directorio de prueba; preflight contiene IDs e identificadores de generación/huella, sin originales.

Se reabrió el coordinador y coincidieron generación e inventario. Una verificación administrativa se rechazó mientras mantenía el bloqueo; tras cerrarlo, volvió a completarse. No hay actualizaciones automáticas ni proveedor real habilitado. No se repitieron suites completas ni el recorrido de adquisición/publicación: el motor Linux de Docker no responde (pipe dockerDesktopLinuxEngine ausente), por lo que caché SDK, volúmenes y publicación offline con esta nueva copia quedan pendientes. Los resultados Docker anteriores corresponden a la copia archivada anterior y no se atribuyen a esta.

Cinco rutas exactas del preflight; concurrencia uno, separación mínima de un segundo entre inicios, timeout de diez segundos y plazo total de 300 segundos. Presupuesto global dieciséis, máximo dos intentos por ruta: máximo efectivo diez GET, cinco si todo funciona. Máximo un reintento por recurso, no dieciséis solicitudes posibles en este piloto. Se contabiliza durablemente antes del socket; reiniciar no reinicia presupuesto ni plazo. Sin warmup, redirects, proxies ni fallback de navegador.

Red/timeout/5xx permiten un reintento tras al menos 30 segundos y Retry-After si es mayor; si la espera no cabe en el plazo, se aborta. 403 detiene el trabajo y requiere revisión administrativa. 429 detiene el trabajo y conserva espera mínima de 900 segundos o Retry-After si es mayor. Vacío, 404, parseo y otros HTTP no producen reintento inmediato: espera durable de 900 segundos, cobertura incompleta y rechazo de publicación; no acreditan ausencia. Breaker abierto detiene el trabajo. Un intento interrumpido consume presupuesto y conserva espera de 30 segundos. Las seis horas regulan refresco de secciones válidas, no esta política de reintentos. No se programa automáticamente otro trabajo para sortear los límites.

Antes de la prueba real: disponer del motor Linux, validar esta copia en la pila aislada, preparar caché fijada y secreto de prueba, comprobar identidad del almacenamiento y autorizar expresamente los cinco GET. El directorio temporal es evidencia de esta instalación de ensayo, no dependencia de producción. No se crearon ni reutilizaron volúmenes Docker en esta entrega; no se cambiaron servicios ni configuración global. Sin llamadas externas, commit o push.

## Adenda Linux 27-10-2026: validación offline completada

El motor volvió a responder. Se ejecutó scripts/test_pilot_v2.py con la copia aportada del 27-10 y las imágenes locales fijadas, sin pull ni red externa. Resultado propio en [piloto-sofascore-linux-20261027.json](piloto-sofascore-linux-20261027.json). Esta adenda supera el pendiente del motor de la sección anterior; la validación del servidor del grupo sigue pendiente.

16 pruebas focalizadas superadas. Recorrido completo satisfactorio: inventario -> caché SDK -> cinco GET de servidor loopback -> bundle de 31.587.177 bytes -> recepción autenticada -> candidata -> verificación -> publicación. Se ejecutaron dos trabajos independientes de cinco GET locales: uno para inyectar fallo posterior al COMMIT de candidata y otro para publicar. Ninguna solicitud externa. 2792 respuestas equivalentes al lector independiente, IDs, originales y decisiones conservados, integridad correcta. Se comprobaron cuatro manipulaciones rechazadas, autenticación, duplicados idempotentes, generación antigua rechazada, reinicio y conservación de la instantánea anterior al fallar candidata. Backend sin SDK ni navegador instalados.

Linux x86_64 de Docker Desktop, UID 10001, capabilities cero, no-new-privileges, network:none, sin puertos publicados. Adquisición sin acceso al volumen deportivo/journal. Sin excepciones adicionales ni navegador en este recorrido HTTP. Los tres volúmenes aleatorios exclusivos se verificaron por identidad y se retiraron al acabar; no se conserva una instalación Linux activa ni se reutilizaron volúmenes habituales. La copia suministrada mantiene su SHA-256. Se conserva la instalación Windows de revisión anterior. La generación/huella del ensayo Linux es distinta porque se creó un coordinador independiente: no sustituye la generación del preflight retenido ni permite publicar un bundle entre ambas instalaciones.

Límites confirmados por pruebas: cinco rutas, dos intentos por ruta, máximo efectivo diez (presupuesto global dieciséis), concurrencia uno, intervalo mínimo un segundo, timeout diez segundos y plazo 300 segundos. Retry-After, rechazo 403/429, presupuesto durable, reinicio, cancelación y prohibición de inferir ausencia de vacíos/404 comprobados con HTTP local. Proveedor real deshabilitado. Antes del piloto externo: autorización expresa y preparar caché/almacenamiento exclusivos asociados exactamente a la generación que se vaya a usar. No se activaron servicios, botón ni consultas reales; sin commit ni push. No se repitieron suites completas.

## Piloto real autorizado 27-10-2026: detenido por HTTP 403

Se revalidaron la base de prueba Windows retenida y su inventario/generación exactos. Se preparó una caché exclusiva en un volumen nuevo, etiquetado con el UUID del trabajo. Adquisición Linux usó la imagen fijada y las reglas aprobadas, sin acceso a la SQLite deportiva o journal, sin secretos/cookies, usuario sin privilegios, capabilities cero y no-new-privileges. Se habilitó el transporte real exclusivamente en esta invocación con max_attempts=10; no se activó ningún servicio habitual.

El primer GET, /unique-tournament/17073/season/97382/events/round/1, recibió 403. Un solo intento, ningún reintento, ninguna otra ruta ni navegador. El transporte detuvo el trabajo y conservó el bloqueo administrativo durable contra nuevos intentos. No se generó bundle, no se preparó candidata y no se intentó publicar. El código HTTP acredita rechazo; no se atribuye a una causa concreta (IP, WAF, cliente, geografía o política del proveedor) sin evidencia adicional. No se intenta eludirlo ni cambiar cabeceras/transporte.

SHA-256 de la base de prueba y generación/inventario idénticos antes/después. No se abrió la base activa ni se modificaron servicios. Evidencia propia y ubicaciones en [piloto-sofascore-real-20261027.json](piloto-sofascore-real-20261027.json). Se conserva el volumen exclusivo con caché, respaldo verificado y evidencia original del rechazo para revisión; el contenedor terminado se retiró tras comprobar su etiqueta. No se incluyen payloads, caché o datos en Git. El recorrido real hasta publicación queda sin validar por este bloqueo, aunque el offline había pasado. No se repetirá el piloto automáticamente. Sin commit ni push.
