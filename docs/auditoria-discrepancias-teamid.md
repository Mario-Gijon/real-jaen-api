# Auditoría de discrepancias `source_team_mismatch`

**Fecha:** 2026-11-01
**Repositorio y rama:** API, `audit/identidades`
**Commit de partida:** `3908dae3c413d78a38b77432cf9546870af90cfe`
**Base:** copia coherente de la SQLite activa, esquema Alembic `0004`.

## Resumen

Se analizaron las 97 filas de `call_up` afectadas, en 41 partidos y con 38 IDs de jugador distintos. En las 97, el `teamId` guardado como evidencia coincide con el `teamId` de la entrada original del jugador en el payload de la observación seleccionada. También en las 97, la FK interna `call_up.team_id` coincide con el equipo del lado (`home` o `away`) del partido. Por tanto, el desacuerdo está entre dos valores que llegan por separado en el payload: el lado en que está anidada la entrada y su campo `teamId`.

Resultados principales:

- **Correcto y verificado:** la extracción usa el equipo del lado del partido para `team_id`, conserva el campo de origen aparte y emite una advertencia. La persistencia y la reconstrucción preservan ambos valores; no se ha encontrado una asignación interna errónea.
- **Correcto y verificado:** para cada una de las 97 convocatorias existen observaciones anteriores de la misma entrada; todas conservan el mismo `teamId` y el mismo hash de contenido que la observación seleccionada. No aparece una versión anterior con un valor diferente. Como los hashes son iguales, esas repeticiones no son evidencia independiente de un cambio deportivo o temporal.
- **Correcto y verificado:** 2 de los 24 IDs crudos están en el catálogo actual de equipos; 22 no están registrados. En una fila el `teamId` crudo coincide con el equipo opuesto del partido. En las 96 restantes no coincide con ninguno de los dos participantes.
- **No verificable con la evidencia disponible:** si `teamId` en el payload significa el equipo que representa al jugador en ese encuentro, su equipo de ficha/inscripción, un ID histórico o algún otro concepto del proveedor. Tampoco puede establecerse por qué los 22 IDs desconocidos no aparecen en las 30 entidades de equipo de esta base.

**Conclusión operativa:** no hay motivo demostrado para modificar IDs, asociaciones ni datos. La relación interna actual por lado es consistente y la advertencia conserva la discrepancia fuente. Se recomienda mantener esa política y no crear correspondencias de equipo a partir de esos valores sin evidencia adicional. La incertidumbre semántica no bloquea la migración a FastAPI si se conservan el lado, el equipo resuelto, el valor crudo y la advertencia.

## Método y alcance de la evidencia

La copia se obtuvo con `sqlite3.Connection.backup` desde una conexión `mode=ro` a `/state/real-jaen.sqlite` dentro del contenedor backend. No se abrió la base activa en modo escritura, no se copió directamente el archivo activo y no se modificaron SQLite, WAL/SHM, volúmenes ni contenedores. La copia temporal pasó `PRAGMA integrity_check` (`ok`), `PRAGMA foreign_key_check` (0 filas) y reportó revisión Alembic `0004`. Se eliminó después del análisis.

La comparación se limita a los contenidos ya guardados en SQLite, sus observaciones, selecciones, archivos de importación y código local. No se consultó SofaScore ni otra fuente externa. En las tablas siguientes, `equipo del lado` y `equipo opuesto` son IDs externos SofaScore que aparecen en el estado del partido; `ID crudo` es el valor de `call_up.external_team_id` preservado desde el payload.

## Recuentos y distribución

| Medida | Resultado |
|---|---:|
| Filas `call_up` con `source_team_mismatch` | 97 |
| Partidos afectados | 41 |
| IDs de jugador afectados | 38 |
| Casos en `home` / `away` | 53 / 44 |
| IDs crudos diferentes | 24 |
| IDs crudos presentes en `team_external_id` | 2 (2 filas) |
| IDs crudos no registrados en `team_external_id` | 22 (95 filas) |
| `teamId` crudo igual al equipo opuesto | 1 |
| `teamId` crudo igual al equipo del lado | 0 |
| FK interna de convocatoria que no coincide con el lado | 0 |

### Tipo de conflicto

La clasificación compara el valor crudo con los dos equipos participantes del partido y con el catálogo actual de equipos.

| Tipo | Filas | Partidos | Jugadores | Lectura respaldada |
|---|---:|---:|---:|---|
| Coincide con el equipo opuesto | 1 | 1 | 1 | El campo apunta al otro participante; no determina si el lado o `teamId` es el dato erróneo. |
| ID registrado, pero ajeno a ambos participantes | 1 | 1 | 1 | Existe una entidad de equipo actual (`44230`, CD Tudelano), pero no aparece en ese partido. |
| ID sin catálogo y ajeno a ambos participantes | 95 | 40 | 36 | No se puede identificar el equipo o significado del ID a partir de esta SQLite. |

### Concentración por partido

| Discrepancias en un partido | Número de partidos | Filas afectadas |
|---:|---:|---:|
| 1 | 13 | 13 |
| 2 | 12 | 24 |
| 3 | 9 | 27 |
| 4 | 4 | 16 |
| 5 | 2 | 10 |
| 7 | 1 | 7 |
| **Total** | **41** | **97** |

