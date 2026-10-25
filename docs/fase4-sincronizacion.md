# Fase 4: importación incremental con sincronización completa

**Estado (25-10-2026): implementada; sin activar en la base real.** Diseño aprobado con cuatro decisiones
(UNIQUE de la convocatoria, FK a la observación más comprobación en código, `call_up_change`, migrar la base activa
solo cuando se pida). Probada sobre bases temporales y copias de la base real; la base activa no se ha migrado ni
escrito. Resultados, límites y procedimiento de activación: secciones 9 y 10. Parte de la alternativa aprobada en
`revision-simplicidad-persistencia.md`: una ejecución de `importar.ensamblar` produce el estado operativo, la
persistencia lo escribe con el mismo código de la carga inicial y solo se escriben las diferencias, conservando ids.

Lo que no cambia:
- contratos e identificadores públicos de la API;
- las reglas del importador y de la API;
- las migraciones existentes (`0001`, `0002`);
- la base activa (`real-jaen.sqlite`), que solo se tocará tras aprobación, con copia previa.

---

## 1. Flujo de una importación

`python -m persistencia importar --base B --exportacion EQUIPO=RUTA … [--calendario RUTA …] [--fichas-jugadores RUTA …]`

1. **Comprobaciones previas** (sin escribir):
   - la base está en la revisión `head` y tiene una carga inicial confirmada;
   - **sincronización en seco** sobre la evidencia existente (sección 4), que debe dar **0 cambios**.
2. **Copia de seguridad consistente** verificada (sección 6). Sin copia válida no se sigue.
3. **Una transacción** (`BEGIN IMMEDIATE`):
   1. Ejecución `import` en estado `running`.
   2. **Evidencia nueva**:
      - Se lee con las funciones actuales: `importar.leer_exportacion`, `leer_calendario` y `leer_fichas` (huella,
        instante, validación de fichas).
      - Un fichero con sha256 conocido no añade observaciones (`already_known`).
      - Uno nuevo recibe la etiqueta siguiente (`E25`, `E26`…) y sus observaciones continúan el orden de ingestión.
      - `content` se deduplica.
   3. **Declaraciones** (`EQUIPO=RUTA`): se crean, validadas, aunque el fichero ya se conociera (sección 3).
   4. **Sincronización completa** (sección 2): `ensamblar` sobre toda la evidencia y escritura de las diferencias.
   5. **Verificación antes de confirmar**:
      - una segunda sincronización en seco da 0 cambios;
      - no hay violaciones de clave foránea (`PRAGMA foreign_key_check`);
      - cada convocatoria activa pertenece a la alineación seleccionada (sección 2.2).
   6. Ejecución `committed`, con informe (cambios por tabla, copia de seguridad, referencia del código).
4. Si algo falla, rollback completo. La ejecución queda `failed`, con su error, en una transacción aparte, y la copia
   de seguridad se conserva.

La API sigue sin recarga en caliente: tras importar, se reinicia para servir el estado nuevo.

---

## 2. Sincronización con claves estables

### 2.1 Políticas por tabla

| Tabla | Clave estable | Política |
|---|---|---|
| entidades (`match`, `team`, `player`, `competition`, `season`, `stage`) | id externo | nunca se borran; las nuevas reciben el siguiente id y su uid UUIDv5 (una vez) |
| `observation` | (fichero, línea) | solo se añaden; las de un partido nuevo se asocian a su entidad antes de seleccionar |
| `section_selection` | (partido, clave, sub) | en su sitio; la anterior pasa a `section_selection_history` con `valid_from_run` = su ejecución, `valid_to_run` = la actual y su regla |
| `match_state`, `team_name` | partido / equipo | en su sitio |
| `match_version_issue`, `call_up_stat` | por padre (sin referencias entrantes) | se sustituye el conjunto del padre solo si cambia |
| `call_up` | (partido, lado, jugador) | en su sitio, con vigencia (2.2) |
| `player_attribute_observation` | (observación, entrada, campo) | solo se añaden (las observaciones son inmutables) |
| `discrepancy` + hijas | (tipo, sujeto, clave, sub) | si cambia, la anterior se marca `superseded_in_run` y se crea otra; nada se borra |
| `rule_set` | (nombre, versión) | una regla nueva detiene la importación (sección 4) |

