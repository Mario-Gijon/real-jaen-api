# Revisión crítica de simplicidad del almacenamiento (tras la fase 3)

**Fecha: 25-10-2026. Código revisado: commit `c56dc83`.** Revisión sin cambios de código de producción, esquema,
migraciones ni base activa. Las mediciones se hicieron sobre **copias temporales** de `real-jaen.sqlite` (su sha256 se
comprobó antes y después) con un prototipo fuera del repositorio.

Criterios:
- no simplificar eliminando evidencia ni debilitando comprobaciones de coherencia;
- no justificar una estructura solo por una posibilidad futura;
- cada recomendación indica beneficio y riesgo.

> **Revisión 2 (25-10-2026).** La primera versión de este documento recomendaba dejar de materializar las 12 tablas
> derivadas y recalcularlas al arrancar. Esa recomendación **se retira**: se conserva el modelo relacional deportivo.
> La sección 2 corrige dos afirmaciones erróneas de aquella versión.

---

## 1. Conclusión revisada

- **Mantener el modelo relacional** (partidos, convocatorias, estadísticas, discrepancias, selección).
- **Producirlo con una sola ejecución de `importar.ensamblar`**, y escribir su resultado con el **mismo código de
  escritura** que la carga inicial. No se reimplementa ninguna regla deportiva en la persistencia.
- **Actualizar con una sincronización completa en una transacción.** Se recalcula todo el estado derivado y se escribe
  solo la diferencia, con claves estables, sin borrar y recrear filas referenciables.
  - Medido: **unos 3,3 s** con la base real, con o sin cambios.
  - Resultado **idéntico** (3222 rutas) a un camino independiente: evidencia → `ensamblar` → API.
- **Retirar únicamente `team_match_stat` e `incident`**: ninguna consulta las usa, no salen de las reglas del
  importador ni de la API (las rellena un analizador propio de la persistencia) y retirarlas reduce complejidad.
  - El resto de tablas derivadas aporta capacidades concretas de consulta o procedencia y se mantiene.
- **No se toca el esquema ahora.** La retirada sería dejar de escribirlas y, más adelante, una migración nueva.

---

## 2. Correcciones a la versión anterior

1. **«Consultar por SQL en cada petición obligaría a reimplementar en SQL la normalización y los agregados»: incorrecto.**
   - SQL puede **seleccionar** las filas necesarias (los partidos de un equipo, las convocatorias de un partido, las
     versiones de una sección) y aplicarles **las mismas funciones de Python**.
   - Solo habría duplicación si la lógica de las reglas se trasladara a SQL (agregados o normalización en consultas).
   - El coste real es otro: las funciones actuales esperan la estructura `datos` completa (recorren listas). Cargar
     por petición exigiría una capa de acceso que entregue subconjuntos con esa forma. Es trabajo de acceso a datos,
     no duplicación de reglas.
   - Hoy no hace falta: la carga completa tarda 1,5 s.
2. **«Comparar la selección guardada con la recalculada garantiza la equivalencia»: incorrecto.**
   - Que coincida la selección solo prueba que se eligen las mismas versiones.
   - El estado y las respuestas dependen además de la extracción y del ensamblado (`ensamblar`,
     `extraer_convocatorias`, `ficha_combinada`), de las reglas al consultar (`actuacion`, agregados), del ámbito, de
     las sustituciones y de las decisiones de identidad.
   - Lo que sí comprueba el estado materializado completo es **comparar todo el estado derivado recalculado con el
     guardado**. Es exactamente la diferencia que calcula la sincronización: una **sincronización en seco sin cambios
     (0 filas)** demuestra que lo guardado coincide con las reglas actuales.
   - La equivalencia de las respuestas, además, requiere compararlas (arnés de rutas), porque las reglas que se
     aplican al consultar no se guardan.

---

## 3. Alternativa medida: una ejecución de `ensamblar` y sincronización completa

### 3.1 Cómo funciona (prototipo)

1. **Estado deseado**: evidencia de la base → `importar.ensamblar` → `persistencia.carga.escribir`, con sus filas
   **capturadas** en lugar de insertadas. Es el mismo código de la carga inicial; ninguna regla se escribe dos veces.
2. **Traducción a los ids de la base**, por claves estables:

| Tabla | Clave estable | Qué se hace si cambia |
|---|---|---|
| entidades (`match`, `team`, `player`, `competition`, `season`, `stage`) | id externo | nunca se borran; las nuevas reciben el siguiente id y su uid UUIDv5 |
| `observation` | (fichero, línea); el id es el orden de ingestión | solo se añaden. Las de partidos nuevos se asocian a su entidad antes de seleccionar |
| `section_selection` | (partido, clave, sub) | actualización en su sitio; la anterior pasa a `section_selection_history` con su ejecución (`valid_from_run`) y su regla |
| `match_state`, `team_name` | partido / equipo | actualización en su sitio |
| `call_up` | (partido, lado, jugador) | actualización en su sitio, **el id se conserva aunque cambie la versión de la alineación** |
| `call_up_stat`, `match_version_issue` | clave natural por padre, sin referencias entrantes | se sustituye el conjunto del padre solo si cambia |
| `player_attribute_observation` | (observación, entrada, campo); inmutable | solo se añaden las de observaciones nuevas |
| `discrepancy` + hijas | (tipo, sujeto, clave, sub) | si cambia, la anterior se marca `superseded_in_run` y se crea otra; nada se borra |
| `rule_set` | (nombre, versión) | una regla nueva detiene la sincronización: exige una reconstrucción explícita |