### Partidos afectados

Los recuentos separan filas `home` y `away`; los dos IDs de equipo son los participantes del estado de partido.

| Partido | Equipo local | Equipo visitante | Filas `home` | Filas `away` | Total |
|---:|---:|---:|---:|---:|---:|
| `16599359` | `217210` | `24322` | 1 | 1 | 2 |
| `16599361` | `24326` | `263407` | 4 | 0 | 4 |
| `16599362` | `24323` | `24368` | 2 | 1 | 3 |
| `16599363` | `135672` | `2841` | 3 | 0 | 3 |
| `16599365` | `24265` | `24363` | 2 | 0 | 2 |
| `16599366` | `202074` | `24338` | 0 | 2 | 2 |
| `16599367` | `2840` | `2815` | 1 | 1 | 2 |
| `16599368` | `43751` | `5069` | 0 | 4 | 4 |
| `16599370` | `24322` | `135672` | 2 | 0 | 2 |
| `16599371` | `24329` | `2840` | 0 | 1 | 1 |
| `16599372` | `2877` | `24326` | 0 | 2 | 2 |
| `16599388` | `2841` | `24265` | 0 | 1 | 1 |
| `16599554` | `5069` | `263407` | 3 | 0 | 3 |
| `16599697` | `24338` | `4489` | 1 | 0 | 1 |
| `16599713` | `2815` | `24323` | 0 | 2 | 2 |
| `16599763` | `24265` | `135672` | 1 | 0 | 1 |
| `16599792` | `5069` | `24326` | 5 | 2 | 7 |
| `16599806` | `24363` | `24323` | 0 | 2 | 2 |
| `16599822` | `24338` | `24368` | 1 | 0 | 1 |
| `16599834` | `217210` | `5069` | 0 | 3 | 3 |
| `16599843` | `24326` | `4487` | 3 | 0 | 3 |
| `16599856` | `24323` | `24322` | 2 | 1 | 3 |
| `16599870` | `135672` | `2877` | 1 | 0 | 1 |
| `16599875` | `24265` | `24338` | 1 | 1 | 2 |
| `16599881` | `24326` | `2815` | 2 | 1 | 3 |
| `16599884` | `24323` | `24329` | 2 | 0 | 2 |
| `16599892` | `24368` | `5069` | 0 | 3 | 3 |
| `16599906` | `24322` | `2840` | 1 | 0 | 1 |
| `16599914` | `24265` | `24326` | 2 | 3 | 5 |
| `16599921` | `24338` | `24323` | 1 | 2 | 3 |
| `16599934` | `2840` | `24326` | 0 | 1 | 1 |
| `16599937` | `5069` | `24323` | 1 | 1 | 2 |
| `16599951` | `24326` | `24338` | 3 | 2 | 5 |
| `16599953` | `24323` | `2841` | 2 | 0 | 2 |
| `16599962` | `24368` | `2815` | 0 | 1 | 1 |
| `16599967` | `24322` | `24368` | 1 | 0 | 1 |
| `16599969` | `5069` | `24322` | 4 | 0 | 4 |
| `16599972` | `43751` | `24265` | 0 | 1 | 1 |
| `16599977` | `24265` | `24323` | 0 | 1 | 1 |
| `16599988` | `24338` | `24329` | 1 | 0 | 1 |
| `16969545` | `24363` | `5069` | 0 | 4 | 4 |

### Equipo que determina el lado de la convocatoria

El recuento agrupa por el equipo del partido derivado de `side`; “partidos” cuenta partidos distintos afectados por al menos una convocatoria de ese equipo.

| Equipo | Filas `home` | Filas `away` | Total | Partidos |
|---:|---:|---:|---:|---:|
| `5069` | 13 | 14 | 27 | 8 |
| `24326` | 12 | 8 | 20 | 8 |
| `24323` | 8 | 8 | 16 | 9 |
| `24338` | 4 | 5 | 9 | 7 |
| `24265` | 6 | 2 | 8 | 6 |
| `24322` | 4 | 2 | 6 | 5 |
| `135672` | 4 | 0 | 4 | 2 |
| `2815` | 0 | 3 | 3 | 3 |
| `2840` | 1 | 1 | 2 | 2 |
| `217210` | 1 | 0 | 1 | 1 |
| `24368` | 0 | 1 | 1 | 1 |

### IDs crudos de `teamId`

El catálogo se comprobó con `team_external_id` (`provider='sofascore'`, namespace vacío, estado `origin`). “Registrado” solo acredita que ese ID tiene una entidad en esta SQLite; no acredita qué significaba en el payload.