**Implementación**:
- `carga.escribir` se divide en «calcular filas» (puro) e «insertarlas»: la carga inicial inserta todo y la
  sincronización compara.
- Las políticas se expresan como una tabla declarativa (tabla, clave, política) para que el código genérico no se
  repita por tabla.
- El prototipo medido ocupa unas 270 líneas.

### 2.2 Convocatoria eliminada de la versión operativa

Una versión posterior de la alineación puede dejar de incluir a un jugador. Tratamiento:

1. **Deja de contar sin borrarse.** La fila de `call_up` conserva su id, su última alineación
   (`lineup_observation_id`), sus campos y sus estadísticas (`call_up_stat`), que siguen siendo evidencia de lo que
   decía esa versión. Pasa a `status = 'retired'`, con `retired_in_run`.
2. **Nada la usa mientras está retirada.** La reconstrucción y la API solo leen convocatorias `active`, así que el
   jugador desaparece del partido, de sus acumulados y de su lista de partidos. Es lo mismo que hace hoy el modo JSON
   con esa alineación.
3. **Su id nunca se reutiliza**: las filas no se borran y los ids nuevos son siempre el máximo más uno.
4. **Si reaparece** en una versión operativa posterior, con la misma clave (partido, lado, jugador), se **reactiva la
   misma fila** con el mismo id (`status = 'active'`, `retired_in_run = NULL`), con los campos y estadísticas de la
   nueva alineación.
5. **Si cambia de lado**, es otra clave: se retira la del lado anterior y se crea o reactiva la del nuevo.
6. **Trazabilidad de los cambios**: cada transición se guarda en `call_up_change` (`call_up_id`, `run_id`, `change`
   `retired` | `reactivated`, `lineup_observation_id` de la versión que la provoca). Sin esta tabla, una reactivación
   borraría el rastro de la retirada.

**Cambios de esquema** (migración nueva, sección 5):
- `call_up.status` (CHECK `active`/`retired`) y `call_up.retired_in_run` (FK, con CHECK de coherencia).
- La FK compuesta `fk_call_up_selection`, que obliga a apuntar a la observación seleccionada, impediría conservar una
  retirada con su alineación. Se sustituye por:
  - una FK compuesta a `observation` (`id`, `match_id`, `key`, `sub`): la convocatoria sale de una alineación de su
    partido;
  - la comprobación «toda convocatoria **activa** apunta a la alineación seleccionada», en la verificación de cada
    importación y en una prueba. SQLite no admite FK parciales, así que la regla se valida en código.
- **UNIQUE (`match_id`, `side`, `external_player_id`)**: protege la clave de sincronización.
  - Hoy ninguna base contiene duplicados, porque la carga los rechaza.
  - El lector histórico (modo JSON) sigue admitiendo carpetas legadas con duplicados; una base no.
  - **Decisión para aprobar**: renuncia a que el esquema pueda representar duplicados legados, que las importaciones
    ya rechazan.

### 2.3 Casos que detienen la importación sin escribir

- Clave de convocatoria repetida en el estado deseado.
- Regla nueva o sincronización en seco con cambios (sección 4).
- Una declaración que no se puede validar.
- Un valor no representable en JSON.
- Ids no enteros, como en la carga inicial.

---

## 3. Manifiesto con todos los ficheros y declaraciones

`reconstruccion.exportaciones` construye hoy el manifiesto solo con los ficheros de la carga inicial. Pasará a:

- **Ficheros**: todos los `source_file` de evidencia (`raw`, `listing`, `player_profile`), en orden de ingestión
  (`source_file.id`), con su etiqueta. `file` es el nombre de su **primera** presentación (`import_run_file`).
