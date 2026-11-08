# Transición administrativa de enrichment 0007 → 0008

`scripts/transition_enrichment_0008_admin.py` contiene `adopt` y `restore` para una ventana de mantenimiento explícita. No arranca servicios, no ejecuta Acquisition y no hace consultas externas. La herramienta no sustituye la preparación de la candidata: recibe la candidata 0008 ya generada y vuelve a comprobar su informe, procedencia, paquete coherente, integridad, cobertura `rules-v1`, idempotencia y conservación de las tablas 0007.

## Requisitos antes de usarla

- Ejecutar únicamente con todos los servicios detenidos por el operador. El texto `services-stopped` de la confirmación es una declaración explícita; los bloqueos cooperativos y los journals añaden comprobaciones, pero no detectan todos los procesos SQLite externos.
- Usar una instalación 0007 y una candidata 0008 producida desde exactamente la misma generación, descripción, ámbito, huella confirmada y tres historiales. La huella fuente se lee de `confirmado.detalle`; no está fijada en el script. La huella destino se calcula desde el código revisado que se ejecuta y se verifica contra `carga.referencia_del_codigo()`.
- Elegir un directorio de transición nuevo, privado, fuera del árbol de instalación y separado de la candidata. La herramienta exige espacio para un respaldo completo antes de crear archivos.
- Para `restore`, conservar el árbol de runtime que corresponde exactamente a la huella original. La herramienta verifica ese código y abre con él la generación después de restaurar.
- Obtener del preflight el UUID y la huella destino, y del `backup/manifest.json` el SHA-256 que se requiere para confirmar la reversión. Los valores no deben copiarse de un respaldo histórico distinto.

## Adoptar

La llamada requiere las rutas `--base`, `--scope`, `--acquisition-journal`, `--candidate-dir` y `--transition-dir`, además de:

```text
--confirm "adopt enrichment 0007->0008 <UUID-generacion> <SHA256-destino> services-stopped"
```

La herramienta toma en orden estable los bloqueos de SQLite deportiva, coordinador, control HTTP y Acquisition. Rechaza trabajos activos, fases `publishing`/`recovering`, diferencias de ámbito o generación, datos laterales no resueltos y cambios en evidencias. Antes de alterar nada crea un paquete privado y verificable con copias SQLite consistentes, diarios, evidencias y un manifiesto sellado.

La candidata se instala mediante la función de restauración SQLite ya existente. Después se actualiza en una única transacción de `confirmado.detalle` solamente `codigo` y `descripcion`; generación, ámbito e historiales se conservan. Se registran fases duraderas, se valida la nueva pareja de estados y, fuera de los bloqueos administrativos, se abre y cierra el coordinador para comprobar la generación antes de emitir `adoption.json`.

## Interrupciones y continuación

- Un fallo antes de empezar el respaldo no cambia la instalación; si no llegó a escribirse ningún artefacto, la carpeta vacía de transición se retira y se puede repetir con la misma ruta.
- Si existe un respaldo parcial sin manifiesto completo, conservarlo e investigar. No se elimina ni se declara válido automáticamente; iniciar una operación nueva solo después de revisar el contenido.
- Con un manifiesto completo, `adopt` vuelve a verificar todos los insumos. Si la SQLite sigue en 0007, continúa desde el origen. Si ya se sustituyó por 0008 pero el journal aún confirma el código antiguo, reconoce esa fase registrada y completa el commit del journal.
- Si el journal ya contiene la huella y descripción nuevas, la repetición verifica y termina el proceso sin duplicar filas de trabajo.
- Un cambio de generación, ámbito, historiales o evidencias hace que la continuación se detenga. No debe forzarse editando el manifiesto o el journal.

## Revertir

`restore` requiere las mismas rutas, el directorio de transición y `--source-runtime-root` con el código exacto que originó la huella previa. La confirmación es:

```text
--confirm "restore enrichment transition <SHA256-manifest> services-stopped"
```

Se rechaza si aparece una generación, solicitud o trabajo posterior, o si el estado no coincide con los dos estados conocidos de la transición. SQLite deportiva y los tres journals se restauran desde el mismo snapshot. Los respaldos previos de SQLite creados por la rutina existente se conservan en `copias/`. La verificación final incluye descripción, huellas de tablas e historiales y apertura/cierre del coordinador con el runtime original. Una interrupción dentro de `restore_*` puede continuarse con el mismo comando mientras sigan intactos generación, ámbito e historiales.

Esta herramienta se ha probado únicamente con instalaciones desechables. No se ha adoptado ni revertido una instalación activa; esa intervención requiere su propia autorización y ventana de mantenimiento, junto con comprobaciones del host y sus servicios.