| ID crudo | Filas | Partidos | Jugadores | Catálogo actual |
|---:|---:|---:|---:|---|
| `1119104` | 17 | 8 | 4 | no registrado |
| `490780` | 12 | 7 | 7 | no registrado |
| `2829` | 11 | 8 | 2 | no registrado |
| `549462` | 9 | 9 | 1 | no registrado |
| `2819` | 7 | 7 | 2 | no registrado |
| `368757` | 7 | 6 | 3 | no registrado |
| `506673` | 7 | 7 | 1 | no registrado |
| `263785` | 4 | 4 | 1 | no registrado |
| `317628` | 3 | 3 | 1 | no registrado |
| `483874` | 3 | 2 | 2 | no registrado |
| `1089499` | 2 | 2 | 1 | no registrado |
| `202077` | 2 | 2 | 1 | no registrado |
| `477045` | 2 | 2 | 1 | no registrado |
| `120854` | 1 | 1 | 1 | no registrado |
| `154810` | 1 | 1 | 1 | no registrado |
| `24363` | 1 | 1 | 1 | registrado: UE Sant Andreu (entidad 17) |
| `263776` | 1 | 1 | 1 | no registrado |
| `275789` | 1 | 1 | 1 | no registrado |
| `2836` | 1 | 1 | 1 | no registrado |
| `37057` | 1 | 1 | 1 | no registrado |
| `379297` | 1 | 1 | 1 | no registrado |
| `44230` | 1 | 1 | 1 | registrado: CD Tudelano (entidad 20) |
| `826809` | 1 | 1 | 1 | no registrado |
| `853702` | 1 | 1 | 1 | no registrado |

### Archivos de exportación, hashes y fechas

`E##` es la etiqueta persistida en `source_file`; `archivo` es el nombre presentado al importar. El hash es el SHA256 registrado para el archivo, no una huella nueva calculada sobre una copia parcial. La observación de cada fila se cita por ID y por `E##:línea`.

| Etiqueta | Archivo presentado | SHA256 | Filas | Partidos | `fetched_at` mínimo | `fetched_at` máximo |
|---|---|---|---:|---:|---|---|
| `E40` | `exportaciones-15.jsonl` | `d4e4a1d1d5b24d26fcd066878951c19e6d40c12fcbcd42f6bfd28d9287d5b52c` | 4 | 1 | `2026-10-22T16:48:47+00:00` | `2026-10-22T16:48:47+00:00` |
| `E46` | `exportaciones-0.jsonl` | `b3e8536210a6e8db43798e5050de13a3d8aefaeff1eaf079366e770b034e73b9` | 2 | 1 | `2026-10-22T16:53:05+00:00` | `2026-10-22T16:53:05+00:00` |
| `E48` | `exportaciones-5.jsonl` | `a2be0161f62f343c4ce6fa6f192932cf0fce06c84e0189598c8dada15021ba2f` | 1 | 1 | `2026-10-22T16:45:50+00:00` | `2026-10-22T16:45:50+00:00` |
| `E49` | `exportaciones-7.jsonl` | `fc0c4daaccd0cd459f7ca1d35265a019ef89f4d75b7fbbf017022fc2523ee8c7` | 4 | 3 | `2026-10-21T12:07:33+00:00` | `2026-10-22T16:54:22+00:00` |
| `E50` | `exportaciones-8.jsonl` | `e3f5563435a1ca0541f2c843275251f6b30199c8cbbcc2723c22f7c5f836124d` | 5 | 2 | `2026-10-22T16:44:37+00:00` | `2026-10-22T16:44:39+00:00` |
| `E51` | `exportaciones-9.jsonl` | `8bc7b6fd7e10ec11a4dd6fe4b1c3033a4399070615e50b788f2c7301ff9891b1` | 13 | 6 | `2026-10-21T12:07:36+00:00` | `2026-10-29T10:48:10+00:00` |
| `E52` | `exportaciones-10.jsonl` | `616a1efd3c31e3a9572bebeaf8e10a7d77774b2bc4e5c6de41e85a0c8b9b30b9` | 26 | 7 | `2026-10-22T16:47:49+00:00` | `2026-10-22T16:47:57+00:00` |
| `E53` | `exportaciones-11.jsonl` | `49b82854d0d8c0fb1d7e60de005350f4f3252fc7aa9eb5f886f1bb6d9f811ae8` | 4 | 3 | `2026-10-22T16:46:53+00:00` | `2026-10-29T10:48:27+00:00` |
| `E54` | `exportaciones-13.jsonl` | `f1204091883527eaa0e875922180fb8832503a452f06abd485fcc958373a1b99` | 8 | 3 | `2026-10-22T16:46:52+00:00` | `2026-10-22T16:58:03+00:00` |
| `E55` | `exportaciones-14.jsonl` | `f59eda4bbe9bc9945b4d5690927a3cb874098cf021206873dafd1c1db9aac567` | 9 | 5 | `2026-10-22T15:19:57+00:00` | `2026-10-29T10:48:18+00:00` |
| `E56` | `exportaciones-16.jsonl` | `e3856689348b2d63ccb05c577349599cc712380a4b5acbc0b78e5aebbb04b0e8` | 7 | 4 | `2026-10-21T12:07:31+00:00` | `2026-10-22T16:51:09+00:00` |
| `E57` | `exportaciones-17.jsonl` | `c59662cbb337fdb1ce4fdd6f32d8616dcd08cae87ef2937d695a3577acb84b88` | 2 | 1 | `2026-10-22T16:57:00+00:00` | `2026-10-22T16:57:00+00:00` |
| `E58` | `exportaciones-18.jsonl` | `3bfa9eec912caae3003890b682d8d77df3ba390accfae8cb24d8cdd611024d9e` | 5 | 2 | `2026-10-22T16:44:35+00:00` | `2026-10-22T16:49:56+00:00` |
| `E59` | `exportaciones-19.jsonl` | `215cde7a4220001205d52c551bf98dc3b715133e83f9111704a61376886c7e96` | 7 | 2 | `2026-10-22T16:47:47+00:00` | `2026-10-22T16:56:01+00:00` |