- **Declaraciones**: las **activas** de `file_team_declaration`, incluidas las añadidas a un fichero ya conocido.
  - Un fichero con varias declaraciones activas genera **una entrada por declaración** (misma etiqueta y sha256),
    porque `api.equipos_con_exportacion` agrupa por equipo y cada equipo debe listarlo. Es una estructura interna:
    `/scope` publica los mismos campos.
- **`imported_at`**: el fin de la última ejecución confirmada que añadió evidencia. La API no lo publica.

La sincronización usa este mismo manifiesto para `ensamblar`.

---

## 4. Cambios de reglas y reconstrucción explícita

**Qué cuenta como cambio de reglas**:
1. **Versión declarada**: cada regla materializada tiene (nombre, versión) en el código (`importar.FORMATO` y
   constantes de la persistencia). Si una no existe en `rule_set`, cambió de versión.
2. **Código sin cambio de versión**: se detecta comparando resultados, no versiones. Una **sincronización en seco
   sobre la evidencia existente debe dar 0 cambios**; si da alguno, el estado guardado no corresponde al código actual.

**Dónde se comprueba**:

| Momento | Comprobación | Si falla |
|---|---|---|
| Inicio de cada `importar` | (1) y (2), antes de la copia y de escribir | se detiene: «el estado guardado no corresponde a las reglas actuales; ejecute `persistencia reconstruir`» |
| `persistencia verificar --base B` (nuevo, solo lectura) | (1), (2) y FK | informe sin escribir |
| Arranque de la API en modo SQL | (1): versiones de `rule_set` usadas por el estado vigente frente a las del código (barato) | no arranca. (2) no se hace al arrancar (~3 s): queda en `verificar` y en cada importación |

**Reconstrucción explícita**: `persistencia reconstruir --base B --confirmar`

1. Copia de seguridad verificada.
2. En una transacción: filas de `rule_set` para las versiones y el código actuales; sincronización completa
   **permitiendo** el cambio de reglas; las filas reescritas llevan la regla nueva y la ejecución `rebuild`.
3. Selecciones que cambian → historial. Discrepancias que cambian → superadas.
4. Informe de cambios por tabla en `import_run.summary`.
5. Sin `--confirmar` solo muestra la diferencia (en seco).

---

## 5. Retirada de `team_match_stat` e `incident`

**Ninguna lectura depende de ellas.**
- Aparecen solo en `persistencia/carga.py` (escritura), `modelos.py`, la migración `0001` y las pruebas de esquema y
  carga.
- `reconstruccion.py`, `lectura.py`, `decisiones.py` y `api.py` no las nombran.
- La API lee esas secciones del payload original (`content`) con sus propias funciones (`estadisticas_all`,
  `contexto_partido`).
- Prueba adicional: las 3217 rutas, idénticas antes y después de aplicar la migración a una copia de la base real.

**Migraciones nuevas** (sin reescribir `0001` ni `0002`):

| Revisión | Contenido | `downgrade` |
|---|---|---|
| `0003_retirar_extracciones_sin_uso` | `DROP` de `team_match_stat` e `incident` | las recrea vacías. No se rellenan: el código que las llenaba se retira. Queda documentado en la revisión |
| `0004_vigencia_de_convocatorias` | `call_up.status` y `retired_in_run`; nueva tabla `call_up_change`; cambio de la FK compuesta (2.2); UNIQUE de la clave | quita lo añadido y restaura la FK a la selección. Solo es posible si no hay convocatorias retiradas: si las hay, el `downgrade` se detiene en lugar de perderlas |

**Lo que se retira con ellas**:
- `TeamMatchStat` e `Incident` de `modelos.py`;
- `escribir_secciones` de `carga.py`;
- la regla `persistencia.carga` de las cargas nuevas;
- sus pruebas, que se sustituyen por la comprobación de que no existen.

