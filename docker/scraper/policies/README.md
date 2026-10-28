# Excepcion seccomp: artefactos de diagnostico, no produccion

Aprobada y probada solo en sondas el 26-10-2026. Ninguna referencia desde Compose/Dockerfile.
No aplicar a un servicio habitual ni a otro host sin una revision independiente.

- Baseline: perfil OCI resuelto de Docker 29.5.2/amd64 con capabilities cero.
- Probe: ese perfil mas una regla ALLOW para unshare cuando arg0 == 268435456 (CLONE_NEWUSER).
- Delta: diferencia exacta; ninguna otra llamada/accion/arquitectura cambia.
- Resultados: user namespace permitido; Chromium sigue fallando antes de contenido local.
- Manifest: hashes de archivos estables y del baseline original revisado.

El baseline se normalizo de CRLF a LF para funcionar con checkout Linux; el objeto JSON no
cambio. El perfil aplicado conserva el mismo SHA256 fa328590879c20500f0f763f20f59e134d2d5ac0991f4a0aa0ab1b7af045e68c.

Desde la raiz del backend: python3 docker/scraper/verify_userns_artifacts.py.
No llama a Docker ni aplica la politica. Detalle de restricciones, resultados y separacion
propuesta: docs/diagnostico-sandbox-linux.md (en la raiz del backend).

No se probaron cookies, sandbox de una sesion funcional o SIGTERM de esa sesion: la secuencia
se detuvo al fallar Chromium. Los perfiles habituales y los datos deportivos no se montaron.

La traza posterior identifica clone(CLONE_NEWUSER | SIGCHLD) con EPERM.
Los archivos clone-user-sigchld.next.* son una segunda propuesta NO aplicada,
separada del perfil userns aprobado. Ver clone-user-sigchld.trace-results.json.

Prueba posterior aprobada: approved-probe.json aplica tambien clone arg0=0x10000011.
El delta from-initial enumera exactamente las dos reglas adicionales al baseline.
Ver clone-user-sigchld.approved.results.json y la ultima seccion del diagnostico:
clone exacto funciona; siguiente clone(0x70000011) recibe EPERM. Sin ampliaciones.
Los next.* anteriores son historicos, no describen el estado de esta nueva prueba.

Prueba completa posterior: full-candidate.approved-probe.json añade exactamente las
cinco reglas aprobadas; delta full-candidate.from-initial.delta.json. Sandbox, cookies
tras recreación normal y SIGTERM verificados en Docker Desktop x86_64. SIGKILL no deja
los PID capturados en el host; recuperación del perfil FALLA por SingletonLock con otro
hostname y fallback del scraper a perfil vacío. Ver full-candidate.results.json y
la última sección del diagnóstico. Sin integración ni nuevas excepciones.
