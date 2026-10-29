# Empaquetado offline del scraper y Chromium

> Estado 29-10-2026: integrado. La imagen de este empaquetado es la base (`RJ_SCRAPER_RUNTIME_IMAGE`) de la imagen de
> adquisición (`docker/acquisition/Dockerfile`, overlay `rj-analyst/compose.acquisition.yaml`). El fallo de sandbox
> de los resultados del 26-10 se resolvió con el perfil seccomp `docker/acquisition/seccomp-linux-amd64.json`
> ([diagnostico-sandbox-linux.md](diagnostico-sandbox-linux.md)). Estado vigente: [adquisicion-sofascore.md](adquisicion-sofascore.md).
> Los apartados de resultados y decisión conservan lo medido el 26-10-2026.

## Fuente y dependencias

Fuente exclusiva: sofascore_scraper 3.1.0, commit
611e436a1528b0f487aa56a045f023e8ae8ae098, que incluye paginacion next y fichas (apartado siguiente).
export_source.py usa git archive de ese objeto, nunca copia el working tree. Lista permitida:
pyproject.toml, requirements.txt, constraints.txt, LICENSE, locales/ y sofascore_scraper/.
No exporta frontend/, package-lock.json, .git, .venv, configuracion local ni datos. El cambio
ajeno frontend/package-lock.json del checkout instalado permanece intacto y fuera de la imagen.
SOURCE_REVISION, SHA256 del archivo Git y SOURCE_FILES.sha256 permiten verificar la entrega;
el build exige la revision exacta y comprueba cada archivo. Metadatos con LF tambien al
exportar desde Windows; una salida existente se rechaza, nunca se sobrescribe.

Imagen de ensayo con Python 3.14.4 slim-bookworm fijado por digest multi-arquitectura, el mismo
runtime base del backend. Instala requirements/constraints del scraper y requirements-sql.txt
EN EL MISMO interprete para detectar incompatibilidades; pip check forma parte del build y
la prueba. No se instala ni construye la UI del scraper. Se conserva su arbol de fuente y
pyproject/locales en /opt/scraper porque su version/recursos se resuelven desde ahi; el CLI
se ejecuta como python -m sofascore_scraper.cli.main. No necesita instalacion editable del host.

El build instala Chromium mediante patchright con sus bibliotecas de sistema. Solo descarga
dependencias/binarios desde sus registros, no datos SofaScore. runtime-python.txt y
runtime-system.txt quedan en /opt para auditar las versiones realmente instaladas. Los paquetes
apt no estan fijados a un repositorio snapshot: aun con la base y revision fijadas, no se
promete reconstruccion bit a bit entre fechas. La imagen ya construida se identifica por ID.
No se fija arquitectura de CPU; esta ejecucion es Linux x86_64, otras requieren validacion.

## Parches propios del scraper (bundle)

