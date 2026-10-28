# Diagnostico de sandbox: userns probado y siguiente delta clone sin aplicar

> Diseño final vigente: [perfil-adquisicion-diseno-final.md](perfil-adquisicion-diseno-final.md). Recuperación administrativa manual, sin JSON de autorización ni socket Docker. Los mecanismos de confirmación del lanzador descritos aquí son históricos del diagnóstico, no el diseño de producción.

Investigacion local del 26-10-2026. No hay peticiones externas, integracion del scraper,
endpoints ni cambios en servicios, datos activos, Docker global o kernel. Todos los procesos
nuevos son sondas temporales propias con --network none. No se ejecuto Chromium sin sandbox,
no se uso privileged/SYS_ADMIN ni se desactivo seccomp o no-new-privileges.

## Configuracion efectiva, no solo parametros declarados

Motor local: Docker Desktop 4.76.0, Engine 29.5.2, runc 1.3.5; Linux amd64,
kernel 6.6.114.1-microsoft-standard-WSL2. docker info informa seccomp profile=builtin.
Imagen candidata: rj-scraper-package-efcadb386d:candidate, revision de scraper fijada en
611e436a1528b0f487aa56a045f023e8ae8ae098. No se reconstruyo ni cambio la imagen al diagnosticar.

Se creo una sonda equivalente en usuario/restricciones a la prueba fallida, sin volumen de
datos deportivos. Se leyo su docker inspect, /proc y SU especificacion OCI en el runtime
activo de Docker Desktop. El acceso al host WSL fue de lectura y sobre la distribucion ya
arrancada. No se monto el socket Docker ni se leyeron configuraciones de otras aplicaciones.

| Elemento | Observacion |
|---|---|
| Usuario efectivo | UID/GID 10001; los cuatro Uid/Gid de /proc coinciden |
| Capabilities | Inh/Prm/Eff/Bnd a cero; OCI sin capabilities; privileged=false |
| no-new-privileges | true en Docker/OCI; NoNewPrivs=1 en proceso |
| Seccomp | Activo, Seccomp=2 y dos filtros; perfil OCI propio extraido y analizado |
| User namespace exterior | userns-remap no seleccionado; mapa UID 0->0 de rango completo; namespace inicial visible |
| Cuota de user namespaces | max_user_namespaces=63387, no cero; no demuestra por si sola que se permita crearlos |
| AppArmor | Docker/OCI sin perfil asignado y no aparece en LSM activos del host |
| LSM del host | capability,landlock,yama,safesetid,selinux; la lista no demuestra que SELinux este imponiendo una regla concreta |
| Sysctls de distribucion | unprivileged_userns_clone y apparmor_restrict_unprivileged_userns no existen en este kernel; no se cambiaron |
| Sandbox SUID | chrome_sandbox propiedad root:root, modo 0755; NO tiene bit setuid |
| Lanzamiento de la prueba | chromium_sandbox=True, TLS no ignorado; sin flags que desactiven sandbox; bibliotecas/binario cargan, Chromium llega a ejecutarse |
| Almacenamiento y shm | Perfil escribible/persistente ya comprobado; shm=512 MiB; no hay evidencia de fallo por permisos o memoria |

El dockerd del entorno tiene a su vez Seccomp=2 y un filtro. La sonda tiene dos: ademas de
su filtro OCI puede heredar restricciones. No se extrajo ni cambio el BPF del filtro ancestro;
no afirmar que permitir una syscall en el filtro hijo eliminara cualquier bloqueo heredado.

## Causa demostrada

El perfil OCI efectivo de la sonda usa defaultAction=SCMP_ACT_ERRNO y defaultErrnoRet=1 (EPERM).
No contiene regla de permiso para unshare. La unica regla clone relevante es:

```json
{
  "names": ["clone"],
  "action": "SCMP_ACT_ALLOW",
  "args": [{"index": 0, "value": 2114060288, "op": "SCMP_CMP_MASKED_EQ"}]
}
```

