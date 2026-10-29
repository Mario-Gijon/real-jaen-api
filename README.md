# api-real-jaen

Backend del dashboard [rj-analyst](../rj-analyst/README.md) para el analista del Real Jaén CF. Sirve, sin
autenticación propia y solo al servidor del dashboard, los datos reales importados de SofaScore:

- **importador** (`importar.py`): exportaciones del scraper → selección de versiones, discrepancias y procedencia;
- **base SQLite** (`persistencia/`): carga, importación incremental, verificación, copias y restauración;
- **API de lectura** (`api.py`) y **modo control** (`control.py`): la misma API más las actualizaciones del botón
  «Actualizar» del dashboard;
- **adquisición** (`acquisition/`): servicio aislado que descarga de SofaScore con el scraper y un navegador.

Instrucciones para agentes: [AGENTS.md](AGENTS.md).

## Mapa

| Ruta | Responsabilidad |
|---|---|
| `api.py` | Rutas `/api/v1/*` del backend y reglas de presentación (rendimiento, acumulados, normalización de convocatorias, detalle de partido) |
| `control.py` | Modo de la instalación: sirve la API desde la base y recibe las órdenes de actualización del servidor Next |
| `importar.py` | Importador: política de versiones, datos maestros, fichas, calendario sin payload |
| `persistencia/` | Base SQLite: modelos y migraciones (`migraciones/`), carga, sincronización incremental, bloqueo, coordinador de actualizaciones (`actualizacion.py`), recepción y verificación de bundles (`adquisicion.py`, `bundle.py`). CLI: `python -m persistencia` |
| `acquisition/` | Servicio de adquisición: receta del Grupo 2, plan, transporte, adaptador del scraper, perfil del navegador y CLI administrativa |
| `ambito.json`, `productores-adquisicion.json`, `correcciones/` | Ámbito servido, productores de bundles aprobados, sustituciones locales |
| `Dockerfile` | Imagen del backend |
| `docker/acquisition/` | Imagen de adquisición, perfil seccomp y pruebas de integración en Docker |
| `docker/scraper/` | Imagen base del scraper con Chromium, bundle con los commits propios del scraper y diagnósticos del sandbox |
| `docker/tools/` | Utilidades de comprobación (huella lógica de una base) |
| `tests/` | Pruebas unitarias e integradas sin red |

## Instalación y operación

Se hace con Docker Compose desde rj-analyst; no hay otra instalación vigente:

- instalación, arranque, mantenimiento, copias y adquisición: [../rj-analyst/docs/docker.md](../rj-analyst/docs/docker.md);
- servidor del grupo: [../rj-analyst/docs/despliegue-servidor.md](../rj-analyst/docs/despliegue-servidor.md);
- contrato del botón: [../rj-analyst/docs/contrato-actualizaciones.md](../rj-analyst/docs/contrato-actualizaciones.md).

## Desarrollo local

Python 3.14 (las dependencias SQL exigen ≥ 3.11):

```sh
python3.14 -m venv .venv && .venv/bin/python -m pip install -r requirements-sql.txt   # Windows: .venv\Scripts\python
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python api.py --almacen sql --base RUTA/real-jaen.sqlite --ambito ambito.json --port 8001
```

- Nunca sobre la base activa de una instalación: usar una copia (`persistencia copiar`, o un respaldo).
- El dashboard en desarrollo apunta a este puerto con `LOCAL_DATA_API_URL` (`../rj-analyst/.env.example`).
  Para usar el backend Docker en su lugar, `../rj-analyst/compose.dev.yaml`.
- Comandos de la base (`cargar`, `importar`, `verificar`, `migrar`, `copiar`, `restaurar`…) y comportamiento del
  importador y de la API: [docs/importador-y-api.txt](docs/importador-y-api.txt).
- `tests/test_exportacion.py` se omite sin la exportación real en `datos/` (fuera de Git).

**Pruebas en Docker** (`docker/acquisition/`, sin salida a SofaScore). Cada script indica su uso en la cabecera:

