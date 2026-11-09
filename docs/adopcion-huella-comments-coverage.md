# Adopción acotada de la corrección de cobertura de comentarios

Este procedimiento corresponde únicamente a la pareja de huellas de código del validador de cobertura de comentarios:

- origen: `4f5ba2ff054951cdcb2ce04f971e8b1e36c57d6d4eb73a4f5c69e44f5d8e6a1b` (`ce27eaf8d1136f5598c8bf4ba57e8a7a4bafcd0c`)
- destino: `376f2cdc92f351ab9fa7056eb584a0e3813f1cf61fe0d9a6b362a996ce7cc7fe` (`a76af75c1cbf651feb363184251266baf9f75dd8`)
- revisión Alembic: `0008`
- generación autorizada: `e07d528a-2f19-47e7-ae28-30f09b285042`
- ámbito: `a791c80eb18cf028f3af658a2dfe19d954168e2c4b28810c5e5d73a45980f164`

El diff productor contiene solo `persistencia/bundle.py`. Cambia cómo se verifica la elegibilidad de comentarios según el estado del partido reportado por el detalle o el listado del Grupo 2. No cambia el esquema, las reglas de normalización deportiva, la identidad de entidades ni los datos persistidos. Las 40 rutas cubiertas y el algoritmo SHA-256 agregado siguen el formato existente. Este procedimiento no amplía `REFERENCIAS_CODIGO_COMPATIBLES` y no es un mecanismo general de adopción.

## Precondiciones

1. Ejecutar desde la imagen Sports Data construida desde el código destino revisado. La herramienta comprueba que su huella agregada coincide exactamente con el destino anterior.
2. Mantener detenidos frontend, Sports Data y Acquisition durante `prepare`, `adopt` y la comprobación inicial. No basta con que no haya un trabajo activo: se deben impedir nuevas solicitudes.
3. Usar rutas exactas para la SQLite, ámbito y journal de Acquisition. La herramienta comprueba revisión, descripción, huella, generación, trabajos de los tres journals, claves foráneas, integridad, sincronización en seco y acompañantes SQLite.
4. Crear un destino de respaldo nuevo y privado, fuera de ambos volúmenes y del repositorio. Debe haber espacio para las cuatro copias SQLite y para los árboles de evidencias, historiales y artefactos.
5. Conservar una fuente de runtime del commit `ce27eaf8d1136f5598c8bf4ba57e8a7a4bafcd0c`. La herramienta vuelve a calcular su huella antes de permitir una reversión.

## Preparar y revisar el respaldo

Los ejemplos siguientes se ejecutan dentro de un contenedor efímero sin red, con la imagen destino y montajes solo para la instalación y el directorio privado de respaldo. No usar `docker compose up` para esta tarea: la dependencia de Sports Data podría arrancar Acquisition. No montar perfiles ni secretos.

```sh
python -B /app/scripts/adopt_comments_coverage_fingerprint.py prepare \
  --base /state/real-jaen.sqlite \
  --scope /app/ambito.json \
  --acquisition-journal /acquisition-jobs/jobs.sqlite \
  --backup-dir /backups/comments-coverage-4f5ba-to-376f-<id>/snapshot
```

Detenerse si cualquier valor de origen difiere. Revisar `manifest.json` y el resultado `prepared`: debe identificar la generación y revisión fijadas y dar firmas para SQLite deportiva, coordinador, control HTTP y Acquisition. El paquete también conserva los artefactos y evidencias de `/state` y `/acquisition-jobs`. La copia usa SQLite Backup API; el original y sus WAL/SHM no se sustituyen. No continuar con un paquete parcial.

## Adoptar

```sh
python -B /app/scripts/adopt_comments_coverage_fingerprint.py adopt \
  --base /state/real-jaen.sqlite \
  --scope /app/ambito.json \
  --acquisition-journal /acquisition-jobs/jobs.sqlite \
  --backup-dir /backups/comments-coverage-4f5ba-to-376f-<id>/snapshot \
  --confirm 'adopt coordinator fingerprint 4f5ba2ff054951cdcb2ce04f971e8b1e36c57d6d4eb73a4f5c69e44f5d8e6a1b -> 376f2cdc92f351ab9fa7056eb584a0e3813f1cf61fe0d9a6b362a996ce7cc7fe generation e07d528a-2f19-47e7-ae28-30f09b285042 services-stopped'
```

La única escritura funcional es una transacción `BEGIN IMMEDIATE` que cambia `confirmado.detalle.codigo`. La base deportiva, `descripcion`, generación, ámbito, trabajos e historiales se vuelven a comparar con el snapshot. Antes de probar la apertura, se guarda en el directorio del respaldo un recibo privado de adopción pendiente. Si el proceso se interrumpe después del cambio de metadatos, ese recibo permite que `restore` verifique el estado exacto y revierta; el recibo solo se marca como verificado después de abrir la generación con el coordinador destino. Si el recibo provisional no pudiera persistirse, la herramienta solo revierte a la huella origen después de volver a demostrar que todos los demás datos siguen coincidiendo con el snapshot. No hay migración Alembic ni acceso al proveedor.

## Comprobación y recuperación

Con los tres servicios aún detenidos, iniciar solo Sports Data con la imagen destino usando el servicio Compose con `--no-deps`; no iniciar el frontend ni Acquisition. Confirmar salud y consultas GET antes de reactivar el resto. No aceptar un trabajo nuevo hasta decidir que la actualización corregida está lista.

Si la comprobación falla antes de aceptar cualquier solicitud o trabajo posterior, detener de nuevo Sports Data y ejecutar, desde la imagen destino, con el runtime antiguo montado en solo lectura:

```sh
python -B /app/scripts/adopt_comments_coverage_fingerprint.py restore \
  --base /state/real-jaen.sqlite \
  --scope /app/ambito.json \
  --acquisition-journal /acquisition-jobs/jobs.sqlite \
  --backup-dir /backups/comments-coverage-4f5ba-to-376f-<id>/snapshot \
  --source-runtime-root /source-runtime \
  --confirm 'restore coordinator fingerprint 376f2cdc92f351ab9fa7056eb584a0e3813f1cf61fe0d9a6b362a996ce7cc7fe -> 4f5ba2ff054951cdcb2ce04f971e8b1e36c57d6d4eb73a4f5c69e44f5d8e6a1b generation e07d528a-2f19-47e7-ae28-30f09b285042 services-stopped'
```

`restore` se niega si cambió la SQLite, la generación, el ámbito o cualquiera de los tres historiales; si hay trabajos posteriores, no se debe forzar ni usar esta reversión. El runtime antiguo debe tener la huella origen exacta y abrir la generación restaurada. Si el runtime antiguo falla, la herramienta vuelve a TARGET únicamente después de comprobar de nuevo, bajo los bloqueos, que la réplica publicada, la generación y todos los historiales siguen idénticos al snapshot; si no puede acreditarlo, deja SOURCE y se detiene para inspección manual. Los respaldos y recibos se conservan.

## Para futuros cambios compatibles

Una nueva modificación de código requiere primero revisar la lista exacta de archivos que contribuyen a la huella, el diff semántico y el esquema. Se debe crear un procedimiento versionado para la pareja exacta de huellas revisada, probar `prepare/adopt/restore` con SQLite y journals temporales 0008, publicar y construir la imagen destino, y solo después abrir una ventana autorizada con respaldo fresco. No se deben agregar hashes antiguos a la lista de compatibilidad ni editar manualmente el journal.