La mascara es 0x7e020000 y el valor esperado omitido es cero. Incluye el bit CLONE_NEWUSER
(0x10000000): clone con ese bit no satisface el permiso y cae en EPERM. clone3 tiene una
regla expresa que devuelve errno 38 (ENOSYS); eso NO prueba que el kernel carezca de clone3.

Sondas en procesos hijos desechables, con las MISMAS restricciones:

| Llamada | Resultado |
|---|---|
| unshare(0) | EPERM |
| unshare(CLONE_FS), 0x200 | EPERM |
| unshare(CLONE_NEWUSER), 0x10000000 | EPERM |

Por tanto, el filtro OCI actual es por si solo un impedimento verificable para la via de
user namespaces. No es solo una hipotesis basada en el mensaje de Chromium. La via SUID
alternativa tampoco esta preparada: helper sin setuid y NNP impediria obtener privilegios
mediante exec. No se propone arreglarla con chmod setuid o quitando NNP.

El mensaje de Chromium sobre restricciones AppArmor de Ubuntu es una sugerencia generica,
no el diagnostico de este host: AppArmor no aparece en sus LSM activos. Tampoco hay evidencia
para atribuir el fallo a datos del perfil, SofaScore, TLS, Python o dependencias SQL.

## Lo que sigue sin estar demostrado

- Resuelto para unshare(CLONE_NEWUSER): la prueba autorizada posterior pasa bajo las mismas
  restricciones. Esto no acredita otras combinaciones ni las llamadas de Chromium.
- Resuelta la primera llamada restante: se observo clone(CLONE_NEWUSER | SIGCHLD) con EPERM.
  Las llamadas posteriores que podria requerir un sandbox funcional siguen sin validarse.
- Que una sesion funcional cargue HTML/JSON local, conserve cookies y cierre limpiamente con
  SIGTERM. El fallo previo impidio esas fases; el cierre tras arranque fallido si quedo sin hijos.
- La politica del servidor del grupo: esta maquina no lo representa necesariamente.

## Cambio exacto aprobado y probado solo en diagnostico

Mantener UID 10001, cap_drop ALL, no-new-privileges=true, init, sandbox Chromium activo,
seccomp activo y red none. No tocar el backend ni una politica global. Derivar una copia del
perfil efectivo de LA SONDA y agregar SOLO esta regla, sin reemplazar las restantes:

```json
{
  "names": ["unshare"],
  "action": "SCMP_ACT_ALLOW",
  "args": [{"index": 0, "value": 268435456, "op": "SCMP_CMP_EQ"}]
}
```

Esto permite exclusivamente unshare(CLONE_NEWUSER). unshare(0), CLONE_FS, combinaciones con
otros bits, clone con flags de namespace, clone3 y setns conservan sus restricciones previas.
No es la receta amplia «permitir clone, unshare y setns» sin argumentos. Es el primer experimento
minimo para distinguir el filtro propio de los bloqueos restantes, NO una promesa de arranque
completo de Chromium. Docker/runc admiten un perfil seccomp por contenedor; el mecanismo
propuesto usa ese perfil concreto, nunca seccomp=unconfined.

Generador de propuesta: docker/scraper/propose_userns_policy.py BASELINE_JSON NEW_PROPOSAL_JSON.
Solo transforma JSON, no llama a Docker ni aplica nada. Rechaza destino existente, baselines
sin denegacion por defecto o con reglas unshare que requieran otro analisis. Se comprobo que
el diff semantico es exactamente una regla y que todas las demas reglas/arquitecturas/acciones
quedan iguales. Tras la aprobacion, el motor acepto el perfil y se ejecuto solo en sondas.
Baseline, perfil, delta, manifiesto y resultados resumidos estan en docker/scraper/policies/;
no dependen de la carpeta temporal de la investigacion inicial.

Evidencia local rj-sandbox-18c2f4213c en el directorio temporal del anfitrion:

- seccomp-effective.json: SHA256 7f2cbfb3a0ca22c4f77634c0576b1547e15ef8e04c5058891586b1fef4ea8671.
- seccomp-userns-diagnostic.proposed.json: SHA256 fa328590879c20500f0f763f20f59e134d2d5ac0991f4a0aa0ab1b7af045e68c.
- probe.json y container.json: sondas y configuracion original, sin datos deportivos.