En la base real la fila `rule_set` `persistencia.carga` queda como **registro histórico**: ninguna tabla la
referencia. Se documenta y no se borra, porque la procedencia de ejecuciones pasadas la menciona.

**Originales**: las secciones `statistics` e `incidents` siguen completas en `content` y se sirven igual.

---

## 6. Copia de seguridad y restauración

Funciones nuevas en `persistencia/sqlite.py`, donde está todo lo específico de SQLite.

### 6.1 Copia consistente

- **Método**: la API de copia de SQLite (`sqlite3.Connection.backup`). Copia página a página una imagen coherente
  aunque haya lectores y diario WAL. Copiar el archivo a mano no lo garantiza.
- **Destino**: `<carpeta de la base>\copias\<nombre>.<AAAAMMDD-HHMMSS>.<motivo>.sqlite`, fuera de los repositorios.
  El nombre nunca existe antes; si existiera, se rechaza.
- **Verificación**, antes de seguir:
  - abrir la copia en solo lectura;
  - `PRAGMA integrity_check` = `ok`;
  - `PRAGMA foreign_key_check` vacío;
  - misma revisión de Alembic;
  - mismos recuentos por tabla;
  - misma huella de contenido: sha256 de todas las filas de todas las tablas, en orden de clave primaria.
- **Registro**: la copia, su sha256 de archivo y su huella de contenido van en el informe de la ejecución que la
  motivó.

**Cuándo se hace**: antes de `importar`, `reconstruir`, `sustituciones` y de aplicar migraciones a una base existente
(`persistencia migrar --base B`, nuevo: hace la copia y luego `alembic upgrade`). La carga inicial no la necesita
porque crea una base nueva.

### 6.2 Restauración

`python -m persistencia restaurar --copia C --base B --confirmar`

1. Verifica la copia (6.1) y que su revisión es la del código (`--revision` permite otra a propósito: marcha atrás de
   una migración junto con el código de esa revisión).
2. **Exige que ningún proceso use la base de destino**: en Windows, la base, su `-wal`, su `-shm` y su `-journal` se
   abren sin compartir; si alguno está abierto por otro proceso, se detiene sin tocar nada. En otros sistemas no se
   sabe comprobar y se trata como en uso.
3. Copia la base actual aparte, también verificada, con el motivo `antes-de-restaurar`: la restauración también se
   puede deshacer.
4. Borra `B` y sus `-wal`, `-shm` y `-journal` (un `-wal` antiguo aplicado sobre el archivo restaurado lo
   corrompería) y escribe la copia en `B` con la API de copia. Nunca renombra encima de una base abierta.
5. Verifica `B` contra la huella de la copia.
6. Avisa de que la API debe reiniciarse.

La API en modo SQL lee la base al arrancar y **cierra la conexión**: con la API en marcha la base no figura en uso.
Por eso el procedimiento (sección 10) exige parar la API antes; si no se para, la restauración no la rompe, pero sigue
sirviendo los datos anteriores hasta reiniciarla. `migrar` hace la misma comprobación de uso.

---

## 7. Pruebas

Bases temporales, salvo las marcadas como **copia real** (una copia de `real-jaen.sqlite` en una carpeta temporal; la
base activa no se toca y su sha256 se comprueba antes y después).

