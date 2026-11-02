# Coordinador interno de actualizacion: fixtures locales

> Estado 29-10-2026: el coordinador (`persistencia/actualizacion.py`) está integrado. Lo usan `control.py` y el botón
> «Actualizar» del dashboard con el servicio de adquisición (`rj-analyst/docs/contrato-actualizaciones.md`,
> [adquisicion-sofascore.md](adquisicion-sofascore.md)). Las restricciones de abajo («sin endpoints, red ni botón»)
> describen la primera entrega del 26-10; sus reglas de generación, captura y publicación siguen vigentes.

Primera entrega, revision del 26-10-2026. Implementacion: persistencia/actualizacion.py.
No se importa desde api.main ni cambia Compose. No hay endpoints, flag de activacion,
red, scraper, navegador, autenticacion, boton ni actualizaciones automaticas. La instalacion
habitual conserva su arranque y su instantanea estaticos. No abrir este control sobre ella.

Contrato vigente del boton: ../rj-analyst/docs/contrato-actualizaciones.md (la propuesta inicial
se retiro el 29-10-2026; recuperable en el historial de Git de rj-analyst).

## Interfaz interna y almacenamiento

Coordinador(base, ambito) exige base existente compatible, huella de codigo acreditada y
uso mediante with/close. solicitar(exportaciones=[(fixture_jsonl, equipo)], calendarios=[...],
fichas=[...]) devuelve UUID; estado/esperar consultan resultado; capturar devuelve Generacion,
con id y consultar(ruta). Los consumidores capturan una referencia una vez por peticion.
Consultar devuelve una respuesta independiente; las instantaneas publicadas no se mutan.
El atributo _datos es privado, no una interfaz de modificacion.

Un hilo propietario mantiene el bloqueo original por ruta desde antes de reconciliar hasta
close. Importacion, copia, verificacion y restauracion se llaman en ese mismo hilo: la reentrada
del bloqueo es por hilo, no un permiso general para todos los workers. Otra instancia o CLI
sigue rechazada. Los hijos de las pruebas no heredan descriptores del propietario.

Journal operativo independiente: <base>.actualizaciones/jobs.sqlite, conexiones breves cerradas,
synchronous FULL, indice unico parcial para queued/running y fila de generacion confirmada.
Las solicitudes simultaneas devuelven el mismo trabajo activo. No hay cola generica de tareas.
Entradas congeladas, manifiesto de huellas y candidata se conservan en subdirectorio UUID;
las copias usan copias/ mediante las funciones existentes. No hay limpieza automatica ni cuota.
Base, journal y copias forman la unidad de recuperacion: no separar ni borrar sus archivos.
Close drena el trabajo aceptado; la cancelacion no forma parte de esta entrega.

## Invariantes, orden durable y punto de confirmacion

1. Registrar queued. Comprobar codigo, ambito, principal y espacio. Congelar fixtures locales.
2. Obtener B consistente y verificada; registrar ruta y descripcion completa antes de tocar principal.
3. Copiar C, importar con sincronizacion.importar_evidencia, verificar y preparar N mediante
   lectura.cargar_desde_base. Comprobar serializacion de las rutas deportivas disponibles.
4. Registrar publishing con B y huella esperada antes de promover.
5. Restaurar C en principal usando sincronizacion.restaurar y verificar descripcion completa.
6. Cerrar la puerta de nuevas capturas, comprobar otra vez codigo/ambito y hacer COMMIT del
   journal que marca completed y actualiza generacion/descripcion/codigo/ambito conjuntamente.
   ESTE COMMIT ES LA CONFIRMACION DURABLE. Intercambiar la referencia y abrir la puerta.

Las peticiones anteriores conservan G; las nuevas esperan el tramo de confirmacion y reciben N.
No se mantiene el mutex de captura durante la preparacion/importacion. La recuperacion de un
fallo de publicacion si puede mantenerlo mientras restablece un estado conocido.

Antes del COMMIT del journal, un fallo tras publishing restaura B y coteja descripcion completa.
Se usa exclusivamente sincronizacion.restaurar tanto para promocion como para recuperacion;
no hay os.replace propio ni borrado de WAL/SHM en el coordinador. El COMMIT de C no se revierte:
su evidencia queda guardada, sin ser generacion publicada.

Si el COMMIT del journal devuelve una excepcion, releer antes de decidir. Si completed existe,
conservar N; un fallo de notificacion no deshace una confirmacion durable. Si tampoco se puede
leer el journal, bloquear nuevas capturas y solicitudes bajo la puerta de confirmacion, sin
intentar otra escritura deportiva ni declarar recuperacion completa. Referencias G ya capturadas
siguen siendo utilizables; el siguiente arranque debe reconciliar antes de entregar otra.

## Reinicio y fallos de recuperacion

Bajo el mismo bloqueo, antes de cargar una instantanea: publishing/recovering sin confirmacion
restaura B; el resto de activos pasa a interrupted sin reimportar. Validar principal confirmado,
codigo y ambito y solo entonces cargar su generacion. Si completed quedo durable antes de morir,
cargar N aunque no se llegara a intercambiar la referencia en RAM. No se descarga ni reanuda nada.