**APLICADO SOLO EN SONDAS AUTORIZADAS, NO EN PRODUCCION.** La aprobacion posterior permitio
probar primero unshare(CLONE_NEWUSER) y, al pasar, Chromium con el mismo perfil. El navegador
volvio a fallar antes de cargar contenido local. Se detuvo ahi, sin modificar otra regla ni
probar alternativas de permisos. No usar el perfil de Desktop como baseline universal del servidor.

Implicacion de seguridad: los procesos del contenedor podrian crear user namespaces propios,
aumentando superficie del kernel. No concede capacidades en el namespace del host, pero tampoco
es una modificacion sin riesgo. Se mantiene el resto del filtrado y el aislamiento Docker.
El experimento no cambia NNP y no habilita la via SUID. No hay por ahora una configuracion de
Chromium funcional y minima demostrada con las restricciones actuales; ese es el limite real.

## Requisitos para el servidor del grupo

Antes de construir o integrar: comprobar arquitectura y disponibilidad del binario fijado;
UID/GID no root; directorio de perfil exclusivo y permisos; init, shm y limites de recursos;
version de Docker/runc y perfil seccomp RESUELTO para el conjunto efectivo de capabilities;
userns/rootless/remap y mapas UID/GID; CONFIG_USER_NS, cuotas y politica de user namespaces;
LSM activos y perfiles asignados (AppArmor/SELinux u otros), y filtros heredados del runtime/host.

No basta con leer un JSON de ejemplo, el sysctl de cuota o que el navegador funcione como root.
Repetir las sondas sin red y, solo tras aprobar la politica necesaria, cargar contenido local
con sandbox activo, comprobar chrome://sandbox, persistencia de cookies al recrear y cierre
normal/SIGTERM sin hijos. La aceptacion por SofaScore queda totalmente fuera de esta prueba.
No proponer cambios globales de sysctl, Docker o AppArmor sin un procedimiento independiente
aprobado por el administrador. No convertir rootless en una garantia: tambien puede filtrar
las syscalls necesarias o tener limites de user namespaces.

## Que aportaria separar adquisicion

No se ha encontrado conflicto pip que fuerce separar: SQL y scraper funcionaron juntos.
La justificacion seria aislar Chromium y cualquier politica de namespace especificamente
aprobada del proceso que posee SQLite, journal y copias deportivas. Un servicio/entorno
separado tendria UID no root, solo /acquisition RW, ningun montaje de /state ni socket Docker,
perfil de navegador exclusivo, politica seccomp propia revisada y limites de memoria/PID/shm.
Backend conservaria su perfil estricto, bloqueo y autoridad exclusiva de importar/publicar.

Si el host actual impide user namespaces en una capa ancestro, un segundo contenedor identico
no aporta el entorno diferente necesario: habria que elegir un runtime/host de navegador que
los permita con un perfil revisado, sin relajar globalmente el servidor de la base. No se ha
creado tal servicio ni una API de comunicacion. Separacion no significa privileged ni quitar
sandbox; es una frontera de permisos/datos y de politica de ejecucion. La decision sigue pendiente.

## Resultado del experimento aprobado (26-10-2026)

Se preservaron los hashes de los perfiles revisados antes de usarlos. Docker inspect de las
dos sondas confirma el perfil completo exacto, UID/GID 10001, cap_drop ALL, ningun cap_add,
privileged=false, no-new-privileges=true, init y network=none. La sonda de namespaces no tenia
montajes. El navegador solo monto un volumen NUEVO exclusivo en /acquisition; sin /state,
secretos, perfil habitual, codigo ni socket Docker. Docker inicializo el volumen desde el
directorio propiedad de UID 10001 de la imagen: no se uso un contenedor root para prepararlo.