| # | Caso | Comprueba |
|---|---|---|
| 1 | Sincronización en seco sin evidencia nueva (sintética y **copia real**) | 0 cambios |
| 2 | Importación con evidencia nueva: otra versión de alineación, pendiente → finalizado, partido, jugador y equipo nuevos | cambios esperados; ids de entidades y convocatorias conservados; historial con ejecución y regla; **todas las rutas** iguales al camino independiente (evidencia → `ensamblar` → `api.componer`) y al modo JSON con la carpeta equivalente |
| 3 | Jugador que desaparece de la versión operativa | convocatoria `retired`, mismo id, estadísticas conservadas, `call_up_change`; fuera de partido, acumulados y lista del jugador; rutas iguales al modo JSON |
| 4 | El jugador reaparece | misma fila y mismo id `active`; dos transiciones en `call_up_change` |
| 5 | Cambio de lado | retirada de una clave y alta de otra |
| 6 | Clave de convocatoria repetida | se detiene sin escribir |
| 7 | Fichero ya conocido; mismo fichero dos veces en el mismo comando | `already_known`, sin observaciones duplicadas |
| 8 | Declaración nueva sobre un fichero conocido; dos declaraciones en un fichero; declaración inválida | manifiesto y `/scope` con ambos equipos; la inválida se rechaza sin escribir |
| 9 | Ficheros añadidos tras la carga inicial | manifiesto con todos, en orden y con etiquetas `E25…` |
| 10 | Versión de regla nueva (simulada) | `importar` se detiene; `reconstruir` sin `--confirmar` muestra la diferencia; con `--confirmar` la aplica con ejecución `rebuild`, regla nueva e historial |
| 11 | Código de reglas cambiado sin versión (función de reglas sustituida en la prueba) | la sincronización en seco da cambios → se detiene |
| 12 | API en modo SQL con versión de regla distinta | no arranca, con mensaje claro |
| 13 | Fallo a mitad de la transacción | rollback; ejecución `failed`; base idéntica a la copia (huella de contenido) |
| 14 | Copia con un lector abierto y diario WAL | copia íntegra, misma huella |
| 15 | Restauración | tras modificar y restaurar, misma huella de contenido y mismas respuestas en todas las rutas que antes; existe la copia `antes-de-restaurar`; una copia corrupta o de otra revisión se rechaza |
| 16 | Migraciones `0003` y `0004` | `upgrade`, `downgrade` y `upgrade` en base temporal; el esquema coincide con los modelos; el `downgrade` de `0004` se detiene si hay retiradas; en **copia real**: copia previa, `migrar`, las 3217 rutas idénticas antes y después, y las tablas retiradas ya no existen |
| 17 | Ninguna lectura de las tablas retiradas | búsqueda en el código y las rutas iguales tras la migración |
| 18 | Modo JSON sin dependencias | suite y captura de la fase 0 idénticas |
| 19 | Sustituciones tras una importación | siguen activas y válidas; la copia previa se hace |

---

## 8. Alcance de la implementación y orden

1. `persistencia/sqlite.py`: copia, verificación y restauración. Comandos `migrar`, `restaurar` y `verificar`.
2. Migraciones `0003` y `0004`, modelos y pruebas de esquema.
3. `carga.py`: separar filas de inserción; quitar `escribir_secciones`; vigencia de convocatorias en la escritura.
4. Módulo de sincronización con políticas declarativas; comando `importar`; `reconstruir`.
5. `reconstruccion.py`: manifiesto completo y solo convocatorias activas. `lectura.py`: comprobación de versiones de
   reglas.
6. Pruebas de la sección 7 y documentación.

**Fuera de alcance**: botón Actualizar, Docker, descarga de datos, cambios en el dashboard, herramientas de identidad.

**Para aprobar**:
1. La UNIQUE (`match_id`, `side`, `external_player_id`) en `call_up` (2.2).
2. Sustituir la FK compuesta a la selección por la FK a la observación más la comprobación en código (2.2).
3. `call_up_change` como registro de transiciones.
4. Aplicar `migrar` a la base activa solo cuando lo pidas, con copia previa.

---

## 9. Implementación, resultados y límites

### 9.1 Qué se ha implementado