### Filas afectadas y observación seleccionada

Una fila por convocatoria. `obs` es el ID de la observación `lineups` seleccionada por `section_selection`; `origen` identifica el archivo persistido y la línea de entrada. No se incluyen nombres de jugadores: el ID externo identifica sin copiar datos personales al informe.

| Partido | Jugador | Lado | Equipo del lado | Equipo opuesto | `teamId` crudo | Catálogo | Obs. seleccionada | `fetched_at` | Origen `E##:línea` |
|---:|---:|---:|---:|---:|---:|---|---:|---|---|
| `16599359` | `2606246` | away | `24322` | `217210` | `263785` | no registrado | `4799` | `2026-10-22T16:44:35+00:00` | `E58:3` |
| `16599359` | `1595678` | home | `217210` | `24322` | `853702` | no registrado | `4799` | `2026-10-22T16:44:35+00:00` | `E58:3` |
| `16599361` | `1939326` | home | `24326` | `263407` | `1119104` | no registrado | `4864` | `2026-10-22T16:47:47+00:00` | `E59:3` |
| `16599361` | `2073897` | home | `24326` | `263407` | `1119104` | no registrado | `4864` | `2026-10-22T16:47:47+00:00` | `E59:3` |
| `16599361` | `2271379` | home | `24326` | `263407` | `2836` | no registrado | `4864` | `2026-10-22T16:47:47+00:00` | `E59:3` |
| `16599361` | `2272348` | home | `24326` | `263407` | `1119104` | no registrado | `4864` | `2026-10-22T16:47:47+00:00` | `E59:3` |
| `16599362` | `1216283` | away | `24368` | `24323` | `37057` | no registrado | `4604` | `2026-10-22T15:19:57+00:00` | `E55:3` |
| `16599362` | `1383986` | home | `24323` | `24368` | `506673` | no registrado | `4604` | `2026-10-22T15:19:57+00:00` | `E55:3` |
| `16599362` | `2138059` | home | `24323` | `24368` | `549462` | no registrado | `4604` | `2026-10-22T15:19:57+00:00` | `E55:3` |
| `16599363` | `1594520` | home | `135672` | `2841` | `379297` | no registrado | `4669` | `2026-10-21T12:07:31+00:00` | `E56:3` |
| `16599363` | `1978038` | home | `135672` | `2841` | `483874` | no registrado | `4669` | `2026-10-21T12:07:31+00:00` | `E56:3` |
| `16599363` | `2009805` | home | `135672` | `2841` | `483874` | no registrado | `4669` | `2026-10-21T12:07:31+00:00` | `E56:3` |
| `16599365` | `1895962` | home | `24265` | `24363` | `24363` | registrado | `4539` | `2026-10-22T16:54:31+00:00` | `E54:3` |
| `16599365` | `2612978` | home | `24265` | `24363` | `368757` | no registrado | `4539` | `2026-10-22T16:54:31+00:00` | `E54:3` |
| `16599366` | `2290824` | away | `24338` | `202074` | `826809` | no registrado | `4734` | `2026-10-22T16:57:00+00:00` | `E57:3` |
| `16599366` | `2419210` | away | `24338` | `202074` | `2819` | no registrado | `4734` | `2026-10-22T16:57:00+00:00` | `E57:3` |
| `16599367` | `2082128` | away | `2815` | `2840` | `317628` | no registrado | `4019` | `2026-10-22T16:53:05+00:00` | `E46:3` |
| `16599367` | `2270780` | home | `2840` | `2815` | `202077` | no registrado | `4019` | `2026-10-22T16:53:05+00:00` | `E46:3` |
| `16599368` | `1975197` | away | `5069` | `43751` | `2829` | no registrado | `3313` | `2026-10-22T16:48:47+00:00` | `E40:3` |
| `16599368` | `2049131` | away | `5069` | `43751` | `1089499` | no registrado | `3313` | `2026-10-22T16:48:47+00:00` | `E40:3` |
| `16599368` | `2296584` | away | `5069` | `43751` | `490780` | no registrado | `3313` | `2026-10-22T16:48:47+00:00` | `E40:3` |
| `16599368` | `2325649` | away | `5069` | `43751` | `490780` | no registrado | `3313` | `2026-10-22T16:48:47+00:00` | `E40:3` |
| `16599370` | `2070285` | home | `24322` | `135672` | `275789` | no registrado | `4673` | `2026-10-22T16:44:36+00:00` | `E56:7` |
| `16599370` | `2606246` | home | `24322` | `135672` | `263785` | no registrado | `4673` | `2026-10-22T16:44:36+00:00` | `E56:7` |
| `16599371` | `2270780` | away | `2840` | `24329` | `202077` | no registrado | `4478` | `2026-10-22T16:52:01+00:00` | `E53:7` |
| `16599372` | `1939326` | away | `24326` | `2877` | `1119104` | no registrado | `4413` | `2026-10-22T16:47:49+00:00` | `E52:7` |
| `16599372` | `2272348` | away | `24326` | `2877` | `1119104` | no registrado | `4413` | `2026-10-22T16:47:49+00:00` | `E52:7` |
| `16599388` | `2612978` | away | `24265` | `2841` | `368757` | no registrado | `4218` | `2026-10-21T12:07:33+00:00` | `E49:7` |
| `16599554` | `1975197` | home | `5069` | `263407` | `2829` | no registrado | `4870` | `2026-10-22T16:56:01+00:00` | `E59:9` |
| `16599554` | `2296584` | home | `5069` | `263407` | `490780` | no registrado | `4870` | `2026-10-22T16:56:01+00:00` | `E59:9` |
| `16599554` | `2325649` | home | `5069` | `263407` | `490780` | no registrado | `4870` | `2026-10-22T16:56:01+00:00` | `E59:9` |
| `16599697` | `2419210` | home | `24338` | `4489` | `2819` | no registrado | `4158` | `2026-10-22T16:45:50+00:00` | `E48:12` |
| `16599713` | `1383986` | away | `24323` | `2815` | `506673` | no registrado | `4354` | `2026-10-22T16:46:48+00:00` | `E51:13` |
| `16599713` | `2138059` | away | `24323` | `2815` | `549462` | no registrado | `4354` | `2026-10-22T16:46:48+00:00` | `E51:13` |
| `16599763` | `2612978` | home | `24265` | `135672` | `368757` | no registrado | `4687` | `2026-10-22T16:51:09+00:00` | `E56:21` |
| `16599792` | `1939326` | away | `24326` | `5069` | `1119104` | no registrado | `4432` | `2026-10-22T16:47:55+00:00` | `E52:26` |
| `16599792` | `2272348` | away | `24326` | `5069` | `1119104` | no registrado | `4432` | `2026-10-22T16:47:55+00:00` | `E52:26` |
| `16599792` | `1937350` | home | `5069` | `24326` | `490780` | no registrado | `4432` | `2026-10-22T16:47:55+00:00` | `E52:26` |
| `16599792` | `1975197` | home | `5069` | `24326` | `2829` | no registrado | `4432` | `2026-10-22T16:47:55+00:00` | `E52:26` |
| `16599792` | `2049131` | home | `5069` | `24326` | `1089499` | no registrado | `4432` | `2026-10-22T16:47:55+00:00` | `E52:26` |
| `16599792` | `2237795` | home | `5069` | `24326` | `2829` | no registrado | `4432` | `2026-10-22T16:47:55+00:00` | `E52:26` |
| `16599792` | `2296584` | home | `5069` | `24326` | `490780` | no registrado | `4432` | `2026-10-22T16:47:55+00:00` | `E52:26` |
| `16599806` | `1383986` | away | `24323` | `24363` | `506673` | no registrado | `4560` | `2026-10-22T16:46:52+00:00` | `E54:24` |
| `16599806` | `2138059` | away | `24323` | `24363` | `549462` | no registrado | `4560` | `2026-10-22T16:46:52+00:00` | `E54:24` |
| `16599822` | `2419210` | home | `24338` | `24368` | `2819` | no registrado | `4628` | `2026-10-22T15:20:01+00:00` | `E55:27` |
| `16599834` | `1587195` | away | `5069` | `217210` | `490780` | no registrado | `4828` | `2026-10-22T16:49:56+00:00` | `E58:32` |
| `16599834` | `1975197` | away | `5069` | `217210` | `2829` | no registrado | `4828` | `2026-10-22T16:49:56+00:00` | `E58:32` |
| `16599834` | `2296584` | away | `5069` | `217210` | `490780` | no registrado | `4828` | `2026-10-22T16:49:56+00:00` | `E58:32` |
| `16599843` | `1100964` | home | `24326` | `4487` | `477045` | no registrado | `4439` | `2026-10-22T16:47:51+00:00` | `E52:33` |
| `16599843` | `1939326` | home | `24326` | `4487` | `1119104` | no registrado | `4439` | `2026-10-22T16:47:51+00:00` | `E52:33` |
| `16599843` | `2272348` | home | `24326` | `4487` | `1119104` | no registrado | `4439` | `2026-10-22T16:47:51+00:00` | `E52:33` |
| `16599856` | `2606246` | away | `24322` | `24323` | `263785` | no registrado | `4376` | `2026-10-22T16:44:38+00:00` | `E51:35` |
| `16599856` | `1383986` | home | `24323` | `24322` | `506673` | no registrado | `4376` | `2026-10-22T16:44:38+00:00` | `E51:35` |
| `16599856` | `2138059` | home | `24323` | `24322` | `549462` | no registrado | `4376` | `2026-10-22T16:44:38+00:00` | `E51:35` |
| `16599870` | `2009805` | home | `135672` | `2877` | `483874` | no registrado | `4701` | `2026-10-22T16:51:03+00:00` | `E56:35` |
| `16599875` | `2419210` | away | `24338` | `24265` | `2819` | no registrado | `4246` | `2026-10-22T16:54:22+00:00` | `E49:35` |
| `16599875` | `2612978` | home | `24265` | `24338` | `368757` | no registrado | `4246` | `2026-10-22T16:54:22+00:00` | `E49:35` |
| `16599881` | `2082128` | away | `2815` | `24326` | `317628` | no registrado | `4445` | `2026-10-22T16:47:56+00:00` | `E52:39` |
| `16599881` | `1939326` | home | `24326` | `2815` | `1119104` | no registrado | `4445` | `2026-10-22T16:47:56+00:00` | `E52:39` |
| `16599881` | `2073897` | home | `24326` | `2815` | `1119104` | no registrado | `4445` | `2026-10-22T16:47:56+00:00` | `E52:39` |
| `16599884` | `1383986` | home | `24323` | `24329` | `506673` | no registrado | `4510` | `2026-10-22T16:46:53+00:00` | `E53:39` |
| `16599884` | `2138059` | home | `24323` | `24329` | `549462` | no registrado | `4510` | `2026-10-22T16:46:53+00:00` | `E53:39` |
| `16599892` | `1937348` | away | `5069` | `24368` | `490780` | no registrado | `4640` | `2026-10-22T15:20:03+00:00` | `E55:39` |
| `16599892` | `1975197` | away | `5069` | `24368` | `2829` | no registrado | `4640` | `2026-10-22T15:20:03+00:00` | `E55:39` |
| `16599892` | `2335492` | away | `5069` | `24368` | `120854` | no registrado | `4640` | `2026-10-22T15:20:03+00:00` | `E55:39` |
| `16599906` | `2606246` | home | `24322` | `2840` | `263785` | no registrado | `4321` | `2026-10-22T16:44:37+00:00` | `E50:45` |
| `16599914` | `1100964` | away | `24326` | `24265` | `477045` | no registrado | `4450` | `2026-10-22T16:47:54+00:00` | `E52:44` |
| `16599914` | `1939326` | away | `24326` | `24265` | `1119104` | no registrado | `4450` | `2026-10-22T16:47:54+00:00` | `E52:44` |
| `16599914` | `2272348` | away | `24326` | `24265` | `1119104` | no registrado | `4450` | `2026-10-22T16:47:54+00:00` | `E52:44` |
| `16599914` | `2612978` | home | `24265` | `24326` | `368757` | no registrado | `4450` | `2026-10-22T16:47:54+00:00` | `E52:44` |
| `16599914` | `2685909` | home | `24265` | `24326` | `368757` | no registrado | `4450` | `2026-10-22T16:47:54+00:00` | `E52:44` |
| `16599921` | `1383986` | away | `24323` | `24338` | `506673` | no registrado | `4386` | `2026-10-22T16:46:51+00:00` | `E51:45` |
| `16599921` | `2138059` | away | `24323` | `24338` | `549462` | no registrado | `4386` | `2026-10-22T16:46:51+00:00` | `E51:45` |
| `16599921` | `2419210` | home | `24338` | `24323` | `2819` | no registrado | `4386` | `2026-10-22T16:46:51+00:00` | `E51:45` |
| `16599934` | `1939326` | away | `24326` | `2840` | `1119104` | no registrado | `4457` | `2026-10-22T16:47:57+00:00` | `E52:51` |
| `16599937` | `2138059` | away | `24323` | `5069` | `549462` | no registrado | `4390` | `2026-10-22T16:46:55+00:00` | `E51:49` |
| `16599937` | `1975197` | home | `5069` | `24323` | `2829` | no registrado | `4390` | `2026-10-22T16:46:55+00:00` | `E51:49` |
| `16599951` | `2268037` | away | `24338` | `24326` | `263776` | no registrado | `4461` | `2026-10-22T16:47:52+00:00` | `E52:55` |
| `16599951` | `2419210` | away | `24338` | `24326` | `2819` | no registrado | `4461` | `2026-10-22T16:47:52+00:00` | `E52:55` |
| `16599951` | `1939326` | home | `24326` | `24338` | `1119104` | no registrado | `4461` | `2026-10-22T16:47:52+00:00` | `E52:55` |
| `16599951` | `2128078` | home | `24326` | `24338` | `1119104` | no registrado | `4461` | `2026-10-22T16:47:52+00:00` | `E52:55` |
| `16599951` | `2272348` | home | `24326` | `24338` | `1119104` | no registrado | `4461` | `2026-10-22T16:47:52+00:00` | `E52:55` |
| `16599953` | `1383986` | home | `24323` | `2841` | `506673` | no registrado | `4396` | `2026-10-21T12:07:36+00:00` | `E51:55` |
| `16599953` | `2138059` | home | `24323` | `2841` | `549462` | no registrado | `4396` | `2026-10-21T12:07:36+00:00` | `E51:55` |
| `16599962` | `2082128` | away | `2815` | `24368` | `317628` | no registrado | `4656` | `2026-10-22T15:20:02+00:00` | `E55:55` |
| `16599967` | `1398517` | home | `24322` | `24368` | `44230` | registrado | `4661` | `2026-10-29T10:48:18+00:00` | `E55:60` |
| `16599969` | `1937350` | home | `5069` | `24322` | `490780` | no registrado | `4336` | `2026-10-22T16:44:39+00:00` | `E50:60` |
| `16599969` | `1975197` | home | `5069` | `24322` | `2829` | no registrado | `4336` | `2026-10-22T16:44:39+00:00` | `E50:60` |
| `16599969` | `2139204` | home | `5069` | `24322` | `154810` | no registrado | `4336` | `2026-10-22T16:44:39+00:00` | `E50:60` |
| `16599969` | `2237795` | home | `5069` | `24322` | `2829` | no registrado | `4336` | `2026-10-22T16:44:39+00:00` | `E50:60` |
| `16599972` | `1494377` | away | `24265` | `43751` | `368757` | no registrado | `4267` | `2026-10-22T16:48:52+00:00` | `E49:56` |
| `16599977` | `2138059` | away | `24323` | `24265` | `549462` | no registrado | `4401` | `2026-10-29T10:48:10+00:00` | `E51:60` |
| `16599988` | `2274260` | home | `24338` | `24329` | `2819` | no registrado | `4528` | `2026-10-29T10:48:27+00:00` | `E53:57` |
| `16969545` | `1402980` | away | `5069` | `24363` | `490780` | no registrado | `4600` | `2026-10-22T16:58:03+00:00` | `E54:64` |
| `16969545` | `1415367` | away | `5069` | `24363` | `490780` | no registrado | `4600` | `2026-10-22T16:58:03+00:00` | `E54:64` |
| `16969545` | `1975197` | away | `5069` | `24363` | `2829` | no registrado | `4600` | `2026-10-22T16:58:03+00:00` | `E54:64` |
| `16969545` | `2237795` | away | `5069` | `24363` | `2829` | no registrado | `4600` | `2026-10-22T16:58:03+00:00` | `E54:64` |