| Prueba con la unica excepcion aprobada | Resultado |
|---|---|
| unshare(CLONE_NEWUSER), arg0=0x10000000 | Correcto |
| unshare(0) | EPERM, sigue denegado |
| unshare(CLONE_FS), arg0=0x200 | EPERM, sigue denegado |
| CapInh/Prm/Eff/Bnd del proceso de sonda | Cero; NoNewPrivs=1 y Seccomp=2 |
| Chromium con sandbox exigido | Sigue fallando: No usable sandbox / Chromium sandboxing failed |
| Contenido local | Ninguna peticion recibida; el fallo precede a la carga de la pagina |
| Recogida de hijos tras fallo | children_after_close vacio, children_closed=true |
| Cookies persistentes / sandbox de sesion funcional / SIGTERM funcional | No validados: secuencia detenida en el segundo bloqueo |

La prueba positiva demuestra que ESTE host permite al hijo crear un user namespace cuando
su seccomp propio autoriza esa llamada concreta. Los filtros ancestros no impidieron esa
llamada en este ensayo. No implica que permitan otras syscalls/combinaciones; los privilegios
internos de un user namespace no son capacidades concedidas en el namespace del host.

clone con bits de namespace y otras formas de unshare siguen restringidos por el perfil.
La investigacion posterior de la seccion siguiente traza el clone concreto que Chromium
intenta y relaciona su denegacion con la regla vigente; no acredita llamadas posteriores. El mensaje de Chromium solo
confirma que no encontro un sandbox utilizable. No se ha ampliado clone, clone3, setns,
unshare con otros argumentos, capacidades ni politicas del host para investigarlo.

Artefactos portables y revisables:

- docker/scraper/policies/docker29.5.2-cap0-amd64.baseline.json
- docker/scraper/policies/docker29.5.2-cap0-amd64.unshare-user.probe.json
- docker/scraper/policies/unshare-user.delta.json
- docker/scraper/policies/unshare-user.results.json y manifest.json

El baseline estable tiene solo normalizacion CRLF->LF respecto al original revisado, mismo
objeto JSON. Su hash LF es 86ebd1c8740202bd6c8317c40a6e5d7eb0e259ce81b0bfbb968815f65c28d619;
el manifiesto conserva tambien el hash original. El perfil aplicado no cambia de bytes:
fa328590879c20500f0f763f20f59e134d2d5ac0991f4a0aa0ab1b7af045e68c.
Comprobacion local sin Docker ni red: python3 docker/scraper/verify_userns_artifacts.py.
Los nombres amd64/Docker29.5.2 describen el baseline auditado, no una configuracion portable
para activar automaticamente en otros motores o arquitecturas. No hay referencias desde Compose.

Logs/inspect completos quedan fuera de Git en el directorio de evidencias de la prueba
rj-userns-approved-da2887a02c; la futura entrega usa los artefactos estables anteriores.
Las sondas se retiraron; queda conservado el volumen exclusivo
rj-userns-approved-da2887a02c-profile para inspeccion. Sin commit ni push.

## Propuesta de arquitectura a partir de los resultados

Conviene separar adquisicion en la futura integracion, sin implementarla ahora. La razon
concreta es mantener cualquier politica de namespace adicional especificamente revisada lejos
del proceso que posee SQLite, journal y copias; no un conflicto de paquetes Python.

El servicio de adquisicion tendria solo perfil/cache/temporales propios RW y una salida de
evidencia inmutable; backend podria leer esa salida RO e importar exclusivamente mediante
su coordinador. No montaria /state ni el volumen deportivo, ni el journal, copias o socket
Docker. Backend conservaria exactamente sus restricciones y autoridad de publicacion.
UID no root, capabilities a cero, NNP y sandbox seguirian siendo requisitos. La comunicacion
y autorizacion de trabajos se disenaran en otra entrega, sin shell arbitraria ni puertos
publicos por defecto; no se han creado endpoints ni servicios aqui.

La separacion por si sola NO permite arrancar Chromium: exige validar un entorno/politica
que soporte su sandbox y presentar antes cualquier nuevo permiso exacto. Contenedores del
mismo host comparten kernel, asi que tampoco elimina su superficie de ataque; para una
frontera de kernel haria falta otro entorno/VM expresamente aprobado. Mantener el navegador
fuera del backend evita ampliar tambien el acceso del proceso deportivo a namespaces.
El servidor del grupo sigue pendiente de validacion local sin egress, con SU perfil efectivo,
antes de decidir si basta un contenedor dedicado o se requiere otro runtime/entorno.