La revision fijada no esta en el repositorio original: son dos commits propios sobre `main` del original
(`dd9f061`, https://github.com/tunjayoff/sofascore_scraper):

- `c697852` fix(follow): lee todas las paginas de proximos partidos de un equipo;
- `611e436` feat(players): descarga y exporta las fichas individuales de jugadores.

Se conservan en `docker/scraper/real-jaen-follow-next-pages.bundle` (rama `real-jaen/follow-next-pages`; requiere
`dd9f061`). Un bundle conserva los identificadores, que `export_source.py` exige; aplicar los cambios como parche
daria otros. Recuperacion en un clon del original, sin la rama local:

```sh
set -eu
git clone --single-branch --branch main https://github.com/tunjayoff/sofascore_scraper.git ../sofascore_scraper/sofascore_scraper
git -C ../sofascore_scraper/sofascore_scraper bundle verify "$PWD/docker/scraper/real-jaen-follow-next-pages.bundle"
git -C ../sofascore_scraper/sofascore_scraper fetch "$PWD/docker/scraper/real-jaen-follow-next-pages.bundle"   real-jaen/follow-next-pages:real-jaen/follow-next-pages
python3 docker/scraper/export_source.py ../sofascore_scraper/sofascore_scraper ../packaging-scraper/source
```

Comprobado el 29-10-2026 en un clon que solo tenia `main`: la exportacion resultante es identica a la del checkout
original (SHA256 del archivo Git `19c53bf0b9aaa9716fa789566c78c4bf1c95b45226e5b42f67e48089170c1f74`, 155 archivos).
Si el original reescribiera `main` y `dd9f061` dejara de existir, el bundle no basta: conservar entonces un bundle
completo (`git bundle create ... real-jaen/follow-next-pages`, unos 37 MB) junto a las copias de la instalacion.
Al cambiar los parches: nuevo commit en esa rama, nuevo bundle y nueva `REVISION` en `export_source.py`.

## Reproduccion portable (POSIX, sin PowerShell)

Requisitos de preparacion: Git, Python >=3.12 para exportar, Docker Engine Linux y BuildKit
con contextos adicionales, acceso a registros/PyPI/CDN de Chromium durante build, espacio para
el navegador y sus bibliotecas. No se arranca Docker Desktop ni se cambia el motor desde scripts.
El host de ejecucion de una imagen ya construida solo necesita Docker y una shell POSIX.

Desde la raiz api-real-jaen, con el checkout del scraper disponible solo para exportar:

```sh
set -eu
# Elegir un directorio NUEVO fuera de los repositorios para las fuentes/evidencias.
mkdir -p ../packaging-scraper
python3 docker/scraper/export_source.py ../sofascore_scraper/sofascore_scraper ../packaging-scraper/source

docker version
docker buildx build --load --tag rj-scraper-offline:611e436a \
  --build-context backend=. --build-context scraper=../packaging-scraper/source \
  --file docker/scraper/Dockerfile docker/scraper
sh docker/scraper/validate.sh rj-scraper-offline:611e436a ../packaging-scraper/evidence
```

Cambiar las rutas relativas de exportacion segun la entrega, nunca Dockerfile ni imagen.
No usar el checkout sucio como contexto scraper. .dockerignore del contexto de prueba solo
permite Dockerfile y offline_probe.py. El contexto backend usa su allowlist y aporta solo
requirements-sql.txt a esta imagen. No enviar la base activa ni snapshots para estas pruebas.

El validador POSIX rechaza volumen existente; crea uno exclusivo etiquetado, comprueba
identidad y prepara permisos con un contenedor root temporal y solo CHOWN. Los probes
trabajan como UID/GID 10001 con --network none, --cap-drop ALL,
--security-opt no-new-privileges:true, --init y /dev/shm de 512 MiB. No hay puertos, socket
Docker, codigo montado ni acceso a /state. El unico volumen es /acquisition: datos de prueba,
configuracion, perfil, marcadores e informes. No es un volumen de datos deportivos.

Pruebas ordenadas: dependencias/CLI/Store SQL; recrear contenedor y leer marcador de perfil;
arranque de navegador sobre servidor HTTP local dentro del propio contenedor; persistencia
de cookie entre contenedores; cierre normal y SIGTERM con recogida de hijos. Un fallo detiene
las siguientes fases. El script conserva volumen e informes y retira solo sus contenedores;
no limpia recursos ajenos. En esta maquina se ejecutaron los comandos Docker equivalentes
desde la herramienta Windows; la sintaxis del validador POSIX se comprobo dentro de Linux.
No se presenta como una ejecucion integral de ese script en el servidor del grupo.

## Protecciones del navegador y alcance de la prueba

El puente instalado no pasa chromium_sandbox=True ni ignore_https_errors=False. Scrapling
admite por defecto opciones permisivas (entre ellas ignore_https_errors=True). No se ejecuta
una sesion upstream sin protecciones para intentar que pase. Esta prueba inyecta una fabrica
LOCAL al proceso de ensayo que exige sandbox, valida TLS, no concede permisos y elimina
flags permisivos de lanzamiento. No modifica fuente upstream ni integra un adaptador real.
El resultado no equivale a certificar el puente sin cambios de configuracion.

Las constantes de pagina/probe y consulta de cookie se redirigen a un servidor loopback con
HTML/JSON fijo. No resuelve CAPTCHA ni hace consultas reales. network none impide egress;
se deniegan tambien peticiones de pagina fuera del origen local. No hay fallback con
--no-sandbox, privileged, SYS_ADMIN o seccomp desactivado. El informe incluye politica de
lanzamiento, procesos durante/cierre, cookie y estado chrome://sandbox si se llega a abrir.

## Resultados propios (26-10-2026)

Imagen candidata: rj-scraper-package-efcadb386d:candidate.
ID informado por Docker: sha256:e13cef0eff2f0d87e8023f149eebf1fdd2682613a4788611c3f9c1efcb856b62.
Fuente verificada: 155 archivos exportados del commit fijado, sin frontend/package-lock.json.

| Comprobacion | Resultado |
|---|---|
| Build Linux y manifiesto | Correctos; revision y SHA256 comprobados |
| Dependencias conjuntas | pip check correcto; SQLAlchemy 2.1.6, Alembic 1.20.1, greenlet 3.5.6, typing_extensions 4.16.0 |
| Scraper y navegador instalado | CLI 3.1.0; Scrapling 0.4.15, patchright/playwright 1.63.0; Chromium 153.0.8010.12, revision 1243 |
| Usuario y Store | UID 10001, SQLite 3.40.1; creacion/cierre de Store de prueba y consulta SQL en memoria correctos |
| Perfil persistente (filesystem) | Escritura correcta; marcador recuperado tras recrear contenedor con el mismo volumen |
| Arranque protegido | FALLA antes de solicitar contenido: No usable sandbox / Chromium sandboxing failed |
| Diagnostico de aislamiento | CapEff=0, NoNewPrivs=1, Seccomp=2; unshare(CLONE_NEWUSER) devuelve EPERM, pese a existir cuota de namespaces |
| Cierre tras fallo | Chromium abortado y driver cerrados; children_after_close vacio; sin hijos propios restantes |
| Contenido local y cookies Chromium | NO validados: el navegador no llego a cargar la pagina local (local_requests vacio) |
| Cierre de sesion funcional y SIGTERM | NO ejecutados: se detuvo la secuencia al fallar el arranque |

Actualizacion de diagnostico: docs/diagnostico-sandbox-linux.md demuestra una denegacion
concreta en el perfil OCI; los filtros ancestros/kernel siguen pendientes. El primer ensayo
solo identificaba falta de sandbox usable; ahora hay una regla impeditiva comprobada. No se cambiaron protecciones, kernel, permisos del motor o servicios para salvarla.
No se han probado fallos de SofaScore ni aceptacion del navegador por la IP del grupo.

Evidencias fuera de Git en el directorio temporal rj-scraper-package-efcadb386d: fuente exportada,
logs de build/probes, JSON de resultados/configuracion, diagnostico y listas de dependencias.
El volumen exclusivo rj-scraper-package-efcadb386d-profile se conserva etiquetado para inspeccion;
los contenedores de prueba se retiran. No hay datos ni binarios de navegador en Git.

## Decision antes de integrar

No hay evidencia de conflicto de dependencias que obligue a separar un servicio: los paquetes
SQL y scraper se instalaron y ejecutaron juntos. SI hay una incompatibilidad de entorno con
el sandbox protegido que bloquea la integracion. El puente sin adaptar tambien requiere
configuracion explicita de protecciones; no debe heredarse sin revisar sus defaults.

No relajar el perfil del backend para alojar Chromium. Si el servidor del grupo necesita una
politica de namespaces/sandbox distinta, debe aprobarse y validarse en un entorno de navegador
separado con aislamiento propio; esto justifica evaluar un servicio de adquisicion dedicado.
Separar un contenedor con las mismas restricciones NO arregla automaticamente el problema.
Primero validar soporte de sandbox con el administrador y repetir estas pruebas locales. No se
crea ese servicio ni se cambia la arquitectura en esta entrega. La candidata queda preparada,
pero su navegador NO esta validado para produccion; coordinador y actualizacion real siguen desconectados.

Prueba posterior de excepcion seccomp aprobada: unshare(CLONE_NEWUSER) pasa, pero Chromium
sigue sin sandbox utilizable. No se ampliaron permisos y se detuvo la secuencia. Perfiles
y diferencia exacta conservados en docker/scraper/policies/; resultados y propuesta de
adquisicion separada sin /state en docs/diagnostico-sandbox-linux.md.
