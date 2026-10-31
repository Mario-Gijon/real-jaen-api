# Perfil de adquisición: diseño final con recuperación administrativa manual

> Estado 29-10-2026: implementado e integrado en el servicio de adquisición. Perfil seccomp aplicado:
> `docker/acquisition/seccomp-linux-amd64.json`. Estado general: [adquisicion-sofascore.md](adquisicion-sofascore.md).

> Nota de vigencia: este documento conserva la especificación histórica del diseño del perfil. La integración operativa vigente está descrita en [adquisicion-sofascore.md](adquisicion-sofascore.md) y [dashboard/docs/docker.md](../../dashboard/docs/docker.md); el overlay actual reside en `app-real-jaen/compose.acquisition.yaml`. Los comandos y nombres de perfil de la propuesta de abajo no son un runbook para la instalación actual.

Decisión aprobada el 26-10-2026. Especificación, NO implementación ni integración.
Este documento es normativo para el servicio futuro y sustituye la propuesta de
confirmación de contenedor cercado mediante JSON de los diagnósticos anteriores.
Los resultados y scripts diagnósticos se conservan intactos como evidencia histórica;
no se empaquetarán como entrada del servicio ni como comando administrativo.

## Arranque y propietario único

El servicio acquisition tendrá almacenamiento propio, sin volumen deportivo, journal,
copias o credenciales del backend. Un archivo de coordinación permanente, fuera de las
generaciones del perfil, se protege con flock exclusivo no bloqueante. Misma ruta
canónica y mismo inode para servicio y administración; no se borra al liberar el bloqueo.
Se mantienen UID sin privilegios, capabilities cero, NNP, init y las cinco reglas
seccomp aprobadas. Sin socket Docker, órdenes arbitrarias o permisos globales nuevos.

En arranque normal se adquiere flock y se valida el estado durable del perfil. Si está
limpio, la generación activa existe y no hay singleton inesperado, el servicio queda
listo automáticamente. No comienza trabajos ni consulta al proveedor al arrancar. La
sesión de navegador usa exclusivamente esa generación bajo el mismo propietario. El
bloqueo se conserva durante la vida del servicio, incluidos cierre del navegador/hijos
y confirmación durable de cierre limpio; administración y otro servicio no pueden entrar.
Un perfil inicialmente ausente solo puede inicializarse mediante una operación explícita;
no se sustituye un perfil existente, perdido o ilegible por uno vacío. La CLI futura
`initialize-profile --confirm-new-profile` solo podrá inicializar un destino realmente
nuevo, sin estado previo ni contenido; nunca recuperar o resetear uno ya administrado.

Si flock está ocupado, no se usa el perfil. Si hay sesión incompleta, singleton dudoso,
estado incoherente o perfil que no puede acreditarse limpio, acquisition queda bloqueado
para trabajo y no preparado. El control interno puede seguir disponible para informar
`profile_recovery_required`: **«perfil pendiente de recuperación»**. No se lanza navegador,
no se reintenta la limpieza, no se descarga y no se continúa con perfil temporal vacío.
No provocar un bucle de reinicios para esta condición; readiness negativa y estado
persistente explican el bloqueo. Una sesión incompleta exige recuperación incluso si
no quedan enlaces Singleton. Hostname, PID, tiempo transcurrido o flock libre no la autorizan.

La marca de sesión incompleta se sincroniza antes de abrir el navegador. Solo el cierre
comprobado del navegador y sus hijos, seguido de fsync y confirmación atómica, permite
marcar la sesión limpia. Tras SIGKILL o cierre ambiguo se mantiene incompleta. Si falla
la escritura del estado, no se abre navegador ni se declara limpio el perfil.

## Recuperación administrativa explícita

Única vía prevista: `python -m acquisition.admin recover-profile`. No será un endpoint,
una acción automática del frontend, un flag permanente del servicio ni una variable
de entorno que autorice recuperaciones. Tampoco aceptará `--fence` o un JSON como prueba.

