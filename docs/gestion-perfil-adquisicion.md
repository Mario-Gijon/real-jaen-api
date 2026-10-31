# Gestión del perfil de adquisición: entrega interna offline

> Estado 29-10-2026: el servicio de adquisición y su overlay (`app-real-jaen/compose.acquisition.yaml`) existen y están
> en uso; este documento describe la gestión del perfil (`acquisition/profile.py`, `browser.py`, `admin.py`), que
> sigue vigente. Estado general: [adquisicion-sofascore.md](adquisicion-sofascore.md).

Implementado únicamente `acquisition/profile.py`, `browser.py` y `admin.py`.
No hay servicio HTTP, descarga, integración deportiva, autorización entre servicios,
recetas, exportador ni botón. No modifica el scraper instalado. El diseño de referencia
sigue en perfil-adquisicion-diseno-final.md; su overlay de adquisición completa NO existe.

## Propiedad, generación y recuperación

La raíz persistente es `/profiles`. Contiene `control/owner.lock` permanente,
`control/state.json`, `control/maintenance.json` cuando hay mantenimiento incompleto,
`generations/<uuid>` y `backups/<uuid>` más sus manifiestos. El perfil del navegador es
la generación activa, no la raíz. Todos los comandos y sesiones usan el mismo flock
exclusivo, inode y ruta canónica. Se validaron aliases Linux por punto y symlink de la
raíz; no se admiten enlaces que redirijan generaciones o coordinación.

Un arranque/sesión normal adquiere propietario, exige estado limpio y ausencia de
singletons inesperados, deja durablemente la marca de sesión y abre exactamente esa
ruta. No recupera al arrancar. Tras cerrar navegador e hijos y fsync, marca limpio.
Un SIGKILL deja pendiente incluso si no quedan singletons. El usuario recibe
`profile_recovery_required` / «Perfil pendiente de recuperación». Busy devuelve 73;
error/recuperación necesaria, 74; error de argumentos, 2; éxito, 0.

Recuperar exige AMBAS confirmaciones administrativas en esa invocación. No se leen
autorizaciones de JSON, entorno, hostname, PID o antigüedad. El operador debe haber
detenido cualquier consumidor y servicio que acceda al perfil; los flags son una
precondición declarada, no prueba técnica. Aun con ellos, flock ocupado rechaza.

Bajo ese bloqueo: manifiesto SHA256/tamaños/enlaces de fuente, copia completa verificada
y fsync antes de crear/modificar candidata. Solo se retiran en candidata los enlaces
SingletonLock/Socket/Cookie, sin seguir destinos. Se abre/cierra offline con sandbox y
se contrasta chrome://sandbox con /proc (ruta exacta, PID namespaces, filtros adicionales,
NNP y capabilities de renderers). Fuente y respaldo se vuelven a verificar. Solo entonces
se reemplaza durablemente el puntero activo. La marca de mantenimiento bloquea arranques
si cae antes o después de ese cambio; repetir recuperación requiere confirmaciones nuevas.

El navegador usa Patchright directamente sobre una única ruta; no llama al mecanismo
de perfiles secundarios del scraper. Un fallo de apertura no crea otra generación ni
intenta perfil vacío. No se reutilizó ni importó código de los scripts diagnósticos.
El administrador comprueba además la red real del contenedor (solo interfaz lo); no es
un sustituto de `network_mode: none`, que la configuración y pruebas exigen.

Los archivos internos SQLite/WAL del navegador se preservan como parte del perfil.
Nunca se abren datos deportivos ni se borran sus diarios. Copias y generaciones no se
limpian automáticamente. Se requiere espacio para dos copias y 32 MiB de margen; no
es una reserva garantizada. Errores de disco/copia/parada dejan la fuente intacta y
mantenimiento pendiente; una copia parcial nunca se presenta como respaldo verificado.

## Imagen reproducible sin descargas en esta entrega

`docker/acquisition/Dockerfile` tiene targets `runtime` y `test`. El primero NO contiene
los diagnósticos antiguos ni los instrumentos de fallo. El segundo añade únicamente
las pruebas nuevas de esta implementación. No montar código o entornos del anfitrión.

La construcción offline requiere una imagen de runtime de navegador YA DISPONIBLE y
revisada. La utilizada tiene ID
`sha256:e13cef0eff2f0d87e8023f149eebf1fdd2682613a4788611c3f9c1efcb856b62`,
Python 3.14.4, Patchright 1.63.0, Chromium 153.0.8010.12 revisión 1243 y exportación del
scraper 611e436a1528b0f487aa56a045f023e8ae8ae098. Es el artefacto previamente preparado,
no una descarga ni un build desde el working tree del scraper. Contiene dependencias
SQL deportivas heredadas que esta entrega NO importa; retirar esas dependencias y
producir una base de adquisición mínima sigue pendiente para el servicio completo.

