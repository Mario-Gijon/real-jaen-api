# Adopción explícita de la huella de lotes secuenciales

Este mantenimiento está limitado a la transición exacta del coordinador `8ed0a15a…` → `993e1018…` para la
instalación `rj-analyst-dev`. No es una regla general de compatibilidad y no se ejecuta al iniciar FastAPI. La
herramienta `scripts/adopt_batches_fingerprint.py` no ejecuta Alembic, no cambia productores aprobados y solo
actualiza `confirmado.detalle.codigo` después de verificar y respaldar el estado.

## Revisión del cambio

El destino es la huella agregada exacta incluida en la imagen API del commit `61c87a50143f6f101a5f3d17cd465890dbfc7dcc`.
La comparación con la imagen anterior `a4368ba` muestra cambios en `control.py` y `persistencia/adquisicion.py`:

- `control.py` continúa solicitudes de Grupo 2 mediante lotes secuenciales, con un máximo finito de 64 lotes, las
  protecciones del transporte, límite de progreso y el presupuesto de cada lote existente.
- `persistencia/adquisicion.py` agrega metadatos privados del plan ejecutado para decidir si se permite continuar.
  Esos campos no cambian el bundle deportivo ni la interpretación de los recursos persistidos.
- No cambian importador, esquema Alembic 0007, identidad de entidades, cálculo deportivo ni sincronización de datos.

La huella cubre todo `persistencia/`, por eso ese cambio interno requiere adopción explícita aunque no cambie las
reglas deportivas. El ámbito debe tener SHA-256
`a791c80eb18cf028f3af658a2dfe19d954168e2c4b28810c5e5d73a45980f164` y la base debe estar en Alembic `0007`.
El script detiene la operación si el código cargado, ámbito, revisión, generación, descripción, SQLite, journal,
historial o reglas de sincronización no coinciden exactamente.

## Ejecución aprobada

Antes del mantenimiento: comprobar que no haya trabajos activos en los tres servicios; detener ordenadamente solo
`rj-analyst-dev` para liberar el bloqueo; conservar las tres imágenes, la configuración local y todos los volúmenes.
Crear un directorio de respaldo nuevo, privado y externo al volumen deportivo. La herramienta, dentro de
`bloquear(base)`, toma una copia coherente SQLite de la base y ambos journals, conserva procedencia, generaciones,
evidencias e historial, calcula hashes del payload y genera un manifiesto privado. No sustituye ni elimina archivos
de origen.

Ejecutar el script dentro de la imagen candidata `rj-analyst-dev-sports-data:61c87a5`, con red desactivada y el
estado montado únicamente en el contenedor efímero de mantenimiento. Primero `prepare` valida el estado y escribe un
snapshot nuevo sin modificar la base ni los journals; revisar su manifiesto antes de seguir con `adopt` sobre esa
misma ruta:

```sh
python -B /app/scripts/adopt_batches_fingerprint.py prepare --base /state/real-jaen.sqlite --scope /app/ambito.json --backup-dir /backups/batches-8ed0-to-993e-<identificador>
```

```sh
python -B /app/scripts/adopt_batches_fingerprint.py adopt \
  --base /state/real-jaen.sqlite \
  --scope /app/ambito.json \
  --backup-dir /backups/batches-8ed0-to-993e-<identificador> \
  --confirm 'adopt coordinator fingerprint 8ed0a15a88a399cfb78f0e20f240f5196ff10caead77532a48d2de5c654e8cd7 -> 993e1018b66665a9c1f61cb980893d91a500bd4e852607554b5f3823e1143e2d'
```

El respaldo debe estar fuera de `/state`, con directorio padre privado. El procedimiento solo adopta `codigo`; deja
intactos `descripcion`, generación confirmada, todas las filas deportivas, `trabajo`, historial HTTP, comentarios,
originales, huellas de importación y copias append-only. Tras la escritura vuelve a ejecutar la verificación de
sincronización y abre/cierra el coordinador normal bajo el código nuevo.

Antes de arrancar servicios, verificar el `manifest.json` y `adoption.json`, `integrity_check`, claves foráneas,
Alembic `0007`, huella semántica e inventario deportivo, generación y ambos historiales. El coordinador debe seguir
sin trabajos activos. Solo entonces arrancar el proyecto con el overlay local `compose.dev.batches.yaml` y verificar
los healthchecks de Sports Data, Acquisition y Next.js. No abrir ni activar el botón de actualización como parte del
mantenimiento.

## Recuperación antes de aceptar una nueva actualización

Si el arranque nuevo falla, detener ordenadamente el proyecto y mantener los datos/volúmenes. Ejecutar `restore` con
la misma base, ámbito, snapshot y confirmación inversa exacta:

```sh
python -B /app/scripts/adopt_batches_fingerprint.py restore \
  --base /state/real-jaen.sqlite \
  --scope /app/ambito.json \
  --backup-dir /backups/batches-8ed0-to-993e-<identificador> \
  --confirm 'restore coordinator fingerprint 993e1018b66665a9c1f61cb980893d91a500bd4e852607554b5f3823e1143e2d -> 8ed0a15a88a399cfb78f0e20f240f5196ff10caead77532a48d2de5c654e8cd7'
```

La reversión cambia solo el campo de huella y exige que base, generación y ambos historiales sigan idénticos al
recibo de adopción. Después se arranca únicamente la imagen anterior `a4368ba`. Si hubo una nueva solicitud,
publicación o cualquier otra diferencia, el script se niega a revertir automáticamente: revisar y recuperar usando
el respaldo operativo completo antes de continuar. El respaldo nunca se borra como parte de la reversión.