## Casos representativos

1. **ID crudo igual al equipo opuesto:** partido `16599365`, jugador `1895962`, lado `home`, equipo del lado `24265`, equipo opuesto y `teamId` crudo `24363`; observación seleccionada `4539`, captura `2026-10-22T16:54:31+00:00`, archivo `E54`, línea 3. `24363` está registrado como UE Sant Andreu, el participante visitante del partido. La entrada, sin embargo, está en `home.players`. Es una inconsistencia real entre posición y campo `teamId`; no demuestra cuál de esos campos es defectuoso.
2. **ID registrado que no es participante del partido:** partido `16599967`, jugador `1398517`, lado `home`, equipo del lado `24322`, equipo opuesto `24368`, `teamId` crudo `44230`; observación `4661`, captura `2026-10-29T10:48:18+00:00`, archivo `E55`, línea 60. `44230` está registrado en esta SQLite como CD Tudelano, pero no es ninguno de los dos participantes. La base no contiene evidencia suficiente para llamarlo ID histórico ni para asignarlo a ese jugador.
3. **ID sin entidad de equipo conocida y repetido:** partido `16599361`, jugador `1939326`, lado `home`, equipo del lado `24326`, equipo opuesto `263407`, `teamId` crudo `1119104`; observación `4864`, captura `2026-10-22T16:47:47+00:00`, archivo `E59`, línea 3. El ID crudo no tiene correspondencia en la tabla de equipos. El mismo valor aparece en 17 discrepancias, para 4 jugadores y 8 partidos, pero la SQLite no permite identificar a qué equipo o concepto corresponde.
4. **Discrepancia sin cambio de versión:** partido `16599363`, jugador `1594520`, lado `home`, equipo del lado `135672`, `teamId` crudo `379297`; observación seleccionada `4669`, archivo `E56`, línea 3. Las observaciones anteriores de esta entrada están en `E1`, `E2`, `E10`, `E27`, `E41` y `E47`; todas tienen el mismo `content_hash` y `fetched_at` que el payload seleccionado. Son copias del mismo contenido en la evidencia de importación, no una secuencia de valores distintos.

