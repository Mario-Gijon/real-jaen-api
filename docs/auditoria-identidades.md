# Auditoría de identidades de Real Jaén

**Fecha de auditoría:** 2026-11-01
**Base inspeccionada:** SQLite activa del backend local, esquema Alembic `0004`
**Código de referencia:** API `main`, commit `0d2e24bdeb0256b824de01d4820b26c5b1b956f7`; dashboard en `refactor/dashboard-architecture`
**Alcance:** modelo, sincronización, reconstrucción, lecturas API, IDs del dashboard y una copia coherente de los datos locales.

## Resumen ejecutivo

- **Correcto y verificado:** las seis entidades principales (`team`, `player`, `match`, `competition`, `season`, `stage`) tienen PK interna, UUID persistente y una correspondencia `origin` de SofaScore. En la copia no hay UIDs repetidos, IDs externos vivos compartidos, entidades huérfanas, fusiones ni ciclos; SQLite pasa `integrity_check` y `foreign_key_check`.
- **Problema confirmado:** 97 de 3.853 convocatorias conservan un `teamId` crudo de la alineación que no coincide con el equipo del lado del partido (41 partidos). La FK interna `call_up.team_id` sí coincide con el lado en las 3.853 filas. La API conserva el valor de origen y marca la discrepancia como advertencia; no lo trata como una reasignación de identidad.
- **Posible riesgo:** los UUID se generan determinísticamente a partir del tipo y del ID de SofaScore. Son estables para la identidad de SofaScore ya observada, pero no deduplican por sí solos a una entidad que otro proveedor describa con otro ID.
- **Posible riesgo:** el dashboard no utiliza el UUID del backend. Las rutas de partidos, jugadores y casi todos los equipos continúan dependiendo de IDs derivados de SofaScore. La API REST también publica IDs numéricos de SofaScore, no el UUID.
- **Posible riesgo:** el esquema permite registrar decisiones de identidad, pero no existe una herramienta operativa para gestionarlas y la base inspeccionada no contiene ninguna. Tampoco hay entidades normalizadas con ID estable para acciones de partido; las incidencias se preservan como observaciones originales.

La comprobación de datos se hizo sobre una copia temporal y aislada, obtenida mediante la API de backup de SQLite desde una conexión `mode=ro` a `/state/real-jaen.sqlite` dentro del contenedor backend. No se copió el archivo activo directamente ni se modificaron la base, su WAL/SHM, el volumen o los contenedores. La copia tenía revisión Alembic `0004`, `PRAGMA integrity_check = ok` y cero filas en `PRAGMA foreign_key_check`. `alembic_version` es la fuente de revisión usada por la aplicación; `PRAGMA user_version=0` no representa la revisión de Alembic.

## 1. Modelo e identificadores

En el sistema hay cuatro conceptos que no deben confundirse:

1. **ID interno de fila (`id`):** entero y PK de SQLite. Sirve para las uniones y las FK dentro de esa base. No es un ID de integración entre servicios.
2. **UID persistente (`uid`):** UUIDv5 único almacenado en la entidad. El código lo asigna al crearla y la sincronización conserva la fila y su UID cuando vuelve a encontrar el mismo ID externo.
3. **ID externo de proveedor:** texto compuesto por `provider`, `namespace` y `external_id`, asociado a una entidad en una tabla externa tipada. `origin` indica el ID con el que se creó la entidad; `verified` y `rejected` están previstos para asociaciones decididas.
4. **ID público del dashboard:** cadena de URL o contrato TypeScript. Hoy se deriva principalmente del ID de SofaScore y no es el UID.

Las seis tablas de entidad son `team`, `player`, `match`, `competition`, `season` y `stage`. Cada una tiene sus columnas `id`, `uid`, `status`, `merged_into_id` y `created_in_run`. Para el proveedor actual, la correspondencia externa se conserva respectivamente en `team_external_id`, `player_external_id`, `match_external_id`, `competition_external_id`, `season_external_id` y `stage_external_id`.

