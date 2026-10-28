# Contrato interno backend–adquisición v1: primera integración simulada

Especificación escrita ANTES de implementar (26-10-2026). No modifica Next ni los
endpoints/arranque habituales de la API. Solo pila temporal, sin rutas reales, proveedor
real o tráfico a SofaScore. La gestión del perfil queda en el commit 6fecb58; esta
integración se consolida en un commit local separado. Los diagnósticos y datos previos no se consolidan aquí.

## Límite y activación

Servicio acquisition, DNS interno `acquisition:8002`, sin puertos al host. Backend conserva
SQLite deportivo, journal, importación y publicación; acquisition no monta ninguno.
Adquisición dispone solo de perfil y almacenamiento de trabajos/bundles propios.
Se conserva Profile con flock durante su ejecución; no se inicia navegador en el simulador.
No se cambia recuperación manual, seccomp o políticas del backend. El perfil especial de
cinco reglas corresponde exclusivamente a adquisición. Red Docker interna sin salida.

El proveedor de prueba se distribuye SOLO en target test, módulo acquisition_simulated.
Requiere simultáneamente ACQ_TEST_MODE=1 y ACQ_PROVIDER=simulated-local-v1; sin ambos,
o sin el módulo de prueba, el servicio rechaza arrancar. No hay recetas reales en esta
entrega. El cliente backend exige opt-in allow_simulated y marcador de instalación de
prueba junto a la base, creado por el fixture de inicialización, no por respuestas HTTP.
No incorporar esos flags/imagen a los Compose o entornos habituales.

## Autorización y operaciones

Secreto aleatorio de al menos 32 caracteres ASCII, cargado desde ACQ_SECRET_FILE,
montado RO en /run/secrets/acquisition-token, tanto en acquisition como en el backend.
No se incluye en imágenes, código, configuración versionada, logs ni respuestas. Header
Authorization: Bearer <secreto>; comparación constante. Todas las operaciones requieren
autorización antes de leer recursos; 401 genérico. Solo /health de liveness no la requiere.
HTTP se limita a la red privada; no se afirma cifrado en tránsito. El servidor del grupo
necesitará revisar esa frontera o transporte TLS antes de un despliegue fuera de ella.

| Método y ruta | Petición / respuesta |
|---|---|
| POST /internal/v1/acquisitions | JSON exacto {request_id: UUID, recipe_id: enum}; 202 nuevo, 200 duplicado idéntico |
| GET /internal/v1/acquisitions/{UUID} | id, request_id, recipe_id, state, phase, progress 0..100, error seguro y descriptor al terminar |
| POST /internal/v1/acquisitions/{UUID}/cancel | Sin parámetros; cancelación cooperativa, mismo resultado al repetir |
| GET /internal/v1/acquisitions/{UUID}/bundle | Solo completed; JSON inmutable, Content-Length y ETag SHA256 |

Límites iniciales: solicitud 2 KiB, bundle 2 MiB, archivo 512 KiB, máximo 8 archivos;
rechazar JSON inválido, campos extra, IDs inválidos, rutas fuera del patrón, tamaños y
estados no admisibles. 400 entrada inválida, 404 no encontrado, 409 conflicto/no terminado,
503 indisponible. No aceptar comandos, ejecutables, URLs, rutas, demoras o parámetros de
scraper arbitrarios. Recetas fijas de PRUEBA: fixture-complete-v1, fixture-partial-v1,
fixture-fail-v1 y fixture-slow-v1. El último solo permite ejercitar cancelación/reinicio.

Una ejecución queued/running/cancelling simultánea. request_id único durable: repetir
idéntica receta devuelve mismo ID incluso después de terminar; otra receta con la misma
clave devuelve 409; otra solicitud con trabajo activo, 409. SQLite propio, transacciones
breves y synchronous FULL, sin framework genérico de trabajos. Al reiniciar, trabajos no
terminados pasan a interrupted; no se reanudan ni descargan automáticamente. Repetir la
clave conserva interrupted; para nueva ejecución se exige nueva clave explícita.
Estados: queued, running, cancelling, cancelled, completed, failed, interrupted. Un
completed no se cancela retroactivamente. Cancelación aceptada antes de sellar impide
publicar bundle; se serializa la última comprobación con el cierre del trabajo. Un error
o parcial no se convierte en éxito deportivo. No mostrar excepciones internas ni headers.