| Script | Qué comprueba |
|---|---|
| `test_offline.py` | Imagen de adquisición, perfil y sandbox de Chromium sin red |
| `test_integration.py` | Backend ↔ adquisición con respuestas reales grabadas |
| `test_updates.py` | Recorrido completo desde el dashboard (`/api/v1/updates`) |
| `test_group.py` | Receta del Grupo 2 con horas simuladas y un sitio local |

`test_integration.py` y `test_updates.py` reproducen las respuestas de la ejecución real del 28-10-2026: el bundle
sellado, su descriptor y su inventario, sin cookies ni secretos, en `docker/acquisition/fixtures/real-bundle-20261028.tar.xz`
(sumas en `.sha256`). `--snapshot` es una copia inactiva de una carpeta `copias/`. Imágenes de prueba, sin red, desde
un `git archive` de esta revisión (la del scraper, `RJ_SCRAPER_RUNTIME_IMAGE`, y la del backend ya construidas):

```sh
docker build --network none --pull=false --target test --build-arg PROFILE_RUNTIME=rj-scraper-package-efcadb386d:candidate   --build-context implementation=./acquisition --build-context backend=. --build-context checks=./tests/acquisition   -f docker/acquisition/Dockerfile -t rj-acq-portable:test docker/acquisition
docker build --network none --pull=false --target backend-test --build-arg BACKEND_IMAGE=rj-analyst-backend:2c4008b   --build-context backend=. --build-context fixtures=./tests --build-context checks=./tests/acquisition   -f docker/acquisition/Dockerfile.integration -t rj-backend-portable:test docker/acquisition
python -X utf8 docker/acquisition/test_integration.py --acquisition-image rj-acq-portable:test   --backend-image rj-backend-portable:test --snapshot RUTA/copias/COPIA.sqlite --evidence CARPETA-NUEVA
```

Cada prueba conserva sus volúmenes (`rj-acq-test-*`, `rj-upd-test-*`, `rj-grp-test-*`) para inspección; se retiran a
mano, por nombre exacto.

## Documentación

| Documento | Para qué |
|---|---|
| [docs/importador-y-api.txt](docs/importador-y-api.txt) | Referencia del importador, la API y los comandos de la base |
| [docs/persistencia-sqlite.md](docs/persistencia-sqlite.md), [docs/fase4-sincronizacion.md](docs/fase4-sincronizacion.md) | Diseño de la base, migraciones e importación incremental |
| [docs/normalizacion-estadisticas-individuales.md](docs/normalizacion-estadisticas-individuales.md) | Reglas de las estadísticas por partido |
| [docs/adquisicion-sofascore.md](docs/adquisicion-sofascore.md) | **Estado vigente de la adquisición**: punto de entrada |
| [docs/contrato-adquisicion-interno.md](docs/contrato-adquisicion-interno.md) | Contrato backend ↔ adquisición |
| [docs/diseno-receta-grupo2.md](docs/diseno-receta-grupo2.md), [docs/prueba-real-grupo2.md](docs/prueba-real-grupo2.md) | Receta del Grupo 2 y su ejecución real |
| [docs/coordinador-fixtures.md](docs/coordinador-fixtures.md) | Coordinador de actualizaciones (`persistencia/actualizacion.py`) |
| [docs/perfil-adquisicion-diseno-final.md](docs/perfil-adquisicion-diseno-final.md), [docs/gestion-perfil-adquisicion.md](docs/gestion-perfil-adquisicion.md) | Perfil del navegador: diseño y gestión administrativa |
| [docs/scraper-empaquetado-linux.md](docs/scraper-empaquetado-linux.md) | Imagen del scraper y **recuperación de sus commits propios** |
| [docs/diagnostico-sandbox-linux.md](docs/diagnostico-sandbox-linux.md) | Diagnóstico del sandbox de Chromium (útil en un servidor nuevo) |
| [docs/revision-simplicidad-persistencia.md](docs/revision-simplicidad-persistencia.md) | Revisión del 25-10-2026 que motivó la fase 4 |
| [docs/docker.md](docs/docker.md) | Imagen del backend; remite a rj-analyst |
| [docs/historico/](docs/historico/README.md) | Pilotos y protocolos retirados: solo trazabilidad |