3. Todo en **una transacción** (`BEGIN IMMEDIATE`), con las claves foráneas diferidas comprobadas al confirmar.

### 3.2 Mediciones (copias temporales de la base real)

| Caso | Reglas (`ensamblar`) | Estado deseado (lectura + reglas + filas) | Diferencia y escritura | Total | Resultado |
|---|---|---|---|---|---|
| Sin evidencia nueva | 0,2 s | 2,2 s | 1,1 s | ~3,3 s | 0 cambios (solo comprobación) |
| Evidencia nueva: otra versión de la alineación de 16599928 (Keita: 1 bloqueado, 1 fuera), un pendiente que pasa a finalizado 2-1 y un partido nuevo | 0,4 s | 2,3 s | 1,0 s | 3,3 s | ver abajo |
| Referencia: borrar y reinsertar todo el estado derivado | — | — | 2,5 s | — | tiempo similar, pero **cambia ids y pierde la continuidad**: descartado |

Cambios con evidencia nueva:
- 1 partido nuevo;
- 2 selecciones actualizadas, con sus 2 anteriores al historial;
- 1 estado de partido actualizado y 1 nuevo;
- 39 convocatorias actualizadas **con sus mismos ids**;
- 1 conjunto de estadísticas sustituido (Keita: `blockedScoringAttempt` 1, `shotOffTarget` 1);
- 234 atributos nuevos;
- 2 discrepancias nuevas.

Comprobaciones tras confirmar: integridad `ok`, sin violaciones de clave foránea, ids de entidades existentes
conservados, historial con ejecución y regla de origen.

**Equivalencia**: las **3222 rutas** servidas desde la base sincronizada (modo SQL actual, que lee las tablas
materializadas) son **idénticas** a las de un camino independiente (evidencia → `ensamblar` → `api.componer`).

### 3.3 Complejidad

| Elemento | Tamaño | Comentario |
|---|---|---|
| Construcción de filas | 0 líneas nuevas | se reutiliza `carga.escribir`. En producción conviene separar «calcular filas» de «insertarlas» (refactor pequeño) en lugar de capturarlas |
| Sincronización genérica | ~270 líneas en el prototipo | la mayor parte es un patrón repetido: actualizar por clave estable, sustituir conjunto por padre, marcar superado. Se puede expresar como una tabla de claves y políticas por tabla |
| Actualización selectiva por partido (alternativa) | mayor | habría que calcular qué partidos, jugadores y equipos se ven afectados (el nombre de equipo depende de una regla global, los atributos de todas las versiones…). No compensa: la sincronización completa tarda ~3 s |

### 3.4 Huecos detectados que la fase 4 debe resolver

1. **`reconstruccion.exportaciones`** solo considera los ficheros de la carga inicial. Con importaciones posteriores
   debe incluir toda la evidencia (el prototipo lo hace con `manifiesto_completo`).
2. **Convocatoria que desaparece** de la alineación seleccionada (un jugador que ya no figura en una versión
   posterior). No se puede borrar sin romper referencias y `call_up` no tiene estado de vigencia. El prototipo se
   detiene en ese caso. Hace falta una decisión explícita: detenerse siempre, o añadir vigencia con una migración nueva.
3. **Clave de convocatoria (partido, lado, jugador)**: solo es única porque la importación rechaza jugadores
   repetidos. La sincronización debe comprobarlo y detenerse si se repite, nunca fusionar.
4. **Regla nueva o código distinto**: una regla que no está en `rule_set` detiene la sincronización. Además, una
   sincronización en seco con cambios sin evidencia nueva indica que el código de reglas cambió y debe tratarse como
   reconstrucción explícita.

---

## 4. Tablas derivadas: utilidad concreta

Columnas: **Origen**, quién produce el contenido; **Uso actual**, quién lo lee hoy; **Capacidad**, qué consultas
permite en partidos (P), jugadores (J), estadísticas (E) y procedencia (Pr).