## Bundle inmutable y recepción

Una envoltura JSON versionada contiene manifest y files (nombre fijo → base64). Manifiesto:
job/request/recipe, provider y revisión, is_simulated=true, inicio/fin de consulta,
ámbito fijo identificado, cobertura complete/partial/none, errores estructurados y lista
{name, kind, team_external_id, bytes, sha256}. Receta determina nombres y formato, NO la
petición. En esta entrega solo raw.jsonl/exportación del equipo fijo de prueba; información
no disponible permanece ausente/null. El JSONL conserva fetched_at y payload originales.
No se aplican aquí normalizaciones deportivas. No se acepta contenido ejecutable ni archivo
anidado/ZIP/tar. Guardar primero con fsync, confirmar completed/descriptor al final. Ningún
recurso parcial se entrega como bundle terminado; un bundle parcial deportivo puede sellarse
como adquisición terminada, pero su coverage=partial impide importarlo/publicarlo.

Descriptor: SHA256 del bundle completo y número de bytes. ETag debe coincidir. El backend
acota la descarga, la copia a staging privado nuevo, fsync y verifica descriptor, identidad,
receta, versión, nombres exactos y únicos, tipos, base64, límites, hashes, cobertura y errores.
Rechaza rutas absolutas, .., symlinks representados, archivos extra y discrepancias. Hashes
acreditan integridad frente al descriptor autenticado, no hacen honesto a un servicio
comprometido. No ejecutar contenidos. Solo coverage=complete y errors=[] puede pasar.
Se conserva la copia verificada e inmutable antes de entregarla al coordinador existente.

El coordinador sigue siendo único dueño del bloqueo deportivo durante descarga y publicación.
La integración cliente es interna; no modifica api.main ni publica endpoints. Se añade una
clave de solicitud opcional al coordinador para que reintentos/reinicio del cliente entreguen
el MISMO trabajo de importación (UUID determinista de adquisición+hash de bundle), incluso si
el COMMIT anterior terminó antes de recibir su respuesta. Rechazar colisión de clave con otras
entradas y otro trabajo activo; las llamadas existentes conservan su semántica. La confirmación
durable y recuperación de SQLite/instantánea siguen siendo las del coordinador, no se duplican.
El cliente nunca presenta adquisición completed como generación publicada: devuelve además
estado del coordinador y generation_id confirmado. Cancelar descarga/adquisición no revierte
una publicación ya confirmada; no inicia importación si adquisición cancelada o incompleta.

## Prueba antes de habilitar otro ámbito

Pila Docker con proyecto, red y volúmenes nuevos; sin puertos host ni montajes de datos reales.
Secreto temporal fuera de Git, no imágenes. Perfil inicializado explícitamente; base nueva
creada SOLO con fixtures locales. Camino: solicitud autenticada → progreso simulado → bundle
sellado → copia privada verificada → coordinador/copia/candidata/importación/verificación →
confirmación/publicación. Captura vieja continúa sirviendo y nueva generación tiene el cambio.

Cubrir auth ausente/incorrecta, campos arbitrarios, duplicados/concurrencia, cancelación,
caída/reinicio sin reanudación, fallo y parcial, bundle manipulado (hash/campos/rutas/cobertura),
reintento de publicación sin doble trabajo, bloqueo de mantenimiento deportivo activo,
y conservación de IDs/originales/decisiones. Perfiles y secretos nunca en Git. No iniciar
servicios habituales ni usar snapshot real. Mantener el secreto y la simulación fuera de
logs. No prometer acceso externo, recetas reales o validación en el servidor del grupo.

## Implementación de esta revisión (consolidada localmente)

Componentes reales:
- acquisition/service.py: HTTP interno autenticado, un trabajo activo y registro SQLite
  propio, cancelación cooperativa, sellado inmutable e interrupción durable al reiniciar.
- persistencia/adquisicion.py: cliente sin redirects/proxies del entorno, límite de
  descarga, copia privada/verificación estricta y entrega al coordinador existente.