## Versiones anteriores y concentración temporal

Para cada una de las 97 filas se recorrieron las observaciones `lineups` anteriores en orden de ingestión del mismo partido y subclave, y se buscó el mismo jugador bajo el mismo lado del payload. Las 97 entradas aparecen en observaciones anteriores y todas repiten exactamente el `teamId` crudo actual. En los 97 casos, los hashes de contenido anteriores son iguales al hash seleccionado. No hay un cambio de `teamId` documentado entre las observaciones guardadas.

Esto descarta como explicación observada un valor distinto en una versión anterior que luego hubiese cambiado en la versión seleccionada. **No demuestra estabilidad semántica del dato**: las filas anteriores reutilizan el mismo contenido y comparten el mismo instante `fetched_at`; no son capturas independientes que permitan determinar qué equipo describía el proveedor en otro momento.

Las 97 filas tienen `fetched_at` entre `2026-10-21T12:07:31+00:00` y `2026-10-29T10:48:27+00:00`, todas en octubre de 2026, y proceden de 14 archivos de exportación. La tabla de archivos cuantifica la distribución. Dieciocho de los 38 jugadores afectados aparecen en más de un partido afectado; cada uno conserva el mismo ID crudo de `teamId` en sus discrepancias. Este patrón es compatible con que `teamId` represente una afiliación del jugador independiente del equipo del partido, pero esa interpretación sigue siendo una **hipótesis**, no un hecho probado por los datos disponibles.

