# Calendario HTTPS con navegador gestionado: validación y comando preparado

Estado: HTTPS local validado, consulta real NO ejecutada ni autorizada por esta entrega. No se reanuda el piloto deportivo de cinco GET y no hay importación/publicación.

## Resultado propio

16 pruebas HTTPS en 31,236 s y 23 de regresión HTTP en 37,629 s, satisfactorias. Docker Desktop Linux x86_64, UID10001, capabilities cero, no-new-privileges, sandbox explícito y cinco excepciones seccomp originales sin cambios. Todos los ensayos usaron network:none, solo loopback y recursos exclusivos. No se abrió SQLite ni journal ni se montaron volúmenes habituales. Logs y registro completo del servidor local fuera de Git; metadatos en [calendario-https-validacion.json](calendario-https-validacion.json).

El servidor HTTPS recibió 12 GET repartidos entre escenarios independientes, máximo uno por instancia, sin otras rutas/métodos. Con certificado no confiable, caducado o SAN incorrecto recibió CERO GET; con certificado válido recibió exactamente uno y el calendario se validó. Los certificados y claves fueron efímeros, generados con OpenSSL dentro del contenedor. La CA se registró exclusivamente en una base NSS de un HOME temporal de prueba, mediante libnss3/libsmime3 ya instaladas. No se modificó la confianza del host ni una imagen ni se instaló certutil. ignore_https_errors=False y ninguna excepción de certificado.

El TLS es de extremo a extremo entre Chromium y el servidor, no una petición Python que se presente como navegador ni una terminación/intercepción TLS. La confianza se acredita con los casos positivos y negativos nativos. La respuesta interceptada/reescrita para acotar su cuerpo no conserva security_details de Playwright; no se afirma exportar la huella del certificado desde ese objeto. Se retiró un intento de obtener esa metadata CDP porque podía esperar al request pausado; se acotó la espera de cierre de callbacks a dos segundos. No se relajó validación TLS para resolverlo.

Se comprobaron 403, 429, 408, conexión cortada, challenge rechazado antes de ejecutar contenido, redirects sin seguimiento, cuerpo grande, chunked, plazo global y reserva consumida tras timeout. Un segundo CONNECT que Chromium intentó en ciertos errores se denegó localmente: no creó otra conexión al origen ni otro GET. Canal Unix de un solo uso también probado con TLS local; reutilizarlo se rechaza. Sandbox, ruta/generación gestionada y cierre de hijos siguen acreditados.

## Control y límites

- Un GET de documento a la URL exacta del calendario, cero reintentos y un solo canal TCP/TLS al origen. Reserva canónica del perfil y reserva del gateway antes del socket; no se eliminan al cerrar ni al fallar. URL/métodos adicionales se rechazan, sin home, captcha, probes, fichas o eventos. Sin solver de challenge.
- HTTP/1.1, con HTTP/2 y QUIC desactivados para evitar multiplexación y canales alternativos. No se cambian User-Agent, identidad, proxy de salida público ni certificados para eludir el rechazo. No se promete que el proveedor admita esta configuración; todo rechazo detiene el ensayo.
- 2 MiB de cuerpo decodificado, verificado mediante stream CDP antes de entregar contenido a la página. Content-Length superior, compresión, contenido no JSON o challenge se rechazan. El límite conservador puede rechazar un cuerpo de tamaño exacto si no acredita EOF. No se ejecuta HTML del proveedor para resolver bloqueos.
- 2.125 MiB (2.228.224 bytes) desde el origen por el canal opaco, incluyendo TLS, certificado, cabeceras y cuerpo cifrado. Se corta sin superar el límite de bytes reenviados. No incluye buffers del kernel que el servidor haya escrito, DNS, paquetes TCP ni cabeceras del proxy local; no se presenta como cuota exacta de interfaz Ethernet.
- Plazo de actividad 30 s, navegación 20 s, sockets como máximo 10 s o tiempo restante. Supervisor de proceso cubre arranque, consulta y cierre: al vencer termina solo su árbol; hasta 2 s adicionales para recogida forzada. Una caída puede dejar perfil pendiente de recuperación manual. No repetir automáticamente, borrar reservas/Singletons ni crear un perfil alternativo.

## Separación de salida para el comando real preparado

Para mantener Chromium sin interfaz externa se preparó un gateway efímero de diagnóstico, no un servicio integrado. El navegador usa network:none. Su proxy loopback solo acepta CONNECT al host y puerto fijados y abre un socket Unix de un volumen exclusivo. El otro contenedor, sin perfil ni datos deportivos y con seccomp Docker ordinario, solo puede abrir en su código una conexión a www.sofascore.com:443. Resuelve ese nombre una vez, elige una dirección IPv4 y no prueba otra IP si falla. No acepta URLs, hosts, puertos ni comandos del cliente. Solo reenvía el TLS opaco, con reserva, plazo y cuota; no ve cookies o cuerpo en claro. DNS/TLS son requisitos de transporte, no GET auxiliares.

