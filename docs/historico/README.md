# Archivo histórico de los pilotos de adquisición (26-28 de octubre de 2026)

Informes y evidencias que ya no describen el estado vigente. Se conservan sin cambios de contenido (solo enlaces
relativos ajustados al moverlos); huellas originales en `.manifiesto.sha256`. Estado vigente:
[../adquisicion-sofascore.md](../adquisicion-sofascore.md).

| Archivo | Qué fue | Sustituido por |
|---|---|---|
| `comparacion-descarga-y-pilotos.md` | Comparación de la descarga histórica con los dos pilotos 403 | `adquisicion-sofascore.md` (corrige que la jornada 1 sí se obtuvo el 22-10) |
| `receta-sofascore-primera.md`, `receta-sofascore-offline.md` | Primeros diseños de receta y contrato real v2 | `piloto-sofascore-tecnico.md`, después el protocolo unificado |
| `piloto-calendario-navegador-plan.md`, `calendario-navegador-limites.md`, `calendario-https-piloto.md` | Runner de calendario con navegador limitado y pasarela TLS de un GET | Flujo normal del SDK con tráfico reducido |
| `recuperacion-perfil-linux.md` | Diseño de recuperación de perfil con adaptador diagnóstico | `../gestion-perfil-adquisicion.md` |
| `preflight-sofascore-piloto.json`, `preflight-calendario-navegador.json` | Preflights ya consumidos: no reutilizar | — |
| `piloto-sofascore-{copia,linux,real}-20261027.json` | Resultados del piloto HTTP (copia, Linux y ejecución real 403) | — |
| `calendario-https-*.json`, `calendario-navegador-*.json` | Resultados de la pasarela de calendario (validación local y ejecución real 403) | — |
| `piloto-sofascore-tecnico.md`, `piloto-sofascore-validacion.json`, `sofascore-offline-validacion.json` | Protocolo v2 del piloto y receta offline, con sus validaciones (retirados el 28-10) | `../contrato-adquisicion-interno.md` |
| `contrato-adquisicion-interno-v1-simulado.md`, `integracion-simulada-v1-validacion.json` | Contrato e integración simulada v1 (proveedor de fixtures, bundle de un archivo) | `../contrato-adquisicion-interno.md` |
| `docker-acquisition-validation-results.json` | Resultados de la gestión de perfil con el seccomp en CRLF (`e340…`) | `docker/acquisition/validation-summary.json` (LF, `abd5…`) |

El código de la pasarela de calendario se retiró del repositorio a
`C:\Users\broke\dev\archivo-api-real-jaen\2026-10-27-pasarela-calendario\` (manifiesto y diff incluidos).