No se promete construir sin ese requisito. `PROFILE_RUNTIME` no tiene fallback útil;
comprobar la identidad local ANTES de build. Para reproducir en otra máquina puede
transportarse la imagen final con docker image save/load, comprobando su ID, sin
necesitar el runtime intermedio, Python, Node ni PowerShell del host. Para reconstruir,
transportar también el runtime auditado como artefacto; reconstruirlo desde fuentes
es una preparación separada que requiere sus dependencias y acceso a registros. No depende de artifacts ni de rutas temporales para funcionar.

Desde la raíz de `api` (comandos POSIX; variables elegidas por el operador):

```sh
export RJ_PROFILE_RUNTIME='etiqueta-local-del-runtime-auditado'
docker image inspect "$RJ_PROFILE_RUNTIME" --format '{{.Id}}'
```

Continuar SOLO si coincide la identidad anterior, o tras revisar explícitamente otro
runtime y volver a validarlo. No instalar navegadores o resolver dependencias al arrancar:

```sh
docker build --network none --pull=false --target runtime --build-arg PROFILE_RUNTIME="$RJ_PROFILE_RUNTIME" --build-context implementation=./acquisition --build-context backend=. -f docker/acquisition/Dockerfile -t rj-profile-admin:runtime-20261026 docker/acquisition
docker build --network none --pull=false --target test --build-arg PROFILE_RUNTIME="$RJ_PROFILE_RUNTIME" --build-context implementation=./acquisition --build-context backend=. --build-context checks=./tests/acquisition -f docker/acquisition/Dockerfile -t rj-profile-admin:test-20261026 docker/acquisition
```

Build usa únicamente contextos de código dedicados. El pin de cinco reglas es
`docker/acquisition/seccomp-linux-amd64.json`, idéntico al diagnóstico aprobado,
SHA256 abd5cc8ed37eb8a76b57b6aa9e8042e79ac7f04cb189782e2c8565c0765c5cb7.
No se añade setns/clone3 ni permisos del host. La arquitectura validada es Linux x86_64;
no se debe reutilizar este perfil para otro ABI sin revisión. No se fija platform.

## Administración con Compose independiente

Requiere Docker Linux, Compose v2, imagen local, disco y volumen EXCLUSIVO preparado,
con permisos UID/GID 10001 y semántica flock/fsync adecuada. El Compose habitual de dos
servicios NO se modifica. `docker/acquisition/compose.offline.yaml` solo define un comando
administrativo de una ejecución: sin red, puertos, dependencias, secretos o socket Docker.
`init`, cap_drop ALL, NNP, usuario 10001 y shm privado de 512 MiB permanecen activos.

Preparación inicial (elegir un nombre nuevo y comprobar que NO existe antes de crearlo;
si existe, detenerse y revisar su identidad, nunca reutilizarlo o sobrescribirlo a ciegas):

```sh
export RJ_PROFILE_IMAGE='rj-profile-admin:runtime-20261026'
export RJ_PROFILE_VOLUME='nombre-nuevo-exclusivo-elegido-por-el-operador'
docker volume inspect "$RJ_PROFILE_VOLUME"
```

Solo si la inspección confirma que NO existe (no confundir fallo del motor con ausencia):

```sh
docker volume create --label rj.profile.purpose=offline-administration "$RJ_PROFILE_VOLUME"
docker compose -p rj-profile-admin-offline -f docker/acquisition/compose.offline.yaml run --rm --no-deps admin initialize-profile --confirm-new-profile
```

El volumen nuevo hereda el propietario de /profiles de la imagen (sin volume nocopy).
Inicialización rechaza datos existentes y cualquier perfil administrado; no es un reset.
No ejecuta Chromium ni crea datos deportivos. Para un perfil ya inicializado compatible:

```sh
docker compose -p rj-profile-admin-offline -f docker/acquisition/compose.offline.yaml run --rm --no-deps admin verify-profile
```

No existe todavía un servicio acquisition que arrancar/detener en esta entrega. Para
recuperar, el administrador debe detener por sus medios el consumidor del perfil y
confirmar que ningún otro proceso/host lo usa. Luego, SOLO bajo esas precondiciones:

```sh
docker compose -p rj-profile-admin-offline -f docker/acquisition/compose.offline.yaml run --rm --no-deps admin recover-profile --confirm-service-stopped --confirm-no-external-users
```

Si y solo si termina con éxito, ejecutar verify-profile. Ante cualquier error, detenerse;
no continuar ni arrancar consumidor desde finally o comandos que ignoren su salida.
Nada de down -v, prune o borrado manual de locks. El archivo de coordinación permanece.
La recuperación no acredita autenticación del proveedor ni caducidad de cookies reales.

## Validación reproducible offline