Precondiciones del ADMINISTRADOR:

- Detener acquisition y cualquier contenedor administrativo anterior.
- Detener o excluir cualquier otro programa, contenedor o host con acceso al perfil.
- Confirmar explícitamente ambas condiciones en esa invocación. Los flags propuestos
  `--confirm-service-stopped --confirm-no-external-users` son una declaración de
  responsabilidad del operador, NO una comprobación técnica ni permiso reutilizable.
- Usar el proyecto/entorno y volumen correctos. Mantener el servicio detenido hasta
  concluir con éxito. No hacer limpieza de archivos manual ni forzar flock.

El comando no llama al motor Docker para deducir estas condiciones. Si el operador no
puede acreditarlas operacionalmente, no debe confirmar: se conserva el estado pendiente.
Un contenedor parado no demuestra por sí solo ausencia de consumidores externos. Incluso
con confirmación, flock ocupado rechaza la operación sin modificar el perfil.

Secuencia bajo flock exclusivo, mantenido hasta terminar:

1. Validar rutas, estado, tipos de archivo y espacio para respaldo completo y candidata,
   con margen. Rechazar perfiles no administrados sin procedimiento explícito de adopción.
2. Copiar íntegramente la generación afectada, incluidos archivos internos SQLite y sus
   diarios, sin seguir enlaces. Verificar hashes, tamaños y destinos de enlaces contra
   fuente; fsync de copia y manifiesto antes de modificar nada. Un error detiene el proceso.
3. Crear candidata distinta desde el respaldo verificado. Registrar durablemente la
   preparación y las rutas, dejando vigente la generación anterior. No modificar fuente
   ni respaldo. Si falta disco o falla una copia, no arrancar el navegador.
4. En la candidata, retirar solo los enlaces SingletonLock, SingletonSocket y
   SingletonCookie de primer nivel. No seguir destinos externos ni borrar sockets en /tmp.
   Rechazar tipos inesperados; no limpiar cookies HTTP, WAL/SHM o bases del perfil.
5. Abrir y cerrar Chromium sobre la candidata con sandbox, SIN RED. Comprobar que usa esa
   ruta, sandbox activo y cierre de hijos. Verificar estructura y archivos necesarios sin
   publicar secretos o cookies en logs. No consultar SofaScore ni considerar esta apertura
   una validación de que la sesión del proveedor siga autenticada. Las cookies que expiran
   legítimamente no se pueden prometer; la prueba diagnóstica acreditó las cookies fixture.
6. Tras verificación y fsync, confirmar atómicamente la candidata como nueva generación
   activa limpia. La sustitución durable del puntero activo es el punto de confirmación;
   no mover ni sobrescribir el directorio anterior. Sin cambio confirmado, sigue pendiente.
   Registrar resultado administrativo y copia preservada; no conservar la confirmación
   del operador como autorización de una recuperación futura.

Si falla la validación, no publicar candidata. Si cae el proceso alrededor de la
confirmación, un marcador durable de mantenimiento incompleto obliga a revisar mediante
el mismo comando, con NUEVA confirmación. Reconciliar el puntero y la operación registrada,
no inferir el resultado por nombres recientes. El arranque del servicio no termina una
recuperación pendiente. Conservar fuente, respaldo, candidata y evidencias; nunca elegir
perfil vacío por conveniencia. Salida distinta de cero significa no arrancar acquisition.
Tras éxito, verificar de nuevo el estado limpio bajo el mismo bloqueo antes de liberar.
No borrar respaldos automáticamente; retención y eliminación son operaciones separadas.

## Procedimiento Compose especificado en el diseño histórico

Los comandos que siguen describen la propuesta aprobada para el perfil, no la operación actual. No ejecutarlos como procedimiento vigente: el overlay actual y sus requisitos están documentados en los enlaces de la nota superior. En la propuesta histórica, la configuración del workspace estaba en `.env.docker`, sin secretos; las rutas del anfitrión no se incrustaban en las imágenes.

