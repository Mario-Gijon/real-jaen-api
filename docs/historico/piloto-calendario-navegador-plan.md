# Revisión del 403 y propuesta de prueba de calendario con navegador

Estado: solo revisión local y plan; ninguna consulta externa nueva. No autoriza ejecutar el plan. La autorización anterior era HTTP sin navegador y terminó en el primer 403.

## Evidencia demostrada

Se abrió únicamente el volumen exclusivo rj-real-pilot-a694ac441f39-cache en un contenedor network:none, montaje de solo lectura. Cuerpo original: 48 bytes, SHA-256 6b771bca0fe4271cc0baf29954f10c68be99102048e4df524b6d7d6bf38dad5d. Contenido: `{"error": {"code": 403, "reason": "Forbidden" }}`. No contiene challenge, HTML, CAPTCHA o instrucciones de resolución. El registro durable acredita HTTP 403 para /unique-tournament/17073/season/97382/events/round/1; un solo intento. El piloto conservó el cuerpo pero no los headers de respuesta: no se puede determinar cf-mitigated, cf-ray, servidor o reglas de WAF. Tampoco se puede inferir que el navegador tendrá acceso.

El scraper fijado en 611e436a1528b0f487aa56a045f023e8ae8ae098 activa la rama de fallback del 403 cuando encuentra la cadena `challenge` en el cuerpo (client/transport.py, rutas sync/async). Esta evidencia NO cumple esa condición. Es un rechazo explícito, no un challenge acreditado que el flujo normal vaya a resolver. Su causa concreta permanece desconocida. No atribuirla sin evidencia a IP, fingerprint, geografía o cookies.

## Incompatibilidades antes de ejecutar

El wrapper validado acquisition/browser.py exige network:none y bloquea solicitudes externas. No existe hoy una opción soportada para usarlo en red. No se quitará require_offline ni se monkey-patcheará para presentar una validación falsa. Se necesita un runner diagnóstico de red separado y acotado que reutilice propietario Profile, parámetros de sandbox, prueba chrome://sandbox + /proc y cierre del wrapper, sin cambiar el modo offline administrativo.

BrowserBridge del scraper no es intercambiable sin adaptación: arranca Scrapling, puede usar perfil secundario temporal, navega a home/captcha y tiene un probe /unique-tournament/17/seasons. Su sesión usa solve_cloudflare y puede generar múltiples recursos y reconsultas. Todo ello excede este plan de una sola ruta y no está validado con nuestro propietario/sandbox. No invocarlo automáticamente ni importar sus efectos para resolver un rechazo. No resolver CAPTCHA/Turnstile, generar tokens de desafío ni cambiar identidad, proxy o huellas para eludir la restricción.

## Prueba propuesta, pendiente de aprobación

Objeto: comprobar una vez si una sesión de navegador estándar recibe el mismo rechazo en el calendario; no resolver desafíos ni completar la adquisición deportiva. URL única: https://www.sofascore.com/api/v1/unique-tournament/17073/season/97382/events/round/1.

