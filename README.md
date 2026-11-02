# api

Backend del dashboard [rj-analyst](../dashboard/README.md) para el analista del Real Jaén CF. Sirve, sin
autenticación propia y solo al servidor del dashboard, los datos reales importados de SofaScore:

- **importador** (`importar.py`): exportaciones del scraper → selección de versiones, discrepancias y procedencia;
- **base SQLite** (`persistencia/`): carga, importación incremental, verificación, copias y restauración;
- **API de lectura** (`api.py`, con reglas deportivas en `queries.py`) y **modo control** (`control.py`): la misma API más las actualizaciones del botón
  «Actualizar» del dashboard;
- **adquisición** (`acquisition/`): servicio aislado que descarga de SofaScore con el scraper y un navegador.

Instrucciones para agentes: [AGENTS.md](AGENTS.md).

## Mapa

| Ruta | Responsabilidad |
|---|---|
| `api.py` | Entrada, carga de datos, CLI y servidor HTTP anterior para las rutas `/api/v1/*` |
| `app.py` | Entrada ASGI paralela con FastAPI; reutiliza `queries.consultar` y el cargador existente |
| `queries.py` | Reglas deportivas y respuestas de consulta para equipos, jugadores y partidos |
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

Se hace con Docker Compose desde la raíz `app-real-jaen/`, que contiene los repositorios `dashboard/` y `api/`. La orquestación compartida está en `../compose.yaml`; cada servicio conserva su Dockerfile. Puesta en marcha: [../dashboard/README.md](../dashboard/README.md), «Puesta en marcha con Docker». Además:

- instalación, arranque, mantenimiento, copias y adquisición: [../dashboard/docs/docker.md](../dashboard/docs/docker.md);
- servidor del grupo: [../dashboard/docs/despliegue-servidor.md](../dashboard/docs/despliegue-servidor.md);
- contrato del botón: [../dashboard/docs/contrato-actualizaciones.md](../dashboard/docs/contrato-actualizaciones.md).

## Desarrollo local

Python 3.14 (las dependencias SQL exigen ≥ 3.11):

```sh
python3.14 -m venv .venv && .venv/bin/python -m pip install -r requirements-sql.txt   # Windows: .venv\Scripts\python
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python api.py --almacen sql --base RUTA/real-jaen.sqlite --ambito ambito.json --port 8001
```

El servidor ASGI se instala aparte y no sustituye el arranque anterior:

```sh
.venv/bin/python -m pip install -r requirements-fastapi.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8002
```

Por defecto lee la carpeta `datos/`, igual que `api.py`. Para SQLite, configura
`SPORTS_DATA_STORAGE=sql`, `SPORTS_DATA_DB` y opcionalmente `SPORTS_DATA_SCOPE` antes de arrancar; la base se carga
una vez y mantiene el bloqueo cooperativo durante la vida del proceso. En modo JSON, `SPORTS_DATA_DIR`,
`SPORTS_DATA_SCOPE` y `SPORTS_DATA_REPLACEMENTS` permiten seleccionar la carpeta, ámbito y archivo de sustituciones.
La prueba de paridad HTTP se ejecuta con `python -m unittest tests.test_fastapi_equivalence -v` después de instalar
`requirements-fastapi.txt`.

FastAPI también admite el control de actualizaciones de `control.py`, pero solo si se activa expresamente con
`SPORTS_DATA_CONTROL=1`, junto con `SPORTS_DATA_STORAGE=sql`, `SPORTS_DATA_DB` y `RJ_CONTROL_SECRET_FILE`. Conserva
la configuración interna ya usada por `control.py`: `RJ_ACQUISITION_URL`, `RJ_ACQUISITION_SECRET_FILE`,
`RJ_UPDATE_RECIPE` y, si corresponde, las tres restricciones `RJ_ACQUISITION_MAX_ATTEMPTS`,
`RJ_ACQUISITION_MAX_SENDS` y `RJ_ACQUISITION_DETAIL_ROUNDS`. Los ficheros de secretos deben montarse como secretos
del servicio y no dentro del repositorio. Sin `SPORTS_DATA_CONTROL=1`, las rutas de actualización no se habilitan.
El servicio de control utiliza `Coordinador` y `Actualizaciones` existentes; las consultas deportivas capturan una
sola generación por petición y ven publicaciones nuevas sin reiniciar FastAPI. Debe ejecutarse con un único worker
Uvicorn: la imagen fija `--workers 1`, y el bloqueo exclusivo de `Coordinador` impide que otro proceso abra la misma
base a la vez. No se debe ejecutar `control.py` y FastAPI contra la misma base simultáneamente.