`competition` representa el torneo general; `season` su temporada y `stage` el grupo o etapa. El código conserva esas distinciones y no infiere una competición desconocida a partir del nombre. Los partidos relacionan local, visitante, competición, temporada y etapa mediante FK internas en `match_state`, y conservan a su lado los IDs externos de origen.

### UID y garantía de estabilidad

`persistencia/carga.py` fija el espacio UUIDv5 del proyecto con `uuid.uuid5(uuid.NAMESPACE_URL, 'urn:api-real-jaen:identidad')`; para una entidad de origen genera `uuid.uuid5(ESPACIO_UID, 'sofascore:<tipo>:<id externo>')`. La asignación se realiza al insertar y no se recalcula al sincronizar. La base impone unicidad y longitud 36; no valida por sí sola la variante UUID ni que el valor derive de la correspondencia externa.

La sincronización (`persistencia/sincronizacion.py`) usa el ID externo como clave estable de las seis entidades, no el nombre. Conserva las filas existentes y sus IDs internos; una entidad nueva recibe un nuevo ID y UID. La prueba de carga verifica el UID UUIDv5. Las pruebas de sincronización comprueban que al importar versiones nuevas se conservan los IDs existentes y que las entidades nuevas reciben IDs posteriores. En la copia real se recalculó el UUIDv5 de las **968** entidades de origen: los 30 equipos, 536 jugadores, 393 partidos y 3 entidades de cada uno de los otros tres tipos coinciden.

Esto cubre volver a importar datos con el mismo ID de SofaScore y cambios de nombre: la identidad no se deduce del nombre. Una nueva observación de ficha con el mismo ID externo se asocia a la misma entidad; los perfiles y atributos se guardan como observaciones. El equipo de un jugador se expresa por convocatoria de partido, no como una propiedad global permanente. Un ID de jugador distinto del proveedor crea otra entidad de origen: el importador no hace coincidencia difusa por nombre ni tiene hoy una herramienta de reasignación.

### Inventario por entidad: SQLite real

Todos los recuentos siguientes corresponden a la copia auditada de la SQLite activa.

| Entidad | Total / activas / fusionadas | Con PK / UID | IDs externos `origin` | Sin correspondencia de origen | Proveedor y espacio de nombres |
|---|---:|---:|---:|---:|---|
| Equipo (`team`) | 30 / 30 / 0 | 30 / 30 | 30 | 0 | 30 `sofascore`, namespace vacío |
| Jugador (`player`) | 536 / 536 / 0 | 536 / 536 | 536 | 0 | 536 `sofascore`, namespace vacío |
| Partido (`match`) | 393 / 393 / 0 | 393 / 393 | 393 | 0 | 393 `sofascore`, namespace vacío |
| Competición (`competition`) | 3 / 3 / 0 | 3 / 3 | 3 | 0 | 3 `sofascore`, namespace vacío |
| Temporada (`season`) | 3 / 3 / 0 | 3 / 3 | 3 | 0 | 3 `sofascore`, namespace vacío |
| Grupo/etapa (`stage`) | 3 / 3 / 0 | 3 / 3 | 3 | 0 | 3 `sofascore`, namespace vacío |

En cada tabla: 0 grupos de UID duplicado, 0 claves externas vivas asociadas a más de una entidad, 0 FK externas huérfanas y 0 cadenas de fusión inválidas o circulares. Cada entidad tiene exactamente un ID de origen; las seis tablas externas solo contienen estado `origin` en esta copia, sin `verified` ni `rejected`. Las restricciones únicas cubren `(provider, namespace, external_id)` para filas no rechazadas y una fila `origin` por entidad.

## 2. Correspondencia de ejemplo

Los valores muestran que el ID interno, el UID, el ID del proveedor y el ID público tienen espacios y fines distintos. Los UUID de la tabla real se comprobaron contra el cálculo UUIDv5 actual.