B ausente/corrupta, restauracion fallida o journal ilegible impiden abrir el control; no servir
una base ambigua. En ejecucion, si recuperar falla, nuevas capturas/solicitudes quedan bloqueadas,
se conservan archivos y referencias G ya entregadas y el trabajo sigue recuperable en el journal.
No borrar evidencia para desbloquear. Corresponde resolver almacenamiento y reabrir explicitamente.

## Reglas y disco

Ademas de versiones y sincronizacion en seco, persistir sha256_agregada de
carga.referencia_del_codigo: api.py, queries.py, importar.py, configuracion de carga y todos sus archivos
de persistencia. Persistir tambien SHA256 del ambito. Comparar al abrir, preparar y justo antes
de confirmar; no depender solo de Git o rule_set.version. Esto detecta cambios de normalizacion
y agregados y tambien rechaza conservadoramente cambios sin efecto deportivo.
Primera apertura exige huella archivada en una ejecucion confirmada. La adopcion de otra huella
tras mantenimiento no esta implementada: no editar el journal para saltar esta comprobacion.
Los fixtures se cargan con el codigo de esta entrega; no se usa la base habitual como prueba.

Exigir mismo sistema de archivos para candidata y principal. Preflight conservador: libre >=
12 * (principal + diarios + entradas) + reserva configurable (64 MiB por defecto). Considera B,
C, copias internas y WAL, pero no reserva espacio ni garantiza una expansion arbitraria de JSON.
ENOSPC antes de publicar conserva principal; si no cabe registrar failed, permanece un trabajo
interrumpible. Durante publicacion, intentar recuperar B; si tampoco cabe, bloquear y conservar
los archivos. El administrador debe liberar espacio fuera de ellos y reabrir para reconciliar.
SQLite FULL cubre terminacion de procesos sobre almacenamiento que respete fsync, no perdida
de alimentacion, corrupcion fisica o escrituras externas. El bloqueo sigue siendo cooperativo.

## Respaldo coherente de base y journal

No copiar principal y jobs.sqlite por separado mientras pueda publicarse una generacion.
Son dos transacciones SQLite distintas: cada archivo puede ser integro y el par incoherente.
El mutex de capturas no basta para el respaldo; hay que excluir tambien solicitudes,
importaciones, recuperacion y escrituras del journal durante TODO el procedimiento.

Procedimiento de mantenimiento explicito (no se ejecuta sobre la instalacion habitual):

1. Cerrar la instancia interna mediante close: impide solicitudes nuevas, drena la aceptada,
   termina el propietario y libera su bloqueo. Detener/retirar su punto de entrada con el
   procedimiento del despliegue; anunciar antes cualquier parada de un servicio real.
   No implementar un respaldo desde otro hilo del coordinador activo: la reentrada es por hilo.
2. En una sola herramienta/proceso de mantenimiento adquirir bloquear(base) y mantenerlo
   hasta finalizar todas las copias y su validacion. Si lo mantiene otra API/coordinador, fallar
   y reintentar expresamente; no borrar .lock ni forzar la entrada. El mecanismo es cooperativo:
   tampoco debe haber programas externos escribiendo en el journal o sus directorios.
3. Exigir journal existente, sin trabajos queued/running y con metadatos confirmados cuya
   descripcion coincida exactamente con sqlite.comprobar_copia(base). Si hay publishing,
   recovering o discrepancia, NO exportar como respaldo valido: cerrar y reconciliar con el
   propietario antes de volver a intentarlo. No arrancar/reanudar trabajos automaticamente.
4. Crear un directorio de salida NUEVO fuera del arbol de origen, con permisos restrictivos.
   Bajo ese MISMO bloqueo, copiar principal mediante sqlite.copia_consistente y journal mediante
   sqlite3.Connection.backup desde mode=ro, finalizar ambos con journal DELETE y cerrarlos.
   No abrir el journal origen con connect sin mode=ro: podria crear otro vacio por error de ruta.
5. Conservar en el paquete los directorios de evidencia/candidatas del journal y copias/.
   En este punto estan quiescentes: copiarlos completos, incluidos los diarios que pudieran
   conservar candidatas cerradas; nunca decidir borrar WAL/SHM como paso de exportacion.
   Sustituir en el paquete unicamente jobs.sqlite y sus acompanantes por su copia finalizada,
   no tocar los originales. Conservar tambien el ambito y manifiesto de codigo de la entrega.
6. Validar dentro del bloqueo las dos imagenes finales: integridad, revision y descripcion
   completa de principal; integrity_check y foreign_key_check de journal; ausencia de activos;
   metadatos de generacion/codigo/ambito/descripcion iguales a los del journal origen; el hash
   deportivo del paquete debe coincidir con su confirmado. Releer origen y metadatos al acabar:
   deben seguir iguales. Registrar SHA256 de cada archivo y un manifiesto con generacion,
   codigo, ambito, rutas/estructura, fecha y validacion. Marcar el paquete completo SOLO entonces.