La propuesta definía un perfil administrativo `maintenance` con servicio `acquisition-admin`. Ese servicio no es un daemon ni un servicio permanente: usaría la misma imagen, usuario, volumen de perfil, ruta de bloqueo y restricciones, con `network_mode: none`, sin puertos ni dependencias. No montaría SQLite deportivo ni su journal. Los respaldos del perfil permanecerían en su almacenamiento exclusivo.
No pasar al admin credenciales de API de adquisición o del backend; sí necesita el perfil completo, que contiene sus cookies y debe tratarse como material sensible. Nunca `down -v` o prune.

Arranque habitual con perfil compatible disponible, sin recuperación ni consultas automáticas:

```sh
docker compose --env-file .env.docker -f compose.yaml -f compose.acquisition.yaml up -d acquisition
```

Recuperación: ejecutar cada paso solo cuando el anterior haya terminado correctamente.
No arrancar por un `finally`, por separadores `;` o después de un error ignorado.

```sh
docker compose --env-file .env.docker -f compose.yaml -f compose.acquisition.yaml stop acquisition
docker compose --env-file .env.docker -f compose.yaml -f compose.acquisition.yaml ps -a acquisition
```

El administrador comprueba además otros despliegues/procesos que monten ese volumen,
y confirma por conocimiento operativo que ya no tienen acceso. `ps` es información,
no una autorización. Solo entonces:

```sh
docker compose --env-file .env.docker -f compose.yaml -f compose.acquisition.yaml --profile maintenance run --rm --no-deps acquisition-admin recover-profile --confirm-service-stopped --confirm-no-external-users
```

Si y solo si devuelve éxito, comprobar la generación y respaldo confirmados mediante
el subcomando de solo lectura bajo flock; este verificador no abre Chromium ni modifica perfil:

```sh
docker compose --env-file .env.docker -f compose.yaml -f compose.acquisition.yaml --profile maintenance run --rm --no-deps acquisition-admin verify-profile
```

Solo tras éxito de ambas operaciones:

```sh
docker compose --env-file .env.docker -f compose.yaml -f compose.acquisition.yaml up -d acquisition
docker compose --env-file .env.docker -f compose.yaml -f compose.acquisition.yaml logs --tail 100 acquisition
```

No se para ni reinicia backend/frontend para recuperar este perfil. Mientras acquisition
esté bloqueado o detenido, el dashboard conserva su última generación deportiva válida.
El arranque del scraper instalado fuera de este esquema debe impedirse operacionalmente
para este directorio; la recuperación no mata procesos por leer un PID del singleton.

## Límites y validación requerida para la implementación

flock coordina nuestras herramientas sobre almacenamiento con bloqueo efectivo; no
impide que otro programa ignore el protocolo. No compartir perfil con herramientas
externas ni varios hosts. El administrador debe excluir esos consumidores antes de
confirmar. No es una protección frente a un administrador malicioso o una confirmación
errónea. La exclusión, fsync, permisos y semántica del volumen deberán verificarse en el
servidor; no extrapolar los resultados Docker Desktop a NFS/SMB, otra CPU o LSM.

Se conservan íntegramente las pruebas superadas: sandbox y contenido local, cookies
originales con otro hostname, dos competidores, SIGKILL, SIGTERM y fallo con fuente/copia
intactas. Su mecanismo de JSON del lanzador queda SOLO como instrumento histórico, no
forma parte del diseño final ni certifica el futuro comando. No se reescribe su resultado.

La implementación tendrá pruebas nuevas: arranque limpio automático sin red; estado
pendiente tras SIGKILL, incluso sin singleton; rechazo sin las dos confirmaciones;
rechazo con flock ocupado aunque se confirmen; cookies recuperadas con hostname distinto;
respaldo verificado antes de limpiar candidata; ausencia de fallback; fallos de copia,
ENOSPC y verificación; SIGTERM y caídas antes/después de confirmar puntero; no arranque
tras mantenimiento fallido. Usar únicamente datos/perfiles temporales y contenido local.
Este cambio documental no constituye ejecución de esas pruebas futuras.