| Entidad | PK SQLite | UID | ID SofaScore | ID público del dashboard |
|---|---:|---|---:|---|
| Partido | `1` | `6bee9b27-5d44-5928-b2ca-cd099db1d051` | `16599363` | `sofascore-match-16599363` |
| Real Jaén | `3` | `d4090773-3958-5a9b-956a-753c11a2a4c1` | `2841` | `team-real-jaen` |
| Jugador | `1` | `d61147d7-2e47-562b-b0c1-4404f0290cd1` | `3007` | `sofascore-player-3007` |
| Competición | `3` | `423fe481-afab-5d45-a2ad-2737a56087cc` | `17073` | No se publica como entidad UUID |
| Temporada | `1` | `2b661197-cb2d-5860-b05d-4e174d262bd4` | `97382` | Forma parte del scope observado |
| Grupo/etapa | `1` | `c7db9f6a-2873-502f-87c4-a8e5d11fef89` | `94191` | Forma parte del scope observado |

Los números de PK se muestran solo para ilustrar la relación dentro de esta base; no deben codificarse en otro servicio.

## 3. Relaciones y datos derivados

### Partidos y contexto deportivo

**Correcto y verificado:** hay 393 partidos y 393 filas `match_state`; ningún partido carece de estado. Los 393 tienen equipos local y visitante distintos, y están poblados sus IDs internos y externos de etapa, temporada y competición. Las correspondencias externas de ambos equipos y de competición/temporada/etapa coinciden en todos los casos con el estado del partido. No se hallaron FK inexistentes ni una referencia que dependa de comparar nombres.

### Convocatorias, jugadores y estadísticas

**Correcto y verificado:** la copia contiene 3.853 filas `call_up`; todas están `active`, ninguna tiene `player_id` nulo y cada una coincide con la alineación seleccionada de su partido. No hay claves duplicadas `(match_id, side, external_player_id)`. Los IDs externos de jugador coinciden con su entidad resuelta. `call_up_stat` contiene 113.735 valores JSON válidos, sin padres fuera de la vista `call_up_vigente`. La vista ofrece las 3.853 convocatorias activas; `call_up_change` está vacía, de modo que esta base no tiene convocatorias retiradas ni reactivadas.

La PK de `call_up` identifica una fila operativa de convocatoria; su clave lógica es partido, lado y jugador. No es una identidad permanente de jugador. Las pruebas cubren retirada y reactivación conservando la fila, y cambio de lado retirando la antigua y creando otra. La API lee `call_up_vigente`, no suma una convocatoria retirada.

**Problema confirmado:** en 97 filas de 41 partidos, `call_up.external_team_id` (el `teamId` preservado del payload de alineación) no coincide con el ID externo del equipo que aparece en `match_state` para ese lado. En las 97, el FK `call_up.team_id` sí señala al equipo interno correcto para el lado. `api.actuacion` produce `source_team_mismatch` para el valor de origen y cuenta advertencias de equipo; la capa de persistencia conserva el dato original. Es, por tanto, una discrepancia fuente/resolución que debe revisarse contra las observaciones originales antes de decidir si hace falta una corrección de identidad. Esta auditoría no modifica ni descarta esos valores.

### Alineaciones, incidencias y observaciones

**Correcto y verificado:** las alineaciones se conservan como observaciones `lineups`; las convocatorias y sus estadísticas se derivan de la observación seleccionada con FK a la observación correspondiente. Las secciones originales, incluidas estadísticas e incidencias, se guardan en `content` y siguen disponibles como payload. La base contiene 5.308 observaciones, 1.817 contenidos y 477 observaciones `incidents`.

**Posible riesgo:** en el esquema vigente `0004` no existe una tabla `incident` normalizada; la migración `0003` la retiró y el payload de incidencias queda como evidencia original. La API deriva del payload algunos goles, asistencias y sustituciones usados en sus respuestas, pero no hay una identidad persistente propia para cada acción, tarjeta o evento temporal que otro servicio pueda referenciar. Los IDs externos de jugador en las incidencias no se almacenan como FK por acción.

Las filas de `observation` tienen una PK de ingestión y conservan proveedor, IDs externos de partido/jugador cuando aplican, fichero, contenido, clave y subclave. Son evidencia trazable, no IDs de dominio apropiados para enlazar clips entre servicios.