| Pieza | Dónde |
|---|---|
| Migraciones `0003` (retira `team_match_stat`, `incident`) y `0004` (vigencia, FK a la observación, UNIQUE, `call_up_change`, vista `call_up_vigente`) | `persistencia/migraciones/versions/` |
| Las migraciones se ejecutan con claves foráneas desactivadas (modo batch) y, antes de confirmar, comprueban claves foráneas y, desde `0004`, convocatorias activas y vigencias | `migraciones/env.py` |
| `carga.filas_de_carga` (filas sin escribir) + `escribir` (inserción); `REGLAS`; sin `escribir_secciones` ni `persistencia.carga` | `persistencia/carga.py` |
| Plan de solo lectura, aplicación, vigencia, discrepancias superadas, historial de selección, verificación, importar, reconstruir, migrar, copiar, restaurar | `persistencia/sincronizacion.py` (nuevo) |
| Copia consistente verificada, descripción y huella, revisión, uso por otros procesos, restauración | `persistencia/sqlite.py` |
| Manifiesto completo, resumen vigente, `imported_at`, convocatorias solo desde `call_up_vigente` | `persistencia/reconstruccion.py` |
| La API en modo SQL no arranca si el estado usa reglas que no son las del código | `persistencia/lectura.py` |
| Sustituciones: copia previa y verificación antes de confirmar | `persistencia/decisiones.py` |
| Órdenes `importar`, `verificar`, `reconstruir`, `migrar`, `copiar`, `restaurar` | `persistencia/__main__.py` |

**Selección centralizada de convocatorias activas.** La única lectura de convocatorias para la API es
`reconstruccion.jugador_partido`, que lee la vista `call_up_vigente` (y sus estadísticas, por unión con ella).
Partidos, fichas, listas de jugadores, acumulados y estadísticas de equipo salen de ahí. Una prueba busca en `api.py`,
`reconstruccion.py`, `lectura.py` y `decisiones.py` cualquier `FROM call_up`/`JOIN call_up` que no sea la vista.

**Verificación obligatoria antes de confirmar** (`sincronizacion.exigir_coherencia`), dentro de la transacción de la
carga inicial, la importación, la reconstrucción y las sustituciones; las migraciones hacen la parte de convocatorias
en `env.py`. Comprueba:
- `PRAGMA foreign_key_check` vacío;
- **toda convocatoria activa apunta a la alineación seleccionada de su partido** (`convocatorias_fuera_de_seleccion`);
- cada convocatoria tiene el estado de su última transición (`vigencias_incoherentes`);
- las reglas del estado vigente son las del código;
- la sincronización en seco sobre la evidencia guardada da 0 cambios (incluido el resumen del importador).

`persistencia verificar` hace lo mismo en solo lectura (más `integrity_check`).

**Evidencia histórica.** Una convocatoria retirada conserva su id, su última alineación y sus estadísticas: solo el
último estado que tuvo. **No es el historial**: lo que dijo cada versión de la alineación está en las observaciones
originales (`observation` y `content`, todas las versiones), y `call_up_change` solo registra cuándo y por qué
alineación se retiró o reactivó. Ninguna respuesta de la API expone convocatorias retiradas.

**Cambio de lado.** La clave es (partido, lado, jugador): la convocatoria del lado anterior queda retirada y la del
nuevo lado es otra fila con su propio id (el siguiente), o se reactiva la de ese lado si ya existió.

### 9.2 Diferencias con el diseño

- La huella de contenido ordena las filas por todas sus columnas (no solo por clave primaria): igual de determinista
  y válida para tablas sin clave primaria.
- Las copias quedan con diario `DELETE` (un solo archivo): abrirlas en solo lectura no deja `-wal`/`-shm`.
- Restauración y migración comprueban puntualmente los archivos abiertos en Windows (6.2).
- Una ejecución fallida se registra en otra transacción: tras un fallo la base es idéntica a la copia **salvo** esa
  fila de `import_run` (la prueba compara todas las tablas menos `import_run` e `import_run_file`).
- `reconstruir` solo crea filas de `rule_set` para (nombre, versión) que no existen; si el código cambia sin cambiar
  versión, la referencia del código queda en `import_run.tool_version` de la ejecución `rebuild`, a la que apuntan
  las filas reescritas (`run_id`).
