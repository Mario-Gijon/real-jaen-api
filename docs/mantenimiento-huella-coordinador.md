# Transición explícita de la huella del coordinador

Este procedimiento es una migración administrativa puntual para la instalación que conserva la generación de datos
deportivos en Alembic `0004`. No es adopción automática, no declara versiones históricas compatibles y no se ejecuta
al arrancar FastAPI.

## Diagnóstico confirmado

Hay dos SQLite de trabajos bajo `<base>.actualizaciones/`; no deben confundirse:

| Ruta | Tablas | Significado |
| --- | --- | --- |
| `jobs.sqlite` | `trabajo`, `confirmado` | Journal del coordinador deportivo. Conserva los trabajos, el ámbito, la generación publicada, la descripción y la huella de código confirmadas. |
| `control/jobs.sqlite` | `job` | Historial de solicitudes HTTP e idempotencia de `control.py` (`id`, `idem`, `state`, `data`). No es el journal del coordinador ni sustituye su confirmación. |

La confusión anterior se produjo al inspeccionar el segundo archivo como si fuera el primero. La instalación habitual
usa el formato actual `trabajo`/`confirmado` para el coordinador; no requiere conversión de tablas ni combinación de
historiales. Los dos journals y sus identificadores se conservan sin reescritura. Si el `jobs.sqlite` de la raíz tiene
otro esquema, el procedimiento se detiene: no convierte `job` de manera heurística.

Las huellas tienen funciones distintas:

| SHA-256 | Interpretación acreditada |
| --- | --- |
| `177422701165f3a83bf14f22d537dbab08cd8bd96a560758ce40ec9782130a58` | Huella conservada en resúmenes de importaciones anteriores. El registro local de mantenimiento del 3-11-2026 la identifica como origen de una transición previa a `8d1b`; no es la huella confirmada por el coordinador actual. |
| `8d1b586793ac2495ebf2bebe9cace154f8caa9ae4641199c38ca181fa03ca0d4` | Huella del coordinador y de algunas importaciones de la imagen API basada en `f9c3b34`. Es la huella confirmada que bloquea el código actual. |
| `37038df67dc1c5898a51ffd3efe216d88d43508e16dbede291f3be2c537d0720` | Huella de las reglas/carga/persistencia de API en el commit `0f5d69e7b039cc1eb8261d8271cdffa78c3e5e92`. |

La comparación del árbol `f9c3b34..0f5d69e` dentro de los archivos cubiertos por la huella muestra la incorporación de
capturas, selección y lectura de comentarios; no cambia `api.py`, `queries.py`, `importar.py`, la carga deportiva ni la
sincronización existente. Las migraciones `0005`, `0006` y `0007` crean o amplían únicamente tablas de comentarios.
La herramienta, sus pruebas y esta documentación quedan fuera de `persistencia/` y de la lista hasheada por
`carga.archivos_de_codigo()`. La lista `REFERENCIAS_CODIGO_COMPATIBLES` no se amplía.

Esta conclusión permite una adopción explícita y limitada; no prueba que cualquier huella futura sea compatible. La
herramienta fija las dos huellas, el ámbito, el código actual y la cadena de migración. Si alguno cambia, se detiene.

## Procedimiento

No ejecutar hasta planificar el despliegue. Antes de usarlo, detener los procesos que abren la base, el coordinador,
el control HTTP y acquisition; verificar cero solicitudes y trabajos activos, conservar la copia aislada y comprobar
un respaldo externo de los tres volúmenes (estado deportivo, trabajos de acquisition y perfil). No reutilizar ni tocar
el proyecto de desarrollo habitual durante una futura operación aislada.

La herramienta hace lo siguiente en un único proceso de mantenimiento:

1. Toma `bloquear(base)` y exige que el coordinador esté libre, que `trabajo`/`confirmado` sean los únicos registros
   tabulares del journal deportivo, que no haya trabajos activos ni fases `publishing`/`recovering`, y que la
   descripción confirmada coincida con SQLite. Comprueba también el journal HTTP, su idempotencia, los hashes de
   importación reconocidos, el ámbito y la generación.
2. Antes de escribir en estado, crea en el directorio externo solicitado una copia privada y con manifiesto. Usa la
   API de backup SQLite para la base y ambos journals; copia además el árbol de evidencias, candidatas, generaciones y
   `copias/` sin seguir enlaces simbólicos. No copia ni elimina archivos `.lock` ni modifica el original. El manifiesto
   incluye integridad, descripciones, inventarios, huellas lógicas y hashes de los archivos guardados.