### Partidos aplazados o sustituidos

La tabla `local_replacement` registra una decisión local auditada entre dos partidos distintos; no fusiona sus entidades ni UIDs. La base contiene dos relaciones activas, ambas con correspondencias externas coincidentes y sin autorreferencias:

| Evento sustituido | Evento vigente | Estado observado |
|---:|---:|---|
| `16599956` | `17118209` | aplazado → finalizado |
| `16599919` | `16969545` | aplazado → finalizado |

La API conoce el enlace para navegación y consulta. Un vídeo asociado al encuentro vigente debería apuntar al UID del evento vigente y guardar, cuando proceda, una relación explícita al evento sustituido; no debe tratar los dos IDs como una misma fila.

### Decisiones de identidad

`identity_decision` y `identity_decision_subject` existen en el esquema para registrar fusión, división, reasignación/rechazo de IDs externos y reversión con motivo, responsable y evidencia. **La base contiene 0 decisiones y 0 sujetos.** El propio modelo indica que aún no hay herramienta; `persistencia/decisiones.py` gestiona sustituciones locales de partido, no fusiones de identidad.

El esquema impone estado `merged`, FK a la entidad destino y que una entidad no se fusione consigo misma; la aplicación documenta la compresión de cadenas y validación transaccional. El esquema no impide por sí solo todos los ciclos o un destino que luego se fusione. No hay tales casos en los datos auditados. La prueba existente de fusión es una fila manual en una base de prueba, no un flujo administrativo listo para producción.

## 4. Qué IDs usan API y dashboard

La API Python sirve en sus rutas IDs externos numéricos de SofaScore (`match_id`, `team_id`, `player_id`, `tournament_id`, `season_id`); los serializadores reconstruyen esos IDs desde las tablas de correspondencia. No exponen el `uid` UUID de SQLite ni las PK internas. Las rutas actuales incluyen, por ejemplo, `/api/v1/matches/{id}` y `/api/v1/players/{id}` con el identificador que el adaptador reconoce como ID de origen.

`dashboard/src/server/local-api/identifiers.ts` convierte:

- Partido: SofaScore numérico ↔ `sofascore-match-{id}`.
- Jugador: SofaScore numérico ↔ `sofascore-player-{id}`.
- Equipo: SofaScore numérico ↔ `sofascore-team-{id}`, salvo el ID especial `team-real-jaen` para el origen `2841`.
- Scope de competición observada: `sofascore-scope-{tournamentId}-{seasonId}`. Es una clave de scope basada en IDs externos, no el UID de `competition`, `stage` o `season`.

La navegación y los filtros conservan esos identificadores públicos como cadenas. El adaptador los traduce a los enteros que consume la API; no usa UUIDs internos.

La correspondencia explícita de equipos en el dashboard contiene 20 equipos. Todos coinciden con IDs de equipo activos declarados en SQLite; no hay declaraciones propias sin mapa ni entradas del mapa sin declaración propia. Diez equipos adicionales que están en SQLite carecen de mapa/declaración propia activa: `6588`, `24352`, `24354`, `44230`, `189948`, `202073`, `263096`, `295634`, `327214`, `493345`. Esto no es una referencia rota: pueden aparecer como rivales y mantener sus entidades, pero el dashboard no los trata como equipos propios analizables. Esa condición explícita requiere tanto una correspondencia como una declaración de exportación propia; nunca se deduce por nombre.

## 5. Cambios de identidad y segundo proveedor