- El resumen del importador (`resumen.json`) se guarda en cada ejecución que fija el estado; la reconstrucción lee el
  de la última y la sincronización en seco lo compara.
- Un fichero que solo trae alineaciones no se puede declarar propio de un equipo (la regla existente exige un `event`
  del equipo en el fichero): se importa sin declaración.
- En la base real queda la fila `rule_set` `persistencia.carga` como registro histórico (5).

### 9.3 Resultados

**Resultados previos comunicados por Claude.**

**Suite** (`.venv`, Python 3.14.4, SQLAlchemy 2.1.6, Alembic 1.20.1): 210 pruebas, OK (1 omitida, previa), ~30 s.
`tests/test_sincronizacion.py` (21 pruebas, ~7 s) cubre los casos de la sección 7:

| # | Prueba |
|---|---|
| 1 | `test_sincronizacion_en_seco_sin_cambios` (y copia real) |
| 2, 9 | `test_versiones_nuevas_partido_equipo_y_jugador_nuevos`, `test_ficheros_y_declaraciones_posteriores_en_el_manifiesto` |
| 3, 4, 5 | `test_retirada_reactivacion_y_cambio_de_lado`: en cada paso, todas las rutas (fichas, partidos, alineaciones, listas de jugadores, acumulados y estadísticas de equipo) iguales al camino independiente |
| 6 | `test_jugador_repetido_en_una_alineacion_se_rechaza_sin_escribir`, `test_clave_de_convocatoria_repetida_en_el_estado_deseado` |
| 7 | `test_repetir_la_importacion_no_cambia_datos_ni_crea_historial` (dos veces: `no_changes`, huella igual, sin historial, transiciones ni discrepancias superadas), `test_mismo_fichero_dos_veces_en_un_comando` |
| 8 | `test_ficheros_y_declaraciones_posteriores_en_el_manifiesto`, `test_declaracion_invalida_se_rechaza_sin_escribir` |
| 10, 12 | `test_version_de_regla_nueva` |
| 11 | `test_codigo_de_reglas_cambiado_sin_version` |
| 13 | `test_fallo_a_mitad_de_la_transaccion`, `test_la_verificacion_es_obligatoria_antes_de_confirmar` |
| 14 | `test_copia_con_un_lector_abierto_y_diario_wal` |
| 15 | `test_restauracion` (copia corrupta, otra revisión, base en uso, `-wal`/`-shm` ajenos, `antes-de-restaurar`, rutas iguales) |
| 16 | `Migraciones` de `test_persistencia` (esquema = modelos) y de `test_sincronizacion` (bajar a 0002 y subir, 0004 no baja con retiradas, no migra en uso, marcha atrás) |
| 17 | `test_ninguna_lectura_de_tablas_retiradas_ni_de_convocatorias_sin_la_vista` |
| 18 | suite con el Python global sin SQLAlchemy: 210 pruebas, OK (68 omitidas); captura de la fase 0 idéntica en sus 7 archivos |
| 19 | `test_sustituciones_tras_una_importacion` |

**Copia real** (copia byte a byte de `real-jaen.sqlite`, cuyo `-wal` estaba vacío; la activa no se abrió):

| Comprobación | Resultado |
|---|---|
| `migrar` 0002 → 0004 (copia previa, comprobaciones, `verificar`) | 4,9 s; 3427 convocatorias activas; tablas retiradas ausentes |
| `verificar` | ok, 0 cambios en seco, 3,3 s |
| Rutas tras migrar | 3217, idénticas a la referencia de la fase 0 y al modo JSON actual |
| Repetir la importación de los 24 ficheros iniciales | `no_changes`, 0 cambios, 24 `already_known`, datos idénticos salvo ejecuciones, sin historial; 12,0 s |
| E25: alineación de 16599928 sin Moha Keita (derivada localmente) | convocatoria 1744 `retired`, conserva su última alineación (1328); 16599928 fuera de sus partidos; 3217 rutas = camino independiente; 10,7 s |
| E26: la alineación original otra vez | la misma fila 1744 `active`; dos transiciones; 3217 rutas = camino independiente; 10,9 s |
| Repetir E26 | `no_changes`, sin transición nueva |
| Arranque de la API en modo SQL | 1,3 s (comprobación de reglas incluida) |