## Reglas de extracción y normalización

El flujo inspeccionado es coherente con el modelo descrito:

1. `importar.extraer_convocatorias()` itera por `home.players` y `away.players`. Calcula `team_id` con `partido['home_team_id']` o `partido['away_team_id']`, conserva `entrada.get('teamId')` como `source_team_id` y añade un aviso cuando difieren.
2. `persistencia/carga.py` persiste `fila['team_id']` como FK de `call_up.team_id` y copia por separado `entrada.get('teamId')` en `call_up.external_team_id`.
3. `persistencia/reconstruccion.py` vuelve a publicar `team_id` desde el lado y `source_team_id` desde el campo crudo guardado.
4. `api.actuacion()` compara ambos valores y añade `source_team_mismatch`; el test `test_el_equipo_es_el_del_lado_y_el_team_id_discrepante_es_advertencia` cubre expresamente esta regla. El caso probado asegura que el equipo de la actuación sea el del lado y que se expongan ambas advertencias cuando también cambia el valor normalizado de entrada.

La comprobación directa del JSON seleccionada confirmó que las 97 parejas jugador/`teamId` coinciden exactamente con la entrada cruda en el lado indicado. El lado, el equipo del partido y el ID de jugador se traducen sin cambios en las 97. No se encontró un error de extracción ni una FK reasignada. La incertidumbre está en el significado del campo fuente y en la diferencia entre ese valor y el contenedor `home`/`away`, no en la regla interna que asigna el equipo de la convocatoria.