3. Actualiza la base a `0007` mediante `sincronizacion.migrar()`, que mantiene su copia previa. Compara esquema, filas y
   valores de todas las tablas existentes en `0004`; únicamente acepta las tres tablas de comentarios nuevas. Si la
   migración fue interrumpida en `0005` o `0006`, permite reanudar esa cadena solo si sus tablas nuevas siguen vacías.
4. Bajo el mismo bloqueo vuelve a comprobar el alcance, el journal, los trabajos y las tablas deportivas. En una
   transacción `BEGIN IMMEDIATE` cambia solo `confirmado.detalle.codigo` y `confirmado.detalle.descripcion`. Mantiene
   el ámbito, el UUID de generación y cualquier otro campo. No modifica ninguna fila de `trabajo`, `control/job`, ni
   `import_run.summary`.
5. Registra un recibo privado al lado del respaldo. Tras liberar el bloqueo, abre y cierra el `Coordinador` normal. No
   crea solicitudes, no ejecuta acquisition y no accede a SofaScore.

La herramienta es idempotente mientras el mismo respaldo siga siendo el origen y no se haya aceptado trabajo nuevo.
Si el proceso termina después de la migración o del `COMMIT` pero antes del recibo, mantener los servicios detenidos y
repetir el mismo comando con el mismo directorio y confirmación. El procedimiento rechaza un directorio incompleto,
una huella desconocida, un journal distinto, una copia editada o un historial que haya cambiado.

Ejemplo dentro de un contenedor efímero de mantenimiento preparado con la imagen API revisada, red `none`, rootfs de
solo lectura, UID `10001`, volumen deportivo montado en `/state` y un volumen/directorio de respaldo externo en
`/backups`:

```sh
python -B /app/scripts/adopt_coordinator_fingerprint.py adopt \
  --base /state/real-jaen.sqlite \
  --scope /app/ambito.json \
  --backup-dir /backups/real-jaen-coordinator-transition-<identificador> \
  --confirm 'adopt coordinator fingerprint 8d1b586793ac2495ebf2bebe9cace154f8caa9ae4641199c38ca181fa03ca0d4 -> 37038df67dc1c5898a51ffd3efe216d88d43508e16dbede291f3be2c537d0720'
```

El código devuelve éxito únicamente tras abrir/cerrar el coordinador. Revisar también `manifest.json`, `adoption.json`,
revisión `0007`, `persistencia verificar`, huellas de importación, inventario deportivo y tres trabajos anteriores antes
de volver a habilitar los servicios. Un cambio de revisión distinto, `cambios_en_seco`, generación distinta, trabajo
activo, discrepancia de descripción o pérdida de evidencia obliga a detenerse.

## Recuperación

La copia externa es la fuente de recuperación de la pareja base+journal. Mantener los servicios apagados. Verificar su
`manifest.json`; ejecutar el subcomando `restore` con la misma base, ámbito y frase exacta, que incorpora el SHA del
manifiesto:

```sh
python -B /app/scripts/adopt_coordinator_fingerprint.py restore \
  --base /state/real-jaen.sqlite \
  --scope /app/ambito.json \
  --backup-dir /backups/real-jaen-coordinator-transition-<identificador> \
  --confirm 'restore coordinator snapshot <manifest_sha256>'
```

`restore` primero guarda el estado posterior en `post-maintenance-state/`, prepara una copia validada, conserva el
estado que va a sustituir en un directorio `.<base>.preserved-before-restore-*`, y restaura la base `0004` con el árbol
`<base>.actualizaciones/` del mismo snapshot. Mantiene el archivo `.lock` y no reemplaza el archivo append-only
`copias/`; por eso las copias creadas durante la migración permanecen disponibles. Repetir el mismo comando después de
una interrupción completa la restauración o reconoce que ya se completó. Tras verificar integridad, claves foráneas,
generación, confirmación y ambos historiales, volver a la imagen API anterior que corresponde a la huella `8d1b`.
No arranque la imagen actual con la base restaurada: la protección de huella la rechazará correctamente.

Esta instantánea interna cubre la base, el coordinador, el historial HTTP y sus artefactos del volumen deportivo.
No sustituye el respaldo operativo separado de los volúmenes de acquisition y del perfil del navegador; el
procedimiento no los modifica ni los restaura.