1. Crear nombres aleatorios exclusivos, con etiqueta de ensayo; comprobar que no existen. Perfil nuevo de prueba, inicializado explícitamente mediante acquisition.admin con network:none. No reutilizar perfiles/cookies habituales ni el volumen de caché del rechazo. Fijar imagen auditada y cinco excepciones seccomp de docker/acquisition/seccomp-linux-amd64.json sin cambios; Linux x86_64, UID 10001:10001, cap_drop ALL, no-new-privileges, init y shm_size 512m. Sin socket Docker ni secretos. Solo volumen de perfil y salida de evidencia; no montar SQLite, journal, exportaciones o carpetas deportivas.
2. Primero validar el runner con HTTP local y red externa deshabilitada: presupuesto, redirects, 403/429, cancelación, cierre y perfil. Mantener el bloqueo flock de Profile durante toda la sesión y acreditar la generación/ruta --user-data-dir en /proc; nunca invocar perfil temporal. Si ocupado o pendiente de recuperación, detener. Administrar recuperación exclusivamente sin red con confirmaciones explícitas.
3. Antes de permitir red, acreditar chrome://sandbox y /proc como en el wrapper actual. Instalar interceptación antes de la primera navegación externa, bloquear service workers, descargas, websockets y cualquier URL diferente de la única URL exacta. Desactivar trabajo de red en segundo plano y no abrir home/captcha, probes, recursos auxiliares o terceras partes. No usar credenciales, tokens importados, cambios de User-Agent/huella ni técnicas stealth adicionales.
4. Permitir UNA navegación GET a esa URL: máximo un intento, cero reintentos, concurrencia uno, timeout de navegación 20 segundos, plazo externo 30 segundos, límite de cuerpo conservado 2 MiB. Registrar la reserva durable ANTES de autorizar la solicitud. Cualquier redirect o solicitud adicional se aborta, registra y termina la prueba. No consultar event/lineups/statistics/incidents.
5. 403, 429, challenge, CAPTCHA, rechazo explícito, TLS inválido, error de red o contenido ilegible: parada, sin resolución ni repetición. Guardar Retry-After si existe sin programar nuevos intentos. En 200 exigir JSON con events y verificar ámbito/temporada, duplicados y presencia del evento 16599363 mediante funciones existentes, sin inferir ausencia si falta. Resultado solo diagnóstico: no bundle, importación, candidata o publicación.
6. Cerrar normalmente con tiempo para recoger hijos; conservar perfil y evidencia exclusiva fuera de Git. Si cierre falla o caída, marcar recuperación necesaria, no limpiar Singletons ni reiniciar automáticamente. Registrar rutas y nombre de volumen para revisión; no borrar evidencia ni volúmenes ajenos.

Límite de peticiones: exactamente una solicitud externa permitida por el contexto, no cinco ni diez. Interceptación debe acreditarse localmente para redirects, worker y solicitudes concurrentes. Las rutas del contexto no bastan para demostrar por sí solas ausencia de todo tráfico de procesos Chromium: el runner debe comprobar la configuración efectiva y tráfico auxiliar antes de afirmar un límite absoluto de red. Si no se puede cumplir el presupuesto sin recursos auxiliares, cancelar la prueba y presentar esa limitación; no ampliar automáticamente hosts, rutas o permisos. Consultas DNS/TLS no se contabilizan como GET, pero no habilitan solicitudes HTTP auxiliares.

## Evidencias a conservar y límites

Registrar estado HTTP, content-type, Retry-After y headers de diagnóstico seleccionados (no Set-Cookie, Authorization ni tokens); longitud/hash del cuerpo y original privado. Sin cookies/perfiles/cuerpos en docs o Git. Resumen: request ledger, URL exacta, intentos, bloqueos, generación del perfil, sandbox y /proc, mounts, UID, capabilities, perfil seccomp y cierre de hijos. Un 200 demostraría acceso en ese ensayo, no que el 403 anterior era un challenge ni autorización para reanudar los otros cuatro GET. Un 403 confirma parada sin evasión.

La base activa no se abre: la ausencia de sus mounts en el contenedor se comprueba antes de habilitar red. El ensayo no necesita siquiera la base de prueba. La adopción de este diagnóstico no integra adquisición con backend. Servidor del grupo sigue pendiente de validación. No se han implementado runner, cambios del wrapper ni nuevos permisos; no se han lanzado nuevas consultas, servicios, commit o push.

## Implementación offline del runner (entrega posterior)

Implementado acquisition/calendar_probe.py, pruebas docker/acquisition/test_calendar_probe.py y launcher scripts/test_calendar_probe.py. Solo acepta HTTP literal 127.0.0.1 con la ruta exacta del calendario; require_offline exige interfaz loopback como única interfaz. URL real, local=False, credenciales, queries y fragmentos se rechazan antes de abrir sesión/navegador. No se añadió un interruptor para habilitar tráfico externo. Esta restricción es deliberada hasta revisar las limitaciones de red. La parte anterior que indicaba que no existía runner queda superada exclusivamente por esta implementación local.

Se extraen dos puntos mínimos de extensión de SandboxBrowser (comprobación de entorno e instalación de rutas); el comportamiento administrativo sigue siendo offline y el runner reutiliza su lanzamiento, acreditación de sandbox/ruta /proc y cierre. Se bloquean service workers. Profile mantiene flock y estado durable durante toda la sesión; sin perfiles secundarios, recuperación automática ni uso del BrowserBridge del scraper. No se modificó el scraper instalado.