7. Liberar bloqueo al salir. Un fallo deja paquete incompleto, no un respaldo utilizable;
   no continuar copiando como si hubiera tenido exito. No borrar archivos de origen para reparar.

Esqueleto de la seccion critica, invocado con las herramientas SQL existentes y base/journal
quiescentes. El proceso que ejecuta este bloque debe mantener la exclusividad hasta completar
los pasos 5 y 6 anteriores; no trasladar esas operaciones fuera del with:

```python
from contextlib import closing
import sqlite3
from persistencia.bloqueo import bloquear
from persistencia import sqlite

with bloquear(base):
    # Comprobar journal, ausencia de activos y descripcion confirmada antes de crear salida.
    with closing(sqlite3.connect(journal.resolve().as_uri() + '?mode=ro', uri=True)) as origen:
        # Leer y validar aqui confirmado y trabajo; rechazar activos/discrepancias.
        esperado = sqlite.comprobar_copia(base)
        sqlite.copia_consistente(base, salida_base)  # exige destino ausente
        with closing(sqlite3.connect(salida_journal)) as copia:
            origen.backup(copia)
            copia.execute('PRAGMA journal_mode=DELETE')
        sqlite.comprobar_copia(salida_base, esperado)
        # Copiar arbol quiescente; cotejar journal original/copia, base y manifiesto completo.
```

Este es un protocolo administrativo documentado, no un nuevo comando de backup implementado.
SQLite backup evita depender de si quedaron paginas en WAL; no se presenta una copia de bytes
concurrente como equivalente. No hay backup en caliente habilitado desde otro hilo.

Recuperacion del paquete: parar control, adquirir el mismo bloqueo de la ruta original,
conservar el estado actual completo, validar manifiesto y pareja del paquete, y restaurar
principal exclusivamente mediante sincronizacion.restaurar. Restaurar el journal y su arbol
quiescentes como UNA unidad antes de volver a abrir el control; cerrar todas las conexiones
antes del reemplazo. Si muere la herramienta a mitad, mantener el control parado y repetir
la instalacion completa del paquete validado, no abrir una pareja intermedia. El coordinador
rechazara una descripcion de principal que no coincida con confirmado. Ningun archivo .lock
se exporta como prueba de exclusividad ni se borra el de la instalacion.

La version actual conserva rutas absolutas de candidatas/copias en trabajos historicos.
El protocolo soportado es restaurar en las mismas rutas de almacenamiento; NO prometer una
restauracion portable con rutas reescritas automaticamente. Una futura herramienta de traslado
debera validar y remapear referencias/manifiestos antes de habilitar el control. No modificar
el journal a mano para saltar huellas o promover un trabajo no confirmado.

## Pruebas y limites

Comandos sobre fixtures temporales, sin red:

```
python -B -W error::ResourceWarning -m unittest discover -s tests -p test_actualizacion.py -v
python -B -W error::ResourceWarning -m unittest discover -s tests
```

Exito y sin cambios, 24 solicitudes simultaneas, exclusion de instancia/CLI, generaciones
antigua/nueva, IDs/originales/decisiones y equivalencia de respuestas. Fallo transaccional antes
del COMMIT real, fallo despues del COMMIT de candidata, confirmacion ambigua/ilegible, fallo de
replace/recuperacion, ENOSPC en copia/journal/recuperacion y preflight, codigo/ambito cambiados.
Once terminaciones abruptas os._exit sin finally con reapertura: antes de copia, copia guardada,
antes de importar, candidata confirmada, instantanea preparada, publicacion preparada, principal
reemplazado, antes de confirmacion, confirmado, referencia cambiada y despues de recuperar.

Resultados finales propios (26-10-2026), ResourceWarning tratado como error:

| Validacion | Resultado |
|---|---|
| Focalizadas Windows | 13 correctas, 35,249 s |
| Focalizadas Linux | 13 correctas, 22,538 s; network none y volumen exclusivo de fixtures |
| Suite completa API con SQL | 238 pruebas, correcta, 2 omitidas; 83,742 s |
| Suite completa sin SQLAlchemy/Alembic | 238 pruebas, correcta, 91 omitidas; 7,085 s |

Las 11 terminaciones abruptas son subcasos de la prueba de reinicio, no 11 metodos adicionales.
Las omisiones con SQL son casos ya condicionados de dependencias/symlinks de la suite existente.
Logs fuera de Git: rj-actualizacion-final.log, rj-coordinator-final-linux.log,
rj-api-final-sql.log y rj-api-final-json.log en el directorio temporal del anfitrion.
Contenedor y volumen exclusivos de esta prueba retirados tras verificar etiquetas y salida 0;
no se modificaron recursos Docker habituales. Sin commit ni push.
Linux usa contenedor de pruebas sin red y volumen exclusivo, no datos o servicios habituales.
No se ha probado el servidor del grupo ni caida completa de host/VM. No se ha conectado el
coordinador a HTTP ni implementado coherencia de varias consultas frontend, cancelacion,
retencion o adopcion de nuevas huellas. Primera entrega interna aprobada; las siguientes fases requieren revision independiente.
