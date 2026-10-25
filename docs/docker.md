# Backend en Docker

La composición se mantiene en el repositorio hermano **rj-analyst/compose.yaml**; procedimiento
canónico, variables, instalación, arranque, parada, logs, copias y mantenimiento:
`../rj-analyst/docs/docker.md`. Cada repositorio conserva su Dockerfile; no se mezclan arquitecturas.

Esta imagen usa Python 3.14.4 slim-bookworm fijado por digest y las versiones SQL exactas de
requirements-sql.txt. Solo copia código, ámbito y configuración Alembic, nunca .env.local, SQLite,
exportaciones, Git ni entornos del host. Ejecuta como 10001:10001. No se añade otro servidor HTTP.

El CMD explícito sirve SQL desde `/state/real-jaen.sqlite`, con `/app/ambito.json` y escucha
`0.0.0.0:8001` dentro de Compose. **El valor predeterminado de api.py en Windows sigue siendo
127.0.0.1**; `--host` solo configura la interfaz de red, sin cambiar rutas ni contratos.
El backend no se publica al host y el healthcheck usa el /health existente. No hay migraciones al
arrancar ni recarga en caliente. El código sin .git conserva su huella de archivos como procedencia
(`git:desconocido+sha256:…`); no se presenta como un checkout limpio de un commit Git.

El volumen externo `/state` contiene la base, su .lock, WAL/SHM/journal y copias/. Todas las
operaciones apuntan a la misma ruta; el frontend no monta ese volumen. `/imports` contiene solo
copias/entradas elegidas, en modo solo lectura; no es el directorio activo Windows.

Inicialización explícita, solo para destino ausente y snapshot verificado en revisión actual,
journal DELETE y sin diarios; el script prepara una copia privada, verifica y publica atómicamente:

```
python -B -m scripts.initialize_snapshot /imports/snapshot.sqlite /state/real-jaen.sqlite
```

El snapshot debe haberse producido con el mecanismo existente de copia consistente. No se migra
una copia de revisión antigua automáticamente: se prepara/migra/verifica **otra copia** antes.
Rechaza sobrescribir un destino existente. Pruebas: tests/test_container_setup.py, bases temporales.

Secuencia para cualquier mantenimiento: **parar backend → importar/migrar/decisiones/reconstruir/
restaurar → verificar → arrancar backend**. Compose run --rm --no-deps reutiliza imagen, usuario,
volumen y ruta. El ENTRYPOINT es python -B: `run backend -m persistencia …`.
Copiar también reserva la base: cerrar primero la API. No borrar el archivo .lock, WAL o SHM.
Una restauración sigue el procedimiento seguro existente y conserva copia previa. El bloqueo es
cooperativo: programas externos que abran SQLite no lo respetan necesariamente.

Validación Linux sobre **el propio almacenamiento persistente**, como usuario normal, antes de
arrancar la API de esa base:

```
python -B -m scripts.check_container --base /state/real-jaen.sqlite
```

El script crea bases privadas temporales dentro de /state; prueba permisos, flock y rutas symlink,
API en un puerto libre, rechazo de mantenimiento, salida normal, SIGKILL, rollback de escritura WAL,
operaciones posteriores y fallo de restauración. Registra plataforma, uid, SQLite y montaje. Nunca
modifica el contenido de la base de entrada; la copia inicial exige que esa base no esté reservada.
No equivale a validar Linux si se ejecutan solamente los unittest de Windows.

Validacion Docker Linux ejecutada el 25-10-2026: ambas imagenes construidas y ambos servicios
healthy en el proyecto temporal rj-check-50bca3bcd6b2. Backend UID 10001, SQLite 3.40.1,
/state en /dev/sdd ext4 de Docker Desktop. Pasan permisos, flock entre procesos, symlinks,
rechazo de mantenimiento con API activa, liberacion normal/SIGKILL, rollback WAL y restauracion
fallida sin cambios parciales. Mantenimiento posterior y recreacion conservan la huella logica.
Frontend real revisado a 1440/390 px (13 capturas). Informe completo en la documentacion canonica.
El volumen de prueba etiquetado queda conservado; contenedores y red de prueba retirados.
La muestra procede de una copia anterior 0002, migrada/verificada solo en temporal hasta 0004;
no acredita importaciones posteriores. Base activa y servicios Windows intactos. Sin commit ni push.
Linux y symlinks validados en este volumen ext4; otros almacenamientos, NTFS compartido,
coordinacion Windows/Linux sobre una misma base y caida completa de VM/host siguen sin validar.