| Caso | Comportamiento implementado | Evidencia/límite |
|---|---|---|
| Reimportar el mismo evento con el mismo ID SofaScore | Reutiliza la entidad externa y conserva el ID interno/UID; repetir un fichero conocido es idempotente por su SHA256. | Sincronización por ID externo y pruebas de reimportación/IDs conservados. |
| Cambia el nombre de un equipo | No crea otra identidad mientras conserve el mismo ID SofaScore; el nombre no es clave de entidad. | Las claves de entidad de `sincronizacion.py` no incluyen nombre. |
| Jugador cambia de equipo | Sigue siendo el mismo jugador si mantiene su ID SofaScore; equipo/lado se registra por partido en cada convocatoria. | `call_up` FK a jugador, equipo, partido y lado; no es “equipo actual” global. |
| Aparece una ficha nueva con el mismo ID de jugador | Se registra como nueva observación del mismo jugador de origen; no crea identidad nueva. | `player_profile` y atributos tienen trazabilidad de observación. |
| Mismo jugador con un ID externo nuevo | Se crea una entidad de origen nueva; no hay deduplicación automática por nombre. | No existe flujo productivo para decisión de identidad. |
| Partido aplazado reprogramado con otro ID externo | Son dos entidades `match`; `local_replacement` conserva un enlace explícito entre ellas. | Dos pares activos en los datos reales; no es un merge. |
| Fusionar o dividir una entidad | El esquema y la reconstrucción prevén decisiones auditables; la escritura/gestión operativa no está implementada. | 0 decisiones reales; la prueba de merge usa SQL manual sobre fixture. |
| Incorporar segundo proveedor | Las tablas externas admiten `provider` y `namespace`, pero la carga/sincronización actual trabaja con SofaScore y el namespace vacío. | No hay IDs `verified`, datos de otro proveedor ni herramienta de resolución probada. |

**Conclusión de estabilidad:** los UUID actuales son estables para los IDs de SofaScore ya enlazados, pero nacen de esos IDs. No se puede inferir que dos IDs de proveedores distintos sean la misma identidad. La unicidad `provider + namespace + external_id` permite guardar correspondencias futuras; no constituye por sí sola un proceso de resolución, revisión, fusión, división o reversión.

## 6. Integridad y hallazgos

| Hallazgo | Clasificación | Evidencia |
|---|---|---|
| Unicidad de IDs de entidad y relaciones internas | **Correcto y verificado** | 968 entidades; PK/UID y `origin` completo; cero duplicados/huérfanos; integridad SQLite/FK correctas. |
| UUIDv5 de las entidades activas de la copia | **Correcto y verificado** | 968 de 968 coinciden con el algoritmo actual. |
| Coherencia de partido, lados y ámbito | **Correcto y verificado** | 393/393 con estado y referencias externas/internas coincidentes. |
| Convocatorias activas y sus stats | **Correcto y verificado** | 3.853 activas; selección, jugadores y lados coinciden; 113.735 stats JSON válidos. |
| `external_team_id` de alineación frente al lado del partido | **Problema confirmado** | 97 discordancias en 41 partidos; el FK resuelto sí coincide y la API lo reporta como advertencia. |
| Entidades fusionadas o decisiones ya aplicadas | **Correcto y verificado** | No hay fusiones; cero decisiones y sujetos. |
| Coincidencia entre proveedor futuro e identidad existente | **Posible riesgo** | El esquema lo permite; no hay asociaciones reales de otro proveedor ni resolución operativa. |
| Si IDs de otro proveedor serían las mismas identidades | **No verificable con la evidencia disponible** | No hay datos ni asociaciones de segundo proveedor en esta base. |
| Acciones de incidencias como entidades con UID | **Posible riesgo** | Se preserva el payload, pero `incident` se retiró en `0003` y no hay UID por acción. |

No se encontró corrupción de relaciones. La discrepancia de `external_team_id` es real en la evidencia y merece seguimiento, pero no demuestra por sí sola qué dato original es erróneo ni justifica cambiar un ID: habría que contrastar cada observación original y, si corresponde, registrar una corrección soportada. En ningún caso se consideraron dos entidades duplicadas solo porque compartan nombre.

## 7. Recomendaciones antes de FastAPI