## Impacto

- **Estadísticas:** la agregación usa el `team_id` resuelto desde el lado del partido. `source_team_mismatch` se conserva como aviso de calidad (`team_warnings`); por sí solo no reasigna una convocatoria ni invalida sus estadísticas. No se observó un caso de FK de equipo del lado incorrecto.
- **Convocatorias y alineaciones:** las 97 filas permanecen asociadas al partido, jugador, equipo y lado derivados de la alineación. El valor discrepante sigue disponible como procedencia para revisión.
- **Navegación:** los IDs crudos de `teamId` no son la clave usada para generar IDs públicos de jugadores, partidos o equipos. La discrepancia no cambia las rutas ni los enlaces de detalle actuales.
- **Identidad:** `external_team_id` aquí es evidencia escalar de la alineación. No crea una fila `team_external_id`, no modifica UID ni establece una correspondencia `origin`/`verified`. Los 22 IDs sin catálogo no deben convertirse automáticamente en equipos.

## Clasificación de conclusiones

| Conclusión | Estado | Evidencia |
|---|---|---|
| La FK de equipo de las convocatorias coincide con el lado del partido | **Correcto y verificado** | 97/97 casos cotejados; las 3.853 convocatorias de la copia pasan la misma comprobación. |
| La extracción conserva fielmente `teamId` y el lado | **Correcto y verificado** | 97/97 entradas del payload seleccionado coinciden con los valores persistidos; el código y el test explícito usan esta regla. |
| El `teamId` crudo se mantuvo igual en la evidencia anterior disponible | **Correcto y verificado** | 97/97 con observaciones anteriores de igual hash de contenido y mismo valor. |
| Dos IDs crudos tienen entidad registrada; 22 no | **Correcto y verificado** | Búsqueda exacta en `team_external_id` de la copia `0004`. |
| `teamId` significa afiliación actual o histórica del jugador | **Hipótesis** | Patrón compatible, sin definición de campo ni una entidad de equipo que cubra la mayoría de los IDs. |
| Las discrepancias son una particularidad conocida de SofaScore | **No verificable con la evidencia disponible** | No hay documentación de semántica del campo en SQLite y no se consultaron fuentes externas. |
| Los IDs desconocidos son IDs históricos válidos | **No verificable con la evidencia disponible** | Ninguna correspondencia, alias o catálogo histórico guardado los acredita. |
| Es un bug en el extractor de Real Jaén | **No respaldado por la evidencia** | El código selecciona el equipo por lado, preserva el `teamId` original y genera el aviso; las 97 filas reproducen esa regla. |

## Recomendaciones

1. Mantener la advertencia y los dos valores mientras no exista evidencia para resolver la semántica de origen.
2. No reasignar `call_up.team_id`, no crear filas en `team_external_id` y no fusionar entidades basándose solo en `teamId`.
3. En la migración a FastAPI, preservar por separado lado, equipo de partido, `teamId` original y el diagnóstico `source_team_mismatch`; no convertir esta advertencia en un fallo que oculte la convocatoria.
4. Si se decide resolverlo más adelante, contrastar los valores con documentación de campo o evidencia original ya archivada y registrar cualquier asociación como decisión trazable, sin sobrescribir `content`.
5. Añadir una prueba de regresión basada en casos representativos almacenados: `24363` como equipo opuesto, `44230` como equipo registrado ajeno al partido y un ID no catalogado como `1119104`.

**Bloqueo para FastAPI:** no se identifica un bloqueo estructural o de integridad. El contrato nuevo debe preservar la distinción y la advertencia; la semántica del campo crudo puede quedar pendiente de confirmación.