## Traza local del bloqueo restante (26-10-2026)

Se recogieron los mensajes completos y una traza de syscalls del Chromium fijo de la imagen,
sin descargar herramientas. strace no esta instalado; Python/libc proporcionaron ptrace.
Antes se comprobo PTRACE_TRACEME de un hijo propio: permitido con UID 10001, cap_drop ALL y
NNP, usando el MISMO perfil de la prueba userns aprobada. No se anadio SYS_PTRACE ni se
hicieron attaches a procesos ajenos. PTRACE_O_EXITKILL limita la vida de los hijos al trazador.

Instrumentos locales: docker/scraper/trace_chromium.py y trace_offline_browser.py. El primero
solo ejecuta el binario fijo /opt/ms-playwright/chromium-1243/chrome-linux64/chrome y registra
entradas/salidas con PTRACE_GET_SYSCALL_INFO en Linux x86_64; no es un ejecutor de comandos.
El segundo inyecta ese ejecutable unicamente en el proceso de prueba, manteniendo sandbox,
TLS y argumentos de seguridad. No se modifico el scraper upstream ni la imagen/servicios.
Otra arquitectura requiere revisar el instrumento, no fijar la arquitectura de produccion.

El contenedor fue nuevo, sin red exterior, sin /state/secretos/socket, con perfil de prueba
nuevo. Solo se aplico el perfil userns aprobado con SHA256 fa328590879c20500f0f763f20f59e134d2d5ac0991f4a0aa0ab1b7af045e68c.
El navegador sin trazador ya fallaba con No usable sandbox. Bajo trazado se observo:

```
clone(flags=0x10000011, ...) = -1 EPERM
# 0x10000000 = CLONE_NEWUSER; 0x11 = SIGCHLD (17, en este Linux x86_64)
```

El perfil deja clone permitido solo si (arg0 & 0x7e020000) == 0. Para esta llamada, el resultado
es 0x10000000, no cero, y ninguna otra regla vigente autoriza clone con esos flags. Por tanto
el filtro propio es un bloqueo concreto y suficiente para la llamada OBSERVADA; ya no se
infiere solo a partir de unshare o del texto generico de Chromium. El main Chromium aborta
con SIGABRT (codigo 134), y el wrapper acaba tras recoger los descendientes. El driver informa
No usable sandbox; no se recibieron peticiones en el servidor de contenido local.

Otras observaciones que NO deben confundirse con permisos nuevos necesarios:

- clone3 devuelve ENOSYS y aparecen clones de hilos normales que si tienen exito. Ese ENOSYS
  coincide con la regla explicita del perfil y activa fallback; no demuestra ausencia de
  soporte del kernel ni justifica permitir clone3.
- Crashpad intenta ptrace attach al Chromium ya trazado y recibe EPERM. Es compatible con
  que otro tracer ya lo posee; no se propone conceder SYS_PTRACE ni cambiar Yama por ello.
- prctl de opciones no soportadas devuelve EINVAL en la traza; no es evidencia de un permiso
  que haya que abrir para solventar el clone fallido.

Limites: ptrace puede cambiar el comportamiento del sandbox y del informe de fallos. La
llamada observada, su EPERM y su denegacion independiente en seccomp estan comprobados, y el
control sin traza tiene el mismo fatal. No se afirma que un arranque completo vaya a funcionar
por permitir solo esa llamada. Tampoco se ha probado si un filtro heredado o un requisito de
kernel/LSM denegaria el mismo clone aun con permiso en el filtro propio. userns mediante
unshare ya habia pasado; eso no acredita clone ni el resto del protocolo de sandbox.

Mensajes COMPLETOS, JSON de resultado, inspect y las 57 entradas de traza quedan fuera de Git
en rj-analyst/artifacts/sandbox-trace-2026-10-26/ (ignorado), ademas de la carpeta de evidencias
original de esta sonda. No se truncaron los archivos al mostrarlos por consola. Resumen/hash
revisable estable: docker/scraper/policies/clone-user-sigchld.trace-results.json. La traza
termina con evento complete y cierre de hijos; no hubo lectura de datos deportivos.