Las pruebas nuevas no importan los diagnósticos históricos. Requieren Python 3.11+ en
el host para el runner, Docker Linux/amd64 e imagen target test ya construida. Evidencias
fuera de Git y ruta nueva por ejecución; no se consulta ni se copia la base activa:

```sh
python3 -X utf8 docker/acquisition/test_offline.py --image rj-profile-admin:test-20261026 --evidence /ruta/absoluta/nueva
```

Primero ejecutar pruebas focalizadas (nombre temporal exclusivo; política con ruta absoluta):

```sh
docker run --rm --pull never --name nombre-unit-exclusivo --init --network none --cap-drop ALL --security-opt no-new-privileges:true --security-opt "seccomp=$(pwd)/docker/acquisition/seccomp-linux-amd64.json" --entrypoint python rj-profile-admin:test-20261026 -B -m unittest discover -s /opt/checks -p 'test_*.py' -v
```

Resultados propios de esta entrega: 31 pruebas focalizadas Linux correctas; aceptación
final 23 casos correctos, con dos competidores adicionales rechazados. Comprende cookies
originales tras SIGKILL/hostname distinto; rechazo sin una/ambas confirmaciones y aunque
se confirmen con propietario activo; SIGTERM, sandbox por chrome://sandbox y /proc,
fallos de copia, verificación y fsync; falta de espacio estimada e inyectada, ENOSPC REAL
al copiar en tmpfs privado de 1 MiB; caída por SIGKILL antes/después de publicar puntero,
bloqueo de arranque y recuperación manual posterior conservando cookies. Fuente y
respaldos se comprueban por manifiesto; no se ensaya sobrescribirlos. Fallo de lanzamiento
prueba un único intento y ninguna generación o perfil alternativo.

También verificados el Compose standalone, verify→recover→verify con imagen runtime
sin instrumentos de prueba, separación de targets y ayudas de CLI. No se repite suite
de API: no se modifican rutas, persistencia deportiva ni importaciones de esa API.
Los fallos de desarrollo del runner/encodings/detección de títulos de procesos se
corrigieron y la aceptación final se ejecutó íntegra con las fuentes definitivas.

Pendiente: servidor del grupo (kernel, LSM, namespaces, almacenamiento y permisos),
otras arquitecturas, prueba de corte eléctrico/corrupción física y retención. Windows
nativo no está soportado por esta implementación de flock; se usa Linux/Docker. Se
comprobó SIGTERM de una sesión administrada con handler cooperativo del consumidor de
prueba; un SIGTERM abrupto durante mantenimiento deja la marca pendiente. No hay daemon
ni handler de servicio implementado. Estado ausente/corrupto o inicialización interrumpida
sin puntero utilizable se rechazan para revisión administrativa; no se inventa un perfil
vacío ni se elige automáticamente un backup. No hay adopción de perfiles ajenos.

El bloqueo es cooperativo: no impide acceso por programas que lo ignoren. La afirmación
administrativa debe abarcar esos procesos/hosts. No extrapolar a NFS/SMB ni usar como
protección ante administradores maliciosos. Perfiles/copias contienen material sensible;
no incluirlos en Git, imágenes, logs públicos o capturas.

## Evidencia de la versión final

Resumen verificable: docker/acquisition/validation-summary.json. Logs/inspect de los
23 casos, log real de 31 pruebas focalizadas y hashes de fuentes del runtime quedan
fuera de Git en rj-analyst/artifacts/profile-management-2026-10-26/. Se contrastaron
32 PID capturados de sesiones con señales contra /proc del host: todos ausentes.
Las fuentes de acquisition/*.py se compararon byte a byte por SHA256 con la imagen
runtime final. No se ampliaron las cinco reglas; los diagnósticos previos se conservan.

Imágenes locales de esta validación:
- rj-profile-admin:runtime-20261026: sha256:0f2cf2d573fb78cb62a0429365172d169af4ee495c5e7565169c2579cf302073.
- rj-profile-admin:offline-20261026 (target test): sha256:4e00beffaa2aaf3e6f7a1b30e9088461218159e0d7800d7280b7588dff50508c.

Volumen final exclusivo conservado: rj-profile-impl-90e08c672a-profiles. Las pruebas
crean siempre otro volumen nuevo con etiqueta propia; no reutilizan este por defecto.
Se retiraron solo contenedores propios terminados, sin podar Docker ni tocar servicios
existentes. No se activó ninguna instalación habitual ni se hizo commit o push.

Nota de portabilidad: el perfil se normalizó a LF antes de consolidar la entrega.
El SHA anterior e340dc1b775e61332d5f9dcf63c6bb2a2f614a02fda2555fad363b2bd4d6fe1b correspondía a CRLF; el objeto JSON y sus cinco reglas son idénticos. El SHA LF documentado funciona tras checkout Linux.
