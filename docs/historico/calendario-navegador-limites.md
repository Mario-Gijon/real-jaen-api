# Runner de calendario: control local de salida y presupuestos

Entrega solo offline. Especificación vigente sobre las limitaciones pendientes de tráfico de fondo, descarga y plazo de piloto-calendario-navegador-plan.md. No habilita el ensayo externo ni cambia el rechazo 403 original.

## Fronteras de salida

acquisition/probe_network.py implementa una compuerta HTTP local, no un proxy de propósito general. Solo admite el GET exacto a la URL loopback configurada, una vez. No resuelve nombres, no admite CONNECT, redirects, POST, destinos adicionales o compresión. No incorpora TLS, CAPTCHA, cookies del scraper ni navegaciones de calentamiento. Solo puede conectar al servidor controlado 127.0.0.1 y puerto indicado. Se fuerza el proxy en Chromium, se elimina el bypass implícito de loopback y se desactiva QUIC. Sandbox, capacidades cero y las cinco excepciones seccomp se mantienen intactos.

La evidencia demostró intentos de fondo a servicios de hora, Google y actualización pese a disable-background-networking. Llegaron únicamente a la compuerta local y fueron denegados sin DNS ni conexión al destino: no se afirma que hayan desaparecido. Las denegaciones de esos servicios se registran y no se confunden con el estado HTTP del calendario. Recursos adicionales de página, websocket, popup, worker y redirección se bloquean; el trabajo se detiene si la página intenta trabajo extra. Ninguna denegación de fondo se reenvía. network:none es la frontera del SO que impide salida externa, incluso si una ruta no pasase por el proxy; require_offline exige que solo exista loopback. No se ha habilitado otro modo.

La interceptación de contexto y CDP continúa para las solicitudes y respuestas de página; la compuerta también rechaza redirects antes de enviar cuerpo. No basta con un argumento de Chromium para acreditar aislamiento. Esta arquitectura prueba HTTP local controlado; no equivale a demostrar un túnel HTTPS seguro y acotado para el servidor real. El modo externo se rechaza antes del navegador.

## Presupuestos y persistencia

- Un único GET al origen por ejecución/perfil, cero reintentos. La compuerta crea de forma exclusiva control/calendar-probe.reservation.json bajo el perfil gestionado y hace fsync del archivo y directorio ANTES del socket al origen. El propietario Profile mantiene el bloqueo SO durante la sesión. Un ledger alternativo, error de disco posterior a reserva, HTTP rechazado, reinicio o caída no restablece ese permiso. El archivo no se borra automáticamente ni al cerrar normalmente. El archivo JSON es registro durable del presupuesto, no una autoautorización para recuperar el perfil.
- Máximo 2 MiB de cuerpo de origen leídos por la aplicación, global para ese único GET. Content-Length superior se rechaza sin leer cuerpo. Sin longitud se lee en bloques hasta el límite sin lectura centinela por encima; llegar exactamente al límite sin framing verificable también se rechaza conservadoramente. Cuerpo truncado o Content-Encoding distinto de identity se rechaza. Chromium recibe un cuerpo solo si la compuerta lo ha verificado y acotado; no recibe flujos arbitrarios que luego se midan tarde.
- La cifra de 2 MiB NO incluye cabeceras/framing HTTP, buffers TCP del SO ni las copias internas loopback. No es una cuota exacta de bytes Ethernet. Un servidor puede haber escrito bytes adicionales en sus buffers aunque el lector los rechace; se informa de bytes de cuerpo efectivamente leídos, sin afirmar que el servidor no los emitió. La prueba usa el framing y parser de http.client; no se declara una cuota global de cabeceras.
- CLI supervisada: plazo de actividad de 30 s desde el lanzamiento del trabajador, incluye arranque, consulta, validación y cierre normal. Al vencer, termina únicamente el árbol propio de procesos, comprobando PID/starttime; espera de recogida acotada a 2 s. Presupuesto de pared: 30 s de actividad más hasta 2 s de recogida forzada, sujeto a planificación del SO. No es garantía de tiempo real del kernel. Navegación sigue limitada a 20 s y sockets al tiempo restante con tope 10 s. Las pruebas reducen el plazo para comprobar el corte del proceso, no solo de page.goto.
- SIGTERM/SIGINT del supervisor terminan el trabajador y su árbol, conservando reserva. SIGKILL no ejecuta finally: queda perfil pendiente y reserva consumida; no hay repetición ni limpieza automática de Singletons. Si se mata el supervisor con SIGKILL, no se promete recoger desde ese proceso ya muerto; la frontera final es su contenedor/PID namespace. No se añadió socket Docker ni supervisor de servicios.