### Siguiente cambio minimo propuesto, NO aplicado

Partir del perfil userns ya aprobado, no de uno sin restricciones, y agregar solo:

```json
{
  "names": ["clone"],
  "action": "SCMP_ACT_ALLOW",
  "args": [{"index": 0, "value": 268435473, "op": "SCMP_CMP_EQ"}]
}
```

Es exactamente CLONE_NEWUSER | SIGCHLD para la llamada observada. No permite variantes con
NEWNET/NEWPID/mount, otros bits o senales de terminacion; no abre setns/clone3/unshare generico.
Conservar UID no root, capabilities cero, NNP, init, red none y sandbox activo. Primero probar
sin trazador para no confundir un fallo introducido por la instrumentacion. Ante otra barrera,
detenerse y revisar su evidencia; no ampliar toda la familia clone ni cambiar el host.

Artefactos nuevos, separados del perfil que si se probo:

- docker29.5.2-cap0-amd64.clone-user-sigchld.next.proposed.json
- clone-user-sigchld.next.delta.json
- clone-user-sigchld.next.manifest.json (status PROPOSED_NOT_APPLIED)

SHA256 propuesto: 403d1881298cd11b87db0fdf0e98d9c7df7cdd2c893aec91fb1d47106127477b.
El diff semantico se verifico como una sola regla mas; el perfil propuesto NO se uso en
ningun contenedor. Haria falta revisar/aprobar este permiso antes de probarlo. Autorizar
crear user namespaces sigue ampliando superficie del kernel aunque no de capacidades en el
namespace del host. El perfil sigue siendo especifico del motor/arquitectura auditados.

No se necesito ningun permiso adicional para identificar la primera llamada fallida. Si en
el servidor ptrace de un hijo propio estuviera denegado por su politica, habria que obtener
una traza del hijo con un instrumento local autorizado por su administrador, o un registro
de denegacion de seccomp del runtime. No se solicita SYS_PTRACE globalmente: su necesidad y
alcance se tendrian que justificar con la politica efectiva de ese entorno.

El diseno actualizado del servicio, sin integrar, esta en
rj-analyst/docs/propuesta-servicio-adquisicion.md: acquisition carece de montaje deportivo,
cache/perfil propios, entrega bundles por interfaz interna y backend conserva importacion,
validacion y publicacion. Ni la separacion ni el delta propuesto se han activado.

## Prueba aprobada de clone exacto (26-10-2026)

Esta seccion supera el estado NO aplicado de la propuesta anterior; los manifiestos
next.* se conservan como registro historico. El perfil aplicado esta en
`docker/scraper/policies/docker29.5.2-cap0-amd64.clone-user-sigchld.approved-probe.json`;
SHA256 403d1881298cd11b87db0fdf0e98d9c7df7cdd2c893aec91fb1d47106127477b.
`clone-user-sigchld.approved.from-initial.delta.json` contiene las DOS unicas reglas
anadidas al baseline: unshare arg0=0x10000000 y clone arg0=0x10000011, ambas EQ.
El inspect de Docker se contrasto semanticamente con ese perfil completo.

Un unico contenedor temporal: red none, UID/GID 10001, capabilities cero, NNP=1,
seccomp=2, init, ningun puerto y un unico volumen nuevo de perfil exclusivo en /acquisition.
Sin datos deportivos, journal, secretos, peticiones externas ni cambios globales.
Chromium se ejecuto PRIMERO sin ptrace y manteniendo chromium_sandbox=True. Fallo
con SIGTRAP antes de cualquier peticion al contenido local; cierre sin hijos restantes.
No se verificaron sandbox funcional, cookies ni terminacion SIGTERM de una sesion funcional.

Despues, en el MISMO contenedor y con los mismos permisos, la traza de hijo propio mostro:
- clone(0x10000011) devuelve PID 59: la excepcion aprobada tiene efecto.
- unshare(0x10000000) devuelve 0.
- clone(0x70000011) devuelve -1 EPERM: siguiente barrera observada.