- persistencia/actualizacion.py: únicamente identificador opcional durable e idempotente;
  sin cambiar bloqueo, esquema deportivo, recuperación o confirmación de publicación.
- tests/acquisition/acquisition_simulated.py: proveedor SOLO en las imágenes test;
  no va en el runtime administrativo/habitual. integration_flow.py contiene el conductor
  backend de pruebas, no un servidor ni un endpoint nuevo de la API.
- docker/acquisition/Dockerfile.integration, compose.simulated.yaml y test_integration.py:
  construcción offline sobre artefactos locales y pila de prueba explícita independiente.

api.main y el Compose habitual de dos servicios conservan el arranque SQL estático.
No se activa un coordinador en la instalación habitual. El cliente es una interfaz
Python interna, con request_id proporcionado por el llamador; un controlador futuro
necesitará conservar esa clave antes de solicitar trabajo. No se añade control público
para arrancarlo. El simulador jamás solicita a SofaScore ni abre el navegador: el perfil
se mantiene bajo propietario y se verifica limpio, sin recuperación automática.

El modo de prueba requiere el perfil Compose `simulated`, flag servidor, proveedor test
instalado, allow_simulated en el cliente y marcador explícito de base de fixtures. El
marcador no se deduce del bundle ni lo crea el cliente. No es una certificación de datos:
es una barrera de configuración de una instalación deliberadamente aislada. El runner
crea una base NUEVA desde fixtures versionados; no usa snapshots o bases reales.

## Reproducción con recursos exclusivos

Desde api-real-jaen. Requisitos: Docker Linux/Compose v2 funcionando, Python 3.11+ del host
para el runner, espacio y DOS imágenes locales ya auditadas: runtime del perfil y backend
SQL. No descarga dependencias ni imágenes durante estos comandos. Comprobar su identidad
con docker image inspect antes del build; una etiqueta desconocida no es un requisito
satisfecho. Las imágenes utilizadas se registran con ID en los resultados de revisión.
En otra máquina pueden transportarse como artefactos save/load o reconstruirse por sus
procedimientos previos. No hay rutas Windows incrustadas ni código montado en ejecución.

```sh
export RJ_PROFILE_IMAGE='imagen-local-del-runtime-de-perfil'
export RJ_BACKEND_IMAGE='imagen-local-del-backend-sql'
docker image inspect "$RJ_PROFILE_IMAGE" "$RJ_BACKEND_IMAGE"
docker build --network none --pull=false --target acquisition-test --build-arg PROFILE_IMAGE="$RJ_PROFILE_IMAGE" --build-context implementation=./acquisition --build-context checks=./tests/acquisition -f docker/acquisition/Dockerfile.integration -t rj-acq-simulated:review docker/acquisition
docker build --network none --pull=false --target backend-test --build-arg BACKEND_IMAGE="$RJ_BACKEND_IMAGE" --build-context backend=. --build-context implementation=./acquisition --build-context checks=./tests/acquisition --build-context fixtures=./tests -f docker/acquisition/Dockerfile.integration -t rj-acq-integration-backend:review docker/acquisition
python3 -X utf8 docker/acquisition/test_integration.py --acquisition-image rj-acq-simulated:review --backend-image rj-acq-integration-backend:review --evidence /ruta/absoluta/nueva/fuera-de-git
```

El runner genera proyecto/volúmenes nuevos y secreto aleatorio fuera del árbol de código.
No reutiliza volúmenes existentes. Inicializa explícitamente perfil y base de fixtures;
si una operación falla no ejecuta las siguientes: solo desmonta SU pila y elimina SU
secreto en finally. No se borran volúmenes, ni se llama a down -v/prune. Al terminar se
retiran contenedores/redes propios y el secreto; volúmenes exclusivos quedan para revisión.
La carpeta de evidencias no es necesaria para funcionamiento habitual y no se versiona.

Secretos: archivo temporal RO, jamás ARG/ENV con el valor, ni logs/respuestas. En Linux
el operador debe preparar permisos/ACL que permitan leerlo a UID 10001 sin exponerlo a
usuarios ajenos; no se arreglan permisos arrancando como root. Red `internal: true`, sin
puertos al host. El admin del perfil conserva network_mode: none. Backend solo tiene
state+secreto; acquisition solo profile+jobs+secreto, sin journal deportivo. Backend
conserva seccomp predeterminado, cap_drop ALL, NNP y UID 10001; solo acquisition/admin
usan las cinco reglas, sin ampliar ni tocar la política global del motor.