Presupuesto: reserva durable antes de route.continue_, un único GET de documento a la URL exacta, cero reintentos. Segunda solicitud, destino distinto, método distinto y websocket se rechazan. Un ledger existente no se sobrescribe/repite. Se usa CDP Fetch en fase de respuesta para abortar 3xx antes de seguir Location: las pruebas demostraron que context.route por sí solo deja pasar redirects y no era suficiente. No hay resolución de challenge. Ante 403/429 se termina sin nuevas consultas; Retry-After se conserva como evidencia, no programa trabajo.

Validación propia en Docker Desktop Linux x86_64: 11 pruebas en 11,851 segundos, sin errores de callbacks. Éxito JSON, 403, 429, challenge sin resolución, redirect, recursos adicionales, timeout, fallo ENOSPC al reservar (cero GET recibidos), perfil ocupado/pendiente, URL real deshabilitada, wrapper offline original y cookies entre sesiones. Se comprueban generación gestionada, ruta real y sandbox mediante chrome://sandbox y /proc, perfil listo al cerrar, ausencia de nuevos hijos y un único GET recibido por el servidor en los escenarios que envían. Los perfiles de prueba fueron tmpfs exclusivos y se eliminaron con el contenedor; cookies/cuerpos no se guardan en Git. Resultado en calendario-navegador-validacion-local.json; logs e inspect fuera del repositorio.

Comando reproducible desde la raíz del backend (imagen previamente disponible, Docker Linux amd64 y directorio de salida nuevo):

```sh
python scripts/test_calendar_probe.py --image rj-pilot-package:review-20261026-final --output /tmp/rj-calendar-local-review-new
```

El launcher usa --pull never, network:none, UID10001, cap_drop ALL, no-new-privileges, init, shm512m y el perfil seccomp aprobado (hash abd5cc8ed37eb8a76b57b6aa9e8042e79ac7f04cb189782e2c8565c0765c5cb7). No modifica sus cinco excepciones. Solo monta código y test de solo lectura, más perfil tmpfs; ninguna SQLite, journal, secreto o volumen habitual. No crea volúmenes nombrados. Inspecciona restricciones antes de arrancar y retira solo su contenedor de nombre impredecible/etiqueta comprobada.

Limitaciones antes de red real:

- No se afirma que se haya hecho una consulta con identidad/TLS real de navegador ni resuelto el 403; únicamente servidor local, sin red externa por Docker. El modo externo sigue deshabilitado y su habilitación requiere otra revisión expresa.
- Interceptación de contexto y CDP no prueban ausencia absoluta de conexiones de fondo de Chromium cuando se habilite red. Hace falta acreditar control de salida y tráfico auxiliar antes de prometer un único GET externo total; no se añadieron proxies ni infraestructura para disimular esa carencia.
- El control de cuerpo de 2 MiB se verifica después de recibirlo en Chromium, no es un límite de descarga en streaming. El plazo de 30 segundos limita la fase de navegación, no el arranque y cierre completo del navegador. El timeout de navegación es 20 segundos. Hace falta endurecer esos límites si se requieren límites globales antes de red real.
- Recuperación tras SIGKILL mantiene el protocolo manual existente; esta entrega no repite su suite diagnóstica ni valida SIGTERM de este nuevo CLI. La caída deja estado de sesión que bloquea el siguiente arranque, sin fallback silencioso.
- Cookies se verificaron entre dos sesiones del mismo perfil tmpfs, no entre recreaciones del contenedor en esta entrega. El servidor del grupo y otras arquitecturas no están validados.

No se modificó base activa ni servicios. Sin consultas SofaScore, commit o push.

## Endurecimiento local posterior

El control local de tráfico de fondo, lectura acotada del cuerpo y supervisor de plazo global se implementaron y validaron en [calendario-navegador-limites.md](calendario-navegador-limites.md). Esa especificación supera las limitaciones de esta sección en el ámbito HTTP local; no habilita salida externa ni afirma validación HTTPS. La reserva canónica del perfil impide repetir el GET aunque se cambie el ledger o haya una caída.