El canal Unix y el relay se validaron contra HTTPS local dentro de network:none. Además, scripts/test_https_split.py comprobó el recorrido con DOS contenedores separados, ambos network:none: un único GET HTTPS al origen local, socket compartido montado de solo lectura en el navegador y volumen del perfil ausente en el gateway. Ambos contenedores y sus dos volúmenes exclusivos se retiraron tras comprobar etiquetas; evidencia en el informe. No se incluyeron certificados o claves en Git. El despliegue de dos contenedores con la salida pública, DNS real y el certificado de SofaScore NO se ha ejecutado. No se afirma validación del servidor del grupo. El comando inspeccionará identidad, permisos y restricciones del despliegue; cualquier fallo detiene la ejecución. No cambia redes o políticas globales. No monta el socket Docker dentro de ningún contenedor.

El launcher crea un perfil de prueba NUEVO solo con confirmación explícita; no es fallback de un perfil ocupado. La inicialización administrativa se hace con network:none antes de activar el canal. Gateway solo monta portal; navegador solo perfil y portal, además del código de solo lectura. No monta SQLite, journal, datos, .env.local o secretos. El navegador real usa la confianza estándar de la imagen, nunca la CA local del ensayo.

Imagen ya local y fijada, Docker Linux amd64, CLI Docker, Python estándar en el anfitrión y espacio/permisos para volúmenes son requisitos. No pull, descargas de paquetes ni arquitectura forzada: el perfil seccomp concreto se acredita solo en amd64. No es instalación habitual ni requiere el frontend/scraper en ejecución.

## Preflight y comandos

[preflight-calendario-navegador.json](preflight-calendario-navegador.json) fija URL, imagen, seccomp, hashes de código, límites e identidad única de ejecución. Modificar código invalida el preflight. Confirmar explícitamente una consulta y un perfil nuevo es necesario; leer un JSON no autoriza recuperación administrativa. La instalación habitual conserva sus controles deshabilitados.

Comprobación efectivamente ejecutada: --help y --check. Este último devolvió checked_only, requests=0, profile_created=false. No se crearon los recursos del piloto real. Prefijo reservado propuesto: rj-https-calendar-adef868e8d934aefba8043df28e578a9. Se comprobó que sus volúmenes y la salida propuesta no existían; se vuelve a comprobar antes de crear. Si existen se rechazan, no se reutilizan, sobrescriben o limpian. La misma identidad de preflight no genera otro perfil al repetir el comando.

Comando preparado para una autorización posterior, NO ejecutado (PowerShell, una línea):

```powershell
& 'C:\Users\broke\dev\api-real-jaen\.venv\Scripts\python.exe' -B -X utf8 'C:\Users\broke\dev\api-real-jaen\scripts\run_https_calendar_once.py' --preflight 'C:\Users\broke\dev\api-real-jaen\docs\preflight-calendario-navegador.json' --confirm-one-get --confirm-new-isolated-profile --output 'C:\Users\broke\AppData\Local\Temp\rj-sofascore-calendario-adef868e'
```

Equivalente portable desde raíz del backend, con salida nueva:

```sh
python scripts/run_https_calendar_once.py --preflight docs/preflight-calendario-navegador.json --confirm-one-get --confirm-new-isolated-profile --output /tmp/rj-sofascore-calendario-adef868e
```

Para comprobar sin ejecutar, sustituir las dos confirmaciones y output por --check. Solo inspecciona archivos, Docker y la imagen local; no crea perfil/canal ni consulta DNS del proveedor.

Tras una ejecución futura, los contenedores propios se retiran comprobando etiquetas; se conservan los volúmenes privados de perfil y portal y las reservas. No se exportan cookies/perfiles a Git. La recuperación del perfil seguirá siendo manual, con servicio detenido, bloqueo exclusivo, confirmaciones y copia verificada según el diseño aprobado. No borrar volúmenes para repetir silenciosamente un rechazo. No se publican puertos. No se toca la base activa ni servicios actuales. Sin commit ni push.

## Ejecución real posterior autorizada: detenida por 403

El usuario autorizó después una única ejecución del comando preparado. Resultado propio en calendario-https-real-20261027.json. El comando se ejecutó una vez, con el perfil nuevo gestionado y los hashes/imagen fijados. Se permitió un GET de calendario y un canal TLS; el gateway confirmó exactamente una conexión al origen y 4784 bytes recibidos desde él. HTTP 403, content-type application/json y Retry-After: 0. Se detuvo sin reintentar, sin importar ni publicar; Retry-After: 0 no autoriza repetir un rechazo.

Chromium intentó un segundo CONNECT al mismo host: fue denegado localmente y no abrió otro canal ni consultó de nuevo el origen. Los intentos de fondo también se denegaron localmente. No hubo consultas a otras rutas deportivas, home, captcha ni solver. Sandbox verificado y errores de certificado sin ignorar. El navegador y sus hijos cerraron correctamente.

El cuerpo se abortó antes de procesarlo conforme a la parada ante 403: no se conservó un calendario ni se acredita un challenge a partir solo del código/Content-Type. El número de GET se acredita por el flujo/registro del cliente y el único canal permitido, no por acceso a logs del proveedor. No se afirma conocer la causa del rechazo.

Los contenedores exclusivos ya se retiraron; se conservan los volúmenes privados y las reservas consumidas. Este preflight está consumido y NO se debe repetir el comando mostrado arriba. No se abrió la base activa ni se modificaron servicios actuales; sin commit ni push. No se ejecutaron más solicitudes al proveedor tras la parada.
