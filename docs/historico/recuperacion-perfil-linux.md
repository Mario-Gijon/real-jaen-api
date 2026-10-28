# Recuperación del perfil Linux: diseño y prueba aislada

> Diseño final vigente: [perfil-adquisicion-diseno-final.md](../perfil-adquisicion-diseno-final.md). Recuperación administrativa manual, sin JSON de autorización ni socket Docker. Los mecanismos de confirmación del lanzador descritos aquí son históricos del diagnóstico, no el diseño de producción.

Especificación presentada antes de preparar el adaptador diagnóstico (26-10-2026).
No modifica el checkout del scraper, su package-lock ajeno ni servicios. No integra
adquisición. Código exclusivamente diagnóstico: `docker/scraper/profile_owner_probe.py`
y `test_profile_recovery.py`. Usa la imagen local y las cinco reglas seccomp ya
aprobadas; no concede nuevos permisos ni consulta recursos externos.

## Problema comprobado y mecanismo revisado

[Chromium 153.0.8010.12, ProcessSingleton POSIX](https://raw.githubusercontent.com/chromium/chromium/153.0.8010.12/chrome/browser/process_singleton_posix.cc)
coordina instancias con SingletonLock (enlace hostname-PID), SingletonSocket y
SingletonCookie. El socket vive en un directorio temporal; los cookies del singleton
vinculan ese socket con el perfil. No confundir SingletonCookie con las cookies HTTP.
Tras una caída puede desaparecer el socket y quedar los enlaces. La implementación
trata otro hostname conservadoramente como posible propietario remoto.

El scraper fijado a 611e436a1528b0f487aa56a045f023e8ae8ae098, en
`client/profile_lock.py`, solo considera obsoleto el lock por PID muerto si coincide
hostname. `BrowserBridge` abre un perfil secundario si detecta propietario o error de
singleton. Eso explica la cookie ausente tras la recreación anterior: se abrió otro
perfil; no se demostró pérdida de la cookie original. No se cambia ese código aquí.

## Propiedad y condición para recuperar

El directorio persistente es exclusivo de esta instalación diagnóstica. Un archivo
permanente `/acquisition/profile-control/owner.lock`, fuera de las generaciones del
perfil, se abre sin seguir symlinks y se protege con flock EX|NB. Su inode nunca se
borra. La sesión mantiene el descriptor hasta cerrar navegador e hijos, sincronizar
y confirmar su estado. Un competidor recibe `profile_busy` antes de copiar o lanzar.
El bloqueo lo libera el kernel al morir el propietario. Todas las entradas deben
pasar por el mismo propietario; no coordina programas externos que lo omitan.

Un registro durable diferencia generación confirmada y sesión incompleta (nonce,
contexto del contenedor, candidata y copia preservada). Ni flock libre ni PID/hostname
bastan para recuperar una sesión incompleta: el navegador podría sobrevivir a la muerte
del proceso propietario. Sin prueba adicional se devuelve `recovery_required`.

En esta prueba, el lanzador de confianza del HOST verifica con Docker el contenedor
anterior exacto, etiqueta exclusiva, volumen, estado exited y PID 0. Entonces entrega
al siguiente contenedor una confirmación vinculada al nonce y propietario anteriores.
Esta comprobación externa elimina también el árbol anterior, no solo un PID aparente.
El nuevo propietario exige además adquirir flock. Un nonce incorrecto se rechaza.
No se usa hostname/PID como prueba de obsolescencia ni se mata un PID tomado del lock.

**La confirmación JSON es una afirmación del lanzador autorizado, no un certificado
que se autentique por sí mismo.** Su seguridad depende de que solo ese operador pueda
suministrarla. El adaptador no consulta Docker, no monta su socket, no acepta peticiones
públicas ni ejecuta órdenes suministradas. No es una solución automática integrada.
En el servicio futuro habrá que concretar quién aporta esa prueba al reiniciar; sin
ella se bloquea la recuperación y se requiere intervención. Tampoco se permite adoptar
silenciosamente un perfil no vacío sin historial de propietario o limpiar un singleton
inesperado en un estado supuestamente limpio.

## Copia, recuperación y confirmación

Con flock adquirido y, si procede, contenedor anterior cercado:

1. Leer el perfil fuente completo, sin seguir sus enlaces. Rechazar archivos especiales
   inesperados. Comprobar espacio para dos copias más margen de 32 MiB; no es una reserva
   de disco ni garantiza que otros procesos no lo consuman después.
2. Copiar a `profile-backups/<uuid>`, preservando enlaces y metadatos mediante copytree.
   Comparar manifiesto de contenido SHA256, tamaños, directorios y destinos de enlaces
   entre fuente/copia y volver a comprobar fuente. Fsync de archivos/directorios y
   manifiesto. Si falla, no abrir Chromium ni modificar fuente.
3. Crear y verificar una candidata en `profile-generations/<uuid>` desde esa copia.
   Registrar durablemente la sesión incompleta antes de modificar la candidata.
4. Retirar SOLO los enlaces de primer nivel SingletonLock, SingletonSocket y
   SingletonCookie de la candidata. No seguir ni borrar sus destinos en /tmp; rechazar
   un singleton de tipo inesperado. No tocar cookies HTTP, WAL, journals o bases internas.
   Fuente y copia permanecen intactas incluso si falla esta etapa.
5. Sincronizar candidata y lanzar Chromium protegido sobre esa ruta. El adaptador de
   prueba rechaza explícitamente el fallback secundario de BrowserBridge: si Chromium
   no la acepta, devolver recuperación necesaria, sin perfil vacío alternativo.
6. Solo tras resultado correcto, cierre de todos los hijos y fsync, confirmar por
   reemplazo atómico y fsync el nuevo estado activo limpio. Mantener flock hasta terminar.
   Si falla apertura, cierre o publicación del estado, conservar el registro incompleto
   y las copias. No reintentar automáticamente ni declarar limpia una sesión fallida.

La candidata es la nueva generación del perfil; no se sobreescribe la fuente ni se
restaura una copia por encima de ella. Se conservan generaciones/copia/manifest para
revisión. Este prototipo no implementa retención ni limpieza automática. Copiar todo
el perfil conserva también los archivos SQLite/WAL internos del navegador bajo
exclusión, sin intentar repararlos ni convertir la copia de un perfil caído en prueba
de integridad lógica. La apertura y las cookies verifican el caso local ejercitado.

## Prueba reproducible y límites

Requisitos: Python del host 3.11+ (hashlib.file_digest en el adaptador requiere 3.11;
la imagen probada usa 3.14.4), Docker Linux/amd64, imagen candidata local previamente
construida según scraper-empaquetado-linux.md y suficiente disco. No descarga imágenes.
Nueva carpeta de evidencias fuera de Git en cada ejecución:

    python3 docker/scraper/test_profile_recovery.py --image IMAGEN_LOCAL --evidence /ruta/absoluta/nueva

El runner crea nombres y volumen nuevos, verifica la política exacta por SHA256 e
inspect, red none, UID 10001, capabilities cero y NNP. Solo usa contenido loopback;
comprueba sandbox con chrome://sandbox y /proc. Prueba cookie original con nombre Y
valor; la comprobación ocurre antes de escribir la cookie en esa sesión.

Casos: siembra/cierre normal; sesión con dos competidores rechazados; SIGKILL y cambio
de hostname; rechazo sin confirmación; rechazo de confirmación incorrecta; recuperación
con cookies originales; SIGTERM; nueva caída y fallo inyectado después de limpiar SOLO
la candidata. En este último caso compara hashes de fuente y copia conservada, exige
`recovery_required` y no abre navegador. No continúa a un perfil vacío ni borra locks
del original. Los inspect finales exigen exited/PID 0; cierres normales comprueban hijos.

La prueba no cambia la configuración de Chromium; usa las cinco reglas aprobadas con
SHA256 e340dc1b775e61332d5f9dcf63c6bb2a2f614a02fda2555fad363b2bd4d6fe1b.
Los contenedores terminados propios se retiran al completar; el volumen exclusivo
queda retenido para revisión. Ante un fallo inesperado el runner se detiene y conserva
recursos/evidencias. No elimina volúmenes ajenos ni ejecuta limpiezas globales.

Validación limitada a Docker Desktop Linux x86_64 y el filesystem del volumen Docker
local. Pendientes en el servidor: semántica real de flock entre contenedores, kernel,
seccomp/LSM, UID/GID, fsync y permisos del almacenamiento elegido. No extrapolar a NFS,
SMB o volúmenes compartidos entre hosts. No se han simulado corte eléctrico, corrupción
física, ENOSPC real ni todas las fronteras de escritura: las copias preservadas permiten
parar para revisión; el prototipo no promete autorreparación de esos casos.

Se ha validado la estrategia mediante un adaptador en proceso de prueba. Antes de
modificar el scraper se debe revisar esta entrega, la frontera de confianza de la
confirmación de contenedor cercado y la política de adopción/retención. No activar
servicios ni automatizar recuperaciones a partir de un archivo de metadatos sin esa
frontera de confianza.

## Resultados propios, revisión final

La ejecución final pasó los ocho escenarios: seed, crash, unfenced, wrong-fence,
recovered-other-host, sigterm, crash-again y recovery-failure. Dos competidores
simultáneos recibieron profile_busy. Los dos rechazos de recuperación conservaron
el mismo estado incompleto, sin iniciar navegador. La recuperación autorizada con
hostname distinto encontró la cookie original (nombre y valor), confirmó sandbox y
cerró sus hijos. El fallo inyectado devolvió recovery_required y verificó por
manifiesto que fuente y respaldo permanecían intactos. Se comprobaron 48 PID de
las sesiones con señales contra /proc del host Docker Desktop: todos ausentes.

Resumen y hashes: docker/scraper/policies/profile-recovery.results.json.
Evidencia completa ignorada: rj-analyst/artifacts/profile-recovery-2026-10-26/.
Volumen final propio retenido: rj-profile-recover-3a7eb80b07-only. La ejecución
preliminar también pasó sus casos y dejó su volumen exclusivo para revisión.
No se incluyen perfiles, cookies o bases en Git. Sintaxis Python y diff --check
verificados. No se repitió la suite deportiva: no se cambió código de la API.