## Archivos concretos de la futura implementación

Rutas propuestas; no se crean módulos ni overlays en esta entrega:

| Repositorio / archivo | Cambio previsto |
|---|---|
| api/acquisition/__init__.py | Paquete pequeño, sin framework genérico de trabajos |
| api/acquisition/profile.py | flock permanente, rutas canónicas, estados/generaciones, copias verificadas, confirmación durable y errores de recuperación; sin lógica Docker o JSON de autorización |
| api/acquisition/admin.py | CLI initialize-profile para destino nuevo, recover-profile y verify-profile; confirmaciones explícitas, red deshabilitada y códigos de salida; reutiliza profile.py |
| api/acquisition/browser.py | Adaptador estricto del BrowserBridge fijado: sandbox explícito, una ruta bajo propietario, rechazo de _use_secondary_profile, cierre/hijos; no monkeypatch global en producción |
| api/acquisition/service.py | Servicio interno mínimo, único trabajo activo, estado durable y readiness bloqueada por perfil; sin consultas automáticas en arranque ni comandos arbitrarios |
| api/acquisition/export.py | Preparar/verificar/sellar bundles inmutables desde mecanismos del scraper; nunca importar datos deportivos |
| api/acquisition/recipes.py | Recetas fijas aprobadas y configuración de ámbito; implementar SOLO tras concretar las políticas históricas/personales y cobertura |
| api/docker/acquisition/Dockerfile y .dockerignore | Imagen reproducible con scraper exportado/revisión fija y navegador, UID no root; excluye frontend, datos, env y lock ajeno; sin dependencias SQL deportivas innecesarias |
| api/docker/acquisition/requirements.lock | Versiones exactas de dependencias de servicio y navegador; manifiesto/digest reproducible; build separado del diagnóstico |
| api/docker/acquisition/seccomp-linux-amd64.json | Copia revisada de baseline más cinco reglas aprobadas, sin ampliación; otras arquitecturas requieren perfil/revisión propios |
| api/tests/acquisition/test_profile.py y test_admin.py | Contrato de propietario, confirmaciones, copias/candidata, fallos de publicación y ausencia de fallback |
| api/docker/acquisition/test_offline.py | Integración Linux/Compose aislada con contenido local, sandbox, concurrencia, señales y recuperación administrativa real |
| app-real-jaen/compose.acquisition.yaml | Overlay optativo acquisition/acquisition-admin, redes separadas, admin sin red, volumen propio externo, sin montaje deportivo ni puertos públicos |
| dashboard/.env.acquisition.example | Variables documentadas sin secretos ni rutas locales: imagen, volumen exclusivo y límites; ninguna variable de auto-recuperación |
| dashboard/docs/docker.md y README.md | Instalación explícita del almacenamiento, arranque normal, mantenimiento/recuperación manual y límites del bloqueo |
| api/README.txt y docs/ | Sustituir referencias de prototipo por comandos implementados, documentar revisiones, seguridad y resultados reales |

Mantener docker/scraper/* y sus resultados como diagnósticos; no convertir
profile_owner_probe.py/test_profile_recovery.py en código de producción ni aplicar su
--fence. El scraper upstream NO necesita modificarse para esta política: el adaptador
estricto queda en acquisition/browser.py, con pruebas que fijan el comportamiento de
la revisión exportada. Su package-lock ajeno queda excluido.

Antes de implementar TODO el servicio siguen pendientes de aprobación: contrato interno
exacto y autorización backend→acquisition, recetas históricas/personales, cobertura parcial,
límites/retención y validación del servidor. Este diseño final resuelve la política del
perfil; no autoriza endpoints, botón, integración del coordinador ni peticiones reales.
No cambiar la API deportiva/normalización o los dos servicios actuales por anticipación.
