# Instrucciones para agentes

Valen para este repositorio. No se heredan de `../rj-analyst/AGENTS.md`: si un cambio afecta también al
dashboard, leer y cumplir además las de allí.

## Propósito

Backend del dashboard `rj-analyst` del Real Jaén CF: importador, base SQLite, API de lectura, control de
actualizaciones y servicio de adquisición desde SofaScore. Mapa y documentación: [README.md](README.md). Estado de la
adquisición: [docs/adquisicion-sofascore.md](docs/adquisicion-sofascore.md).

## Límites

- La instalación se hace con Docker Compose desde `rj-analyst` (`compose.yaml`, `compose.acquisition.yaml`). No
  introducir otra base, colas, workers, servicios ni proveedores salvo decisión explícita.
- El scraper (`sofascore_scraper`) es externo y está fijado a `611e436` con dos commits propios
  (`docker/scraper/real-jaen-follow-next-pages.bundle`, `docs/scraper-empaquetado-linux.md`). No modificar el checkout
  instalado; su cambio ajeno `frontend/package-lock.json` queda fuera de todo commit.
- Nunca consultar SofaScore, ejecutar una adquisición real ni pulsar «Actualizar» sin autorización expresa, con
  límites y alcance acordados. Las pruebas usan sitios locales, respuestas grabadas o `--network none`.
- No modificar la base activa, los volúmenes Docker ni los servicios en marcha sin autorización. Trabajar sobre
  copias (`persistencia copiar`, respaldos) en volúmenes o carpetas nuevas; no borrar WAL/SHM, `.lock` ni copias.
- Secretos (tokens, contraseña, secreto de sesión) y perfiles del navegador con cookies: solo en archivos o
  volúmenes montados. Nunca en Git, imágenes, logs, informes ni salida de comandos.

## Datos

- Nunca convertir ausencia de dato en cero: `null`, `not_available`, `not_collected` y cobertura
  `complete/partial/none` tienen significado distinto.
- Conservar originales, procedencia (`export`, línea, huella, `fetched_at`), discrepancias y decisiones locales
  (`correcciones/`). La selección de versiones es la de `importar.py`; la API no aplica otra.
- Los IDs públicos que ve el dashboard son estables; no cambiarlos al reimportar.
- Cambios de esquema solo con una migración Alembic nueva y su `downgrade`; nunca migraciones automáticas al
  arrancar.

## Contratos

- Las rutas `/api/v1/*` de `api.py` las consume `rj-analyst/src/server/local-api`, que además comprueba algunos
  valores derivados (p. ej. los tres partidos más recientes y las medias de goles). Cambiar una respuesta exige el
  cambio coordinado en el dashboard: detenerse y explicar primero el impedimento y la propuesta mínima.
- El control de actualizaciones (`control.py`) y el contrato con la adquisición
  ([docs/contrato-adquisicion-interno.md](docs/contrato-adquisicion-interno.md)) no se amplían por conveniencia.
  Los límites de la adquisición (intentos, envíos, duración) solo pueden reducirse por configuración.
- `productores-adquisicion.json` (productores aprobados) y `docker/acquisition/seccomp-linux-amd64.json` solo se
  cambian con aprobación explícita: afectan a qué código puede publicar y al sandbox del navegador.

## Validación

- Pruebas sin red: `python -m unittest discover -s tests -v` (con `requirements-sql.txt` instalado; ver README).
- Cambios en `acquisition/`, `control.py`, `persistencia/adquisicion.py` o en las imágenes: además, la prueba de
  `docker/acquisition/` que corresponda (README, «Pruebas en Docker»).
- Cambios solo de documentación: comprobar enlaces y comandos citados; no ejecutar suites completas.
- No arreglar fallos ajenos a la tarea sin autorización.

## Git

- Trabajar en la rama actual; no hacer push ni mergear con otras ramas.
- No hacer commits hasta que la tarea lo solicite. Cuando se pidan, pequeños y de la tarea acordada.
- No descartar ni revertir cambios locales que no haya creado el agente.
- Fuera de Git: `datos/`, `.venv/`, bases SQLite, exportaciones, perfiles, evidencias y secretos.

Si una petición contiene explícitamente «detente», no continuar con tareas relacionadas aunque parezcan el siguiente
paso lógico.