1. Definir el contrato público canónico que FastAPI ofrecerá para entidades y conservar compatibilidad con rutas numéricas actuales durante la transición. El candidato existente es el UUID persistente, pero debe publicarse explícitamente por API antes de que otro servicio lo utilice.
2. Mantener PKs enteras solo para almacenamiento y uniones internas. No compartirlas como identidad entre procesos ni usarlas en URLs de servicio de vídeo.
3. Documentar por endpoint si cada entero de la API es ID externo de SofaScore; nombrar por separado `competition` y `stage/tournament` para que el scope no mezcle sus IDs.
4. Reemplazar gradualmente la tabla fija de IDs de equipos del dashboard por una correspondencia/declaración servida por el API, preservando la regla de que “aparece como rival” no implica “equipo analizable”.
5. Implementar y probar una herramienta auditada de decisiones antes de admitir otra fuente: asociaciones verificadas/rechazadas, merge/split/reasignación/reversión, resolución de cadenas, validación de ciclos y exportación/reaplicación desde respaldo.
6. Revisar las 97 discrepancias `source_team_mismatch` en las observaciones y registrar únicamente conclusiones respaldadas por la fuente. No sobrescribir la evidencia original.
7. Añadir pruebas con IDs nuevos de proveedor, colisiones de namespace, cambio de ID externo y migración de una asociación entre entidades, además de verificar que el UID publicado permanece estable.

## 8. Recomendaciones para el servicio de vídeo

- **Vídeo ↔ partido:** enlazar cada grabación al UID público canónico del partido que exponga la API. Conservar también proveedor/ID externo y procedencia al importar. Para partidos sustituidos, almacenar el UID de ambos partidos y la relación explícita de sustitución; no unirlos por nombre, fecha o marcador.
- **Varias grabaciones:** cada grabación debe tener identidad propia en el servicio de vídeo y apuntar a un UID de partido. El UID del partido identifica el encuentro, no el archivo de vídeo ni la cámara.
- **Clip ↔ acción:** un clip debe identificar una grabación y un intervalo temporal. Si se requiere navegar a una acción deportiva, hace falta una identidad persistente de evento (o una referencia externa verificable). El payload `incidents` de hoy es evidencia versionada, no un catálogo de acciones con UID.
- **Jugador detectado:** resolver a UID de jugador mediante una correspondencia explícita del API; no guardar `sofascore-player-*` ni una PK SQLite como identidad canónica. Registrar también incertidumbre/procedencia de la detección.
- **Dorsal:** el número solo tiene significado con equipo y periodo/partido. La convocatoria ya relaciona jugador, equipo del lado, partido y `shirt_number`; permite contrastar una detección para ese partido si el origen lo reporta. No ofrece una asignación global o temporal de dorsal independiente de las convocatorias.
- **Auditoría:** mantener separado el hecho detectado, el intervalo de vídeo, la resolución propuesta a jugador/acción y cualquier decisión humana. Las correcciones deben ser trazables y reversibles.

## 9. Prioridades

1. Revisar la evidencia de las 97 discrepancias de equipo y decidir si solo son valores fuente inconsistentes o si alguna requiere una decisión auditada.
2. Acordar y exponer un UID canónico por API sin cambiar todavía los IDs internos ni romper las URLs existentes.
3. Implementar el flujo operativo de correspondencias de identidad antes de integrar datos de un segundo proveedor.
4. Formalizar IDs de acciones únicamente si vídeo necesita vínculos estables a goles, tarjetas, sustituciones u otros incidentes.
5. Integrar grabaciones y clips con UIDs de partido/jugador y procedencia explícita, manteniendo grabación, clip y acción como identidades distintas.

## Fuentes inspeccionadas

- `persistencia/modelos.py`, `carga.py`, `reconstruccion.py`, `sincronizacion.py`, `lectura.py`, `decisiones.py` y migraciones `0001`–`0004`.
- `api.py`, contratos/respuestas de la API, `docs/persistencia-sqlite.md` y `docs/fase4-sincronizacion.md`.
- `dashboard/src/server/local-api/identifiers.ts`, adaptadores locales, contratos y `src/lib/navigation/`.
- Pruebas de carga, sincronización, persistencia y normalización de convocatorias; se inspeccionó su cobertura, sin ejecutar adquisición ni modificar datos.