0x70000011 = CLONE_NEWUSER | CLONE_NEWPID | CLONE_NEWNET | SIGCHLD. El perfil propio
lo deniega por su mascara inicial, y la nueva igualdad exacta no lo permite. Esto no
demuestra que otro filtro heredado, LSM o requisito de kernel lo permitiria. El ptrace
EPERM de Crashpad puede proceder de estar ya trazado; no justifica SYS_PTRACE.
La causa exacta del SIGTRAP sin traza no queda demostrada por esa correlacion.
No se ha creado ni aplicado otra excepcion. La investigacion se detiene en esta barrera.

ABI comprobado: maquina x86_64, ELF64 e_machine=62, traza nativa clone syscall 56
con flags en arg0, SIGCHLD=17. La igualdad es correcta para esta arquitectura;
no se valida para ABI de 32 bits ni otras arquitecturas (algunas cambian el orden).
No fijar plataforma en produccion para ocultar esta limitacion.

Resultados y hashes revisables: policies/clone-user-sigchld.approved.results.json.
Mensajes completos, traza, inspect y supervisor: rj-analyst/artifacts/
sandbox-clone-approved-2026-10-26/ (ignorado). Los perfiles y delta estables estan en
el repositorio y no dependen de esa carpeta para ser revisados. No hay integracion.

Servidor del grupo: revisar arquitectura/ABI, version y perfil efectivo del runtime,
filtros heredados, user namespaces/cuotas, kernel/LSM y permisos de cache/perfil.
Mantener NNP, capabilities cero y sandbox; validar contenido local, chrome://sandbox,
cookies y cierre de hijos antes de habilitar adquisicion. Cualquier ampliacion para
namespaces de PID/red requiere una nueva revision explicita. Separar adquisicion
limita el acceso a datos y permite una politica propia, pero NO elimina esta barrera
si se ejecuta con el mismo runtime y filtros. El backend debe conservar sus restricciones.

## Configuración completa aprobada: resultado del 26-10-2026

Esta prueba posterior supera el estado «no aplicado» de la propuesta documental.
Perfil completo: docker/scraper/policies/docker29.5.2-cap0-amd64.full-candidate.approved-probe.json.
SHA256: e340dc1b775e61332d5f9dcf63c6bb2a2f614a02fda2555fad363b2bd4d6fe1b.
Delta: full-candidate.from-initial.delta.json. Exactamente cinco reglas añadidas al
baseline completo: unshare EQ 0x10000000; clone EQ 0x10000011, 0x70000011 y
0x20000011; chroot. Sin otros cambios, setns genérico, clone3 ni capabilities nuevas.
El perfil enviado a Docker se contrastó por igualdad JSON con el artefacto completo.

Solo recursos diagnósticos nuevos con red none, UID/GID 10001, cap_drop ALL,
no-new-privileges, init, IPC privado de 512 MiB y un único volumen propio /acquisition.
Sin datos deportivos, journal, secretos o montajes habituales. Imagen local existente,
--pull never. No se ejecutó trazador. La sonda endurece la fábrica de sesión SOLO en
el proceso de prueba; el scraper instalado no se modificó ni quedó integrado.

Resultados propios:
- Primer arranque sin trazado: contenido HTML y JSON local, cierre normal sin hijos.
- chrome://sandbox: Layer 1 Namespace, PID/Network namespaces Yes, Seccomp-BPF Yes,
  TSYNC Yes. Ptrace Protection Non-broker No se conserva en el informe; no se afirma
  que cada mecanismo disponible esté activo.
- Contraste /proc: navegador NSpid de profundidad 1 y dos filtros seccomp; renderers
  profundidad 3 y tres filtros, NNP=1 y CapEff=0. Algunas lecturas de enlaces ns de
  procesos protegidos devuelven EACCES; no se ampliaron permisos para leerlos.
- Capabilities del contenedor y proceso navegador a cero. Un zygote intermedio tiene
  CAP_SYS_ADMIN LOCAL al user namespace antes de retirarla; es parte del mecanismo
  del sandbox, no una capability añadida al contenedor ni autoridad sobre el host.