| Tabla | Origen | Uso actual | Capacidad | ¿Reduce complejidad retirarla? | Recomendación |
|---|---|---|---|---|---|
| `section_selection` (+ historial) | `importar.seleccionar` | API (selección), sincronización | **Pr**: qué versión se sirve y desde cuándo; base de la comprobación de coherencia | no (es la decisión de versión) | **Mantener** |
| `discrepancy` + 3 hijas | `ensamblar` | API (`meta.discrepancy`) | **Pr**: qué partidos y jugadores tienen versiones contradictorias, desde qué ejecución, con todas sus versiones | poco | **Mantener** |
| `match_state`, `match_version_issue` | `ensamblar` | API (todas las rutas de partido) | **P**: calendario, resultados y filtros por equipo, fecha o competición en SQL | no | **Mantener** |
| `team_name` | `ensamblar` (regla del primer nombre) | API | **P/Pr**: nombre publicado y observación de la que sale | no | **Mantener** |
| `call_up` | `extraer_convocatorias` (dentro de `ensamblar`) | API (convocatorias, acumulados, alineaciones) | **J/P**: participación de un jugador por partido, dorsal, titularidad, equipo del lado | no | **Mantener** |
| `call_up_stat` | `statistics` original de cada convocatoria | API (reconstrucción) | **E**: cualquier estadística individual consultable por jugador, partido o clave, con su literal original | no | **Mantener** |
| `player_attribute_observation` | `importar.ficha_de_entrada` por observación | API (ficha combinada) | **J/Pr**: qué observación aportó cada atributo personal | poco (~30 líneas) | **Mantener** |
| `team_match_stat` | **analizador propio de la persistencia** (`escribir_secciones`), distinto de `api.estadisticas_all` | **ninguno** | E de equipo, pero sin las reglas de dominio y conflictos que aplica la API | **sí**: ~50 líneas de extracción, regla `persistencia.carga`, clave compuesta, su parte de la sincronización y el riesgo de dos lecturas distintas del mismo payload | **Retirar** (dejar de escribirla; eliminarla después con una migración nueva) |
| `incident` | **analizador propio de la persistencia**, distinto de `api.contexto_partido` | **ninguno** | P/J (goles, asistencias, cambios), con la misma salvedad | **sí** (ídem) | **Retirar** (ídem) |

Si en el futuro hacen falta consultas SQL de estadísticas de equipo o de incidencias, se reintroducen **con las
funciones de la API** que ya las leen (`estadisticas_all`, `contexto_partido`), no con un segundo analizador.

---

## 5. Resto de capas, módulos y temas

| Elemento | Recomendación | Motivo |
|---|---|---|
| Evidencia (`content`, `observation`, `source_file`, `import_run_file`, `import_run`) | **Mantener** | imprescindible y leída; deduplicada |
| Identidad (entidades e ids externos) | **Mantener** | ids internos estables y traducción a los ids públicos |
| `competition`, `season`, `stage` | **Mantener** | identidad estable acordada; coste bajo; sin lógica añadida hasta que haya consumidor |
| `identity_decision`, `identity_decision_subject` | **Aplazar** su uso; mantenerlas vacías | preparación pedida, sin herramienta; coste en ejecución nulo |
| `local_replacement`, `file_team_declaration` | **Mantener** | decisiones auditadas y leídas |
| `rule_set` | **Mantener** | procedencia de las reglas; detiene una sincronización con reglas nuevas |
| Reconstrucción completa en memoria | **Mantener** | 1,5 s; las consultas SQL por subconjuntos serían posibles sin duplicar reglas (sección 2.1), pero hoy no hacen falta |
| Validaciones repetidas (doble comprobación en la carga, CHECK y Python, sustituciones al guardar y al arrancar) | **Mantener** | cada una protege un momento distinto; la sincronización en seco se añade como comprobación de coherencia completa |
| Convivencia JSON / SQL | **Mantener** hasta que la fase 4 funcione en servicio | vuelta atrás y oráculo de equivalencia |

---

## 6. Recomendación revisada

| Decisión | Qué | Beneficio | Riesgo |
|---|---|---|---|
| **Mantener** | modelo relacional deportivo, evidencia, identidad, decisiones, selección e historial, discrepancias, convocatorias y sus estadísticas, atributos | consultas por partido, jugador, estadística y procedencia; ids estables | coste de almacenamiento (~mitad de la base derivada), asumido |
| **Fase 4 con sincronización completa** | una ejecución de `ensamblar`; filas con el código de la carga; diferencia con claves estables; una transacción | sin reglas duplicadas; ids y trazabilidad conservados; ~3 s; la sincronización en seco sirve de comprobación de coherencia | ~270 líneas de sincronización (reducibles con políticas por tabla) y los cuatro huecos de 3.4 |
| **Simplificar en la fase 4** | dejar de escribir `team_match_stat` e `incident` y su regla `persistencia.carga`; separar «calcular filas» de «insertar» en `carga.py` | menos código y un solo analizador por payload | perder una capacidad SQL hoy sin uso; se reintroduce con las funciones de la API si hace falta |
| **Aplazar** | migración nueva que elimine esas dos tablas; herramientas de identidad; vigencia de convocatorias (según 3.4.2); consultas SQL por subconjunto; retirada del modo JSON | — | cada una con su criterio |

**No se propone** eliminar evidencia, relajar comprobaciones ni modificar las migraciones existentes.