La entrada soportada para los límites globales es el CLI supervisado/run_supervised. El entrypoint --worker es interno y el método async probe usa cancelación cooperativa; no sustituye al watchdog de proceso. Una reserva no se recicla tras recuperación: otro piloto requiere preparación y autorización explícitas, no cambiar un ledger ni borrar archivos de control.

## Resultados propios

Docker Desktop Linux x86_64. 23 pruebas focalizadas satisfactorias en 37,736 s, logs sin callbacks pendientes. Registro de TODAS las solicitudes HTTP recibidas por el servidor controlado mediante parse_request, conservado como origin-requests.jsonl fuera de Git. Se registraron 17 GET en escenarios independientes, máximo uno por instancia del servidor y ninguna ruta/método imprevistos. Las demás pruebas enviaron cero. No son 17 intentos de un mismo trabajo.

Se conservaron las denegaciones recibidas por la compuerta en los resultados de cada caso. El resumen contabiliza 141 denegaciones en los casos que emiten PROBE_RESULT; no se presenta como contador completo de cada proceso finalizado por SIGKILL. Esos destinos nunca se conectaron. Los sockets al origen solo se abren después de la reserva.

Casos: éxito supervisado, sandbox y /proc, perfil/cookies entre sesiones, perfil ocupado o pendiente, URL real deshabilitada, 403, 429/Retry-After, challenge sin resolución, redirects, recursos extra y tráfico de fondo, métodos/CONNECT prohibidos, Content-Length grande (cero cuerpo leído), chunked sin longitud (2 MiB exactos como máximo), contenido comprimido (cero cuerpo leído), timeout de navegación y plazo global, fallo de reserva, ENOSPC después de reserva canónica, cambio de ledger, SIGKILL después de GET y SIGTERM del supervisor. En timeout global y SIGTERM se acreditó ausencia de nuevos hijos; intentos posteriores no alcanzaron el origen. Las pruebas de caída usan exclusivamente el árbol propio, no procesos ajenos.

Informe: calendario-navegador-limites-validacion.json. Perfil solo en tmpfs exclusivo; contenedor retirado con identidad comprobada. Sin SQLite, journal, secretos, volúmenes habituales o publicación. No se repitieron suites API ajenas a este runner.

## Reproducción y límites pendientes

Desde la raíz del backend, con Docker Linux amd64 operativo, imagen ya local y salida nueva:

```sh
python scripts/test_calendar_probe.py --image rj-pilot-package:review-20261026-final --output /tmp/rj-calendar-budget-review-new
```

No pull ni instalación de paquetes. Código de acquisition y tests montados de solo lectura, perfiles tmpfs, network:none, usuario 10001:10001, capabilities cero, no-new-privileges, init y seccomp aprobado. No se ha construido otra imagen ni modificado la instalada.

Quedan pendientes servidor del grupo, otras arquitecturas, TLS/HTTPS y controles de salida adecuados si se propusiera red real. No se debe cambiar network:none ni quitar require_offline para dar por validada esa transición. El presupuesto de cuerpo queda acotado; una cuota exacta que incluya todo tráfico y cabeceras necesitaría otro diseño explícito. No se integra el scraper ni se elude el 403. Sin contacto SofaScore, cambios en base activa/servicios, commit o push.

## Validación HTTPS posterior

La prueba HTTPS local y el comando real preparado, aún no ejecutado, se especifican en [calendario-https-piloto.md](calendario-https-piloto.md). Esa entrega añade validación nativa de certificados, límite del canal TLS y una salida Unix de un solo uso, sin habilitar servicios habituales ni acreditar aún el destino público.