Una importación tarda unos 11 s en la base real: comprobación previa (~3,3 s, sincronización en seco), copia,
lectura de ficheros, sincronización (~3,3 s) y verificación antes de confirmar (~3,3 s).

### 9.4 Límites

- La comprobación de uso solo existe en Windows; en otros sistemas `restaurar` y `migrar` se niegan siempre.
- Comprueba los archivos abiertos en ese instante: un proceso que abra la base justo después no se detecta. La API no
  mantiene la base abierta tras arrancar (6.2): hay que pararla antes.
- Cada escritura cuesta dos sincronizaciones en seco más la propia (~11 s en la base real); crece con la evidencia.
- Una convocatoria retirada solo guarda su último estado (9.1); el historial está en las observaciones.
- No hay orden para retirar declaraciones de equipo ni para revertir sustituciones (fuera de alcance).
- `downgrade` de `0003` recrea `team_match_stat` e `incident` vacías; `downgrade` de `0004` se detiene si hay
  retiradas. Para volver atrás se restaura la copia `antes-de-migrar-0002` (sección 10).
- La API sigue sin recarga en caliente.

---

## 10. Procedimiento de activación (para revisar; no ejecutado)

Base activa: `C:\Users\broke\dev\datos-importados\base\real-jaen.sqlite` (revisión 0002). Desde `api-real-jaen`:

1. **Parar la API** del puerto 8001 y cualquier otro proceso que lea la base. El dashboard puede seguir abierto: dará
   errores hasta que la API vuelva.
2. Anotar la huella del archivo:
   `certutil -hashfile C:\Users\broke\dev\datos-importados\base\real-jaen.sqlite SHA256` (hoy `85273b1f…96e6ba`).
3. Migrar. Hace la copia verificada `copias\real-jaen.<fecha>.antes-de-migrar-0002.sqlite`, se niega si la base está
   en uso, comprueba antes de confirmar y verifica después:
   `.venv\Scripts\python -m persistencia migrar --base C:\Users\broke\dev\datos-importados\base\real-jaen.sqlite`
   Esperado: `"revision": "0004"` y `"verificacion": {"ok": true, "problemas": [], "cambios_en_seco": {}, ...}`.
4. Opcional: `.venv\Scripts\python -m persistencia verificar --base C:\Users\broke\dev\datos-importados\base\real-jaen.sqlite`
   (código de salida 0).
5. Arrancar la API en modo SQL, como hasta ahora:
   `.venv\Scripts\python api.py --almacen sql --base C:\Users\broke\dev\datos-importados\base\real-jaen.sqlite --ambito ambito.json --port 8001`
   y revisar el dashboard. Las 3217 rutas deberían ser idénticas a las actuales (comprobado en copia).
6. **Marcha atrás**, si hiciera falta: parar la API; volver al código de la fase 3 (commit `c56dc83`) para servir la
   revisión 0002; restaurar con el código de la fase 4 (que es el que tiene `restaurar`) indicando la revisión:
   `.venv\Scripts\python -m persistencia restaurar --copia C:\Users\broke\dev\datos-importados\base\copias\real-jaen.<fecha>.antes-de-migrar-0002.sqlite --base C:\Users\broke\dev\datos-importados\base\real-jaen.sqlite --confirmar --revision 0002`
   (deja además una copia `antes-de-restaurar` de la base migrada) y después arrancar la API con el código de la
   fase 3.

Tras activar, una importación nueva sería
`.venv\Scripts\python -m persistencia importar --base C:\Users\broke\dev\datos-importados\base\real-jaen.sqlite --exportacion EQUIPO=RUTA …`,
con la API cerrada antes de comenzar y arrancándola después de terminar.