El Compose estable no activa esta modalidad. Para probarla, usar una copia aislada de una base compatible, secretos
de prueba y un servicio de adquisición con proveedor fixture y red externa deshabilitada; nunca la base activa ni el
proveedor real. `RJ_ACQUISITION_TEST=1` solo es válido para una instalación marcada por el fixture de adquisición.

- Nunca sobre la base activa de una instalación: usar una copia (`persistencia copiar`, o un respaldo).
- El dashboard en desarrollo apunta a este puerto con `LOCAL_DATA_API_URL` (`../dashboard/.env.example`).
  Para usar el backend Docker en su lugar, `../compose.dev.yaml`.
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

Los Compose `docker/acquisition/compose.*.yaml` son pilas de prueba aisladas de adquisición; no son overlays de la instalación del dashboard. Sus scripts indican el comando y directorio de ejecución. La instalación normal usa únicamente `../compose.yaml` desde la raíz del workspace.

`test_integration.py` y `test_updates.py` reproducen las respuestas de la ejecución real del 28-10-2026: el bundle
sellado, su descriptor y su inventario, sin cookies ni secretos, en `docker/acquisition/fixtures/real-bundle-20261028.tar.xz`
(sumas en `.sha256`).

*Snapshot* (`--snapshot`): una copia inactiva dentro de una carpeta llamada `copias/`, nunca `real-jaen.sqlite` de
una instalación. Las pruebas trabajan sobre su propia copia y no la modifican.

- `test_integration.py` y `test_updates.py`: la base del respaldo de la instalación
  (`dashboard/scripts/respaldo-instalacion.sh` (invocado desde la raíz del workspace); comprobado con `respaldo-habitual-20261029`):

  ```sh
  (cd RESPALDO && sha256sum -c SHA256SUMS)
  mkdir -p ../pruebas/copias && cp RESPALDO/real-jaen.sqlite ../pruebas/copias/real-jaen.respaldo-AAAAMMDD.sqlite
  ```

- `test_group.py` simula la jornada 9 y necesita una base en la que aún no se ha jugado: la copia del 27-10
  `real-jaen.20261027-083912-846469.piloto-16599363.sqlite` (SHA256
  `ebe99c6d305db4864b7a316244e60e87360e314e7f71dd1108563530a970e5ca`), que va con la entrega
  (`../dashboard/docs/despliegue-servidor.md`, «Entrega»). Los respaldos posteriores al 29-10 no sirven.

*Imagen base del scraper* (`rj-scraper-package-efcadb386d:candidate`, con Chromium): se carga de `imagenes.tar.gz`
de la entrega. Construirla de nuevo desde la revisión fijada (`docs/scraper-empaquetado-linux.md`: clon del original,
commits propios desde `docker/scraper/real-jaen-follow-next-pages.bundle`, `export_source.py` y `docker buildx
build`) necesita red hacia PyPI, Debian y la descarga de Chromium, y da otra imagen: hay que repetir entonces las
pruebas y `test_offline.py` antes de usarla.

Imágenes de prueba, sin red, desde un `git archive` de esta revisión:

```sh
docker build --network none --pull=false --target test \
  --build-arg PROFILE_RUNTIME=rj-scraper-package-efcadb386d:candidate \
  --build-context implementation=./acquisition --build-context backend=. --build-context checks=./tests/acquisition \
  -f docker/acquisition/Dockerfile -t rj-acq-portable:test docker/acquisition
docker build --network none --pull=false --target backend-test --build-arg BACKEND_IMAGE=rj-analyst-backend:2c4008b \
  --build-context backend=. --build-context fixtures=./tests --build-context checks=./tests/acquisition \
  -f docker/acquisition/Dockerfile.integration -t rj-backend-portable:test docker/acquisition
python -X utf8 docker/acquisition/test_integration.py --acquisition-image rj-acq-portable:test \
  --backend-image rj-backend-portable:test --snapshot ../pruebas/copias/real-jaen.respaldo-AAAAMMDD.sqlite \
  --evidence CARPETA-NUEVA
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