## Resultados propios

Pila final: 16 etapas correctas y 11 pruebas de contrato HTTP correctas dentro de Docker.
Recorrido completo con SQL/candidata/verificación/publicación real del coordinador; vieja
instantánea conservada, generación nueva, equivalencia de respuestas con la base y
conservación de IDs, originales y decisiones locales. Reintento tras reiniciar coordinador
no duplica importación/publicación. Mantenimiento SQL externo continúa rechazado durante
la vida del coordinador. No se debilita el bloqueo para verificar respuestas: se compara
la lectura SQL completa después de cerrar el propietario.

Se probaron: secreto ausente/incorrecto (401 en el servicio de la pila), activación de
simulación deshabilitada por defecto y sin marca de fixtures, parámetros arbitrarios,
cuatro solicitudes duplicadas concurrentes, conflicto de clave/receta y otro trabajo
activo, cancelación repetida, fallo y cobertura parcial sin publicación, SIGKILL/recreación
de acquisition con estado interrupted sin reanudación, corrupción física de bundle
sellado rechazada sin publicación. Validador backend probado también con hash exterior
recalculado pero archivos, rutas, hashes internos, identidad, cobertura o errores alterados,
y claves JSON duplicadas. Los logs se comprobaron contra el secreto real de esa pila.

Regresión en checkout Windows: 239 pruebas con SQL, 2 omitidas; entorno temporal sin SQL:
239 pruebas, 92 omitidas. Para ejecutar en Windows se necesita PYTHONUTF8=1 heredado por
los subprocesos; pasar solo -X utf8 al padre no lo consigue. Esa variable se usó solo en
los procesos de pruebas, sin modificar .env.local o configuración global.

La ejecución genérica de la suite en la imagen SIN Git tuvo 211 pruebas, 3 omitidas y
DOS fallos de precondición del entorno: prueba de procedencia exige commit Git conocido y
prueba de destino dentro del repo requiere .git (sin él intenta crear lock en /app RO para
UID 10001). No se presenta esa ejecución como correcta ni se cambiaron esas pruebas para
ocultarlo. La pila/contrato/coordinador Linux sí se comprobaron con las pruebas específicas;
la suite general pasó en el checkout real. Queda validar el checkout Linux completo en el
servidor del grupo, además de kernel, LSM, filesystem y permisos de secretos/volúmenes.

Fuera de esta revisión: proveedores/recetas reales, política histórica/fichas, descarga
real, autenticación de visitantes al futuro control backend, endpoints Next y botón,
activación habitual, TLS fuera de la red confiable, rotación dinámica de secretos y
retención/cuotas de trabajos/bundles. No se promete idoneidad de este servicio simulado
para producción. La cancelación de adquisición no cancela/revierte una publicación que
ya haya entrado en el coordinador; su resultado durable es la autoridad.

Añadir persistencia/adquisicion.py y la clave del coordinador cambia la huella conservadora
del código de persistencia. No desplegarlo contra la base activa sin revisar compatibilidad
con esa huella y el procedimiento de activación; aquí solo se crearon bases temporales.

Evidencia final de revisión: docker/acquisition/integration-validation.json (solo
resumen, sin secretos, perfiles, cookies, respaldos o trazas). Logs/inspect completos
quedan ignorados en rj-analyst/artifacts/acquisition-integration-2026-10-26/.
Los archivos Python principales se compararon por SHA256 con las imágenes ejecutadas.
El perfil seccomp normalizado LF es JSON idéntico a las cinco reglas aprobadas; la
corrección CRLF/LF se consolidó solo en el commit de gestión del perfil.
ETag del bundle es un valor HTTP entre comillas; el cliente exige el digest fuerte
correspondiente al descriptor autenticado. No acepta redirecciones a otras URLs.
La pila final se retiró; no quedan sus contenedores/redes ni su secreto temporal.
Solo quedan volúmenes de fixtures exclusivos para inspección. No se hizo push. Esta integración se consolida en un commit local
independiente de la gestión del perfil.