- Cookie persistente encontrada entre sesiones/recreaciones normales (contenedores
  distintos, mismo volumen exclusivo); marcador de almacenamiento también conservado.
- SIGTERM mediante docker stop: salida 0, cookie previa encontrada, cierre sin hijos.
- SIGKILL: salida 137, contenedor sin PID en el runtime. Se contrastaron los 16 PID
  capturados antes de matar contra /proc del host Docker Desktop: todos ausentes.
- Recuperación posterior: FALLIDA para el perfil habitual de la prueba. El navegador
  sí arrancó y sirvió contenido local, pero no encontró la cookie esperada. Se detuvo
  la secuencia; esa ejecución no llegó a las comprobaciones posteriores del sandbox.

Causa concreta de esta última barrera: bridge/profile_lock.py interpreta SingletonLock
con hostname anterior como perfil ocupado. El log registra el hostname antiguo y la
selección de /acquisition/profile-6, perfil temporal vacío. profile_owner solo considera
obsoleto un PID muerto cuando el hostname coincide. No demuestra pérdida de la cookie
original ni corrupción. No se borraron locks ni se cambió hostname o lógica del scraper.
Siguiente propuesta para revisión: política de recuperación de un perfil de propietario
único, con exclusión real antes de comprobar obsolescencia y sin fallback silencioso a
perfil vacío. Un hostname estable puede evitar este caso, pero no resuelve por sí solo
PID reutilizado ni aporta exclusión; no se ha probado ni adoptado como solución.

Reproducción (Python 3.10+, Docker Linux/amd64 y la imagen local preparada según
scraper-empaquetado-linux.md; nueva ruta absoluta de evidencias en cada ejecución):

    python3 docker/scraper/verify_userns_artifacts.py
    python3 docker/scraper/test_full_sandbox.py --image IMAGEN_LOCAL --evidence /ruta/nueva/fuera-del-repositorio

El runner crea nombres/volumen únicos, no descarga imágenes, comprueba el perfil
completo y no reutiliza almacenamiento anterior. Se detiene al primer fallo y conserva
su recurso/evidencias; no elimina volúmenes automáticamente. La comprobación WSL de PID
es evidencia adicional de esta máquina y no requisito del runner portable. Docker
inspect/top también confirman que el grupo ya no está ejecutándose. La recuperación
que actualmente falla se mantiene como prueba, no se omite. El manejo del informe de
fallo del runner se mejoró después de ejecutar; no se volvió a lanzar el navegador.

Resumen/hash estable: policies/full-candidate.results.json. Logs completos, inspect,
/proc, señales, fuentes ejecutadas y resultados: rj-analyst/artifacts/sandbox-full-2026-10-26/
(ignorado). Perfiles de navegador, cookies y SQLite de prueba permanecen solo en volúmenes
exclusivos etiquetados, nunca en Git. Se eliminaron únicamente contenedores diagnósticos
propios ya terminados; volúmenes retenidos para revisión, sin limpieza global.

Esto valida el sandbox en Docker Desktop Linux x86_64, NO la recuperación completa ni
el servidor del grupo. Allí siguen pendientes ABI, kernel/namespaces/cuotas, UID/GID maps,
LSM, filtros heredados, runtime/rootless/remap, permisos y límites de recursos. No se ha
modificado Compose ni habilitado adquisición o red externa. Sin commit ni push.

### Recuperación de perfil: prueba posterior

La barrera de SingletonLock se ha resuelto en un ADAPTADOR DIAGNÓSTICO, sin modificar
el scraper instalado: ver docs/historico/recuperacion-perfil-linux.md y
policies/profile-recovery.results.json. Propiedad flock durante toda la sesión;
sesión incompleta exige confirmación del lanzador de confianza de que terminó el
contenedor anterior. Copia verificada preservada, limpieza solo en candidata y
fallback vacío prohibido. Cookies recuperadas con hostname distinto; concurrencia,
SIGTERM y fallo controlado con copia/fuente intactas verificados. No es recuperación
automática integrada ni sustituye comprobar el almacenamiento/host del servidor.
Los resultados previos de fallo se conservan como históricos.
