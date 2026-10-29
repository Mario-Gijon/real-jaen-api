# Normalización de estadísticas individuales por partido

**Estado: fase 1 implementada (24-10-2026)** en `api.py` (`estadisticas_de_actuacion`,
`conflictos_de_estadisticas`) y consumida por `rj-analyst`; pruebas en `tests/test_estadisticas.py`.
Lo que figura como «posterior» o «futuro» sigue sin implementar.

Las relaciones entre categorías de SofaScore que se usan aquí son una **interpretación documentada,
respaldada por la muestra auditada**, no una garantía oficial de SofaScore.

Base empírica: auditoría de solo lectura de la importación activa
`datos-importados/fichas-grupo2-2026-10-23/importacion-001` (80 partidos con alineación, 3427
convocatorias: 2519 jugadas, 884 no utilizadas, 24 sin resolver). Las cifras citadas son de esa
importación y no se extrapolan a otras temporadas, competiciones ni proveedores.

Objetivo: **evitar falsas interpretaciones**, no sustituir guiones por ceros. Un valor solo deja de
ser desconocido cuando lo da la fuente o cuando una relación verificada lo fija sin suponer nada
sobre las claves ausentes.

---

## 1. Criterios obligatorios

1. Los originales (`jugador_partido.statistics`, `originales.json`) no se modifican nunca; la
   normalización va al lado, con su procedencia y su evidencia.
2. Una clave ausente nunca se usa como cero para justificar otra deducción.
3. Que una sección (alineación, `statistics`) esté guardada no prueba que todas sus métricas
   tengan cobertura.
4. **No se aplican** por ahora deducciones por suma de jugadores frente al total del equipo: su
   equivalencia semántica y su completitud no están verificadas (sección 3, H4).
5. Una suma explícita inferior a un total no es una contradicción si faltan valores.
6. Una participación sin resolver (`unknown`) no permite derivar ninguna estadística.
7. Un suplente no utilizado no cuenta como partido jugado.
8. Una incoherencia afecta solo a las métricas de su grupo relacionado (sección 4.4).
9. Una media con cobertura parcial se identifica como tal; nunca se presenta como completa.
10. Las reglas viven en la API. El dashboard consume `value`, `source` y `reason`; no las repite.
11. Se distingue lo respaldado por evidencia (E) de lo que sigue siendo hipótesis (H).

---

## 2. Clases de resultado

| Clase | `source` | `value` | Significado |
|---|---|---|---|
| Explícito | `reported` | número | La clave existe en el registro con un valor válido (incluido 0). |
| Derivado | `derived` (+ `rule`) | número | Fijado por una relación de la sección 3 a partir de valores **explícitos** del mismo registro. |
| Calculado | `calculated` (+ `rule: ratio`) | número | Cociente de dos valores con dato (precisión de pase). No resuelve ninguna ausencia. |
| No utilizado | `unused` | 0 | Suplente no utilizado según la normalización de participación existente. |
| Desconocido | `null` (+ `reason`) | `null` | Cualquier otro caso. |

`reason` (solo con `value: null`):

| reason | Cuándo |
|---|---|
| `not_reported` | La clave falta y ninguna regla aplicable la fija. Compatible con 0, sin prueba. |
| `reported_null` | La clave existe con `null`. No se deduce nada para esa métrica. |
| `invalid` | Valor fuera de dominio (booleano, texto, no finito, negativo, no entero en un recuento). |
| `duplicate_disagree` | Registros duplicados del mismo jugador y partido dan valores distintos. |
| `version_conflict` | Versiones posteriores al partido de la alineación discrepan en el grupo de la métrica. |
| `inconsistent` | Los valores explícitos del grupo violan una relación verificada. |
| `participation_unknown` | Participación sin resolver. |
| `not_applicable` | El cociente no tiene sentido (denominador 0). |

---

## 3. Relaciones: evidencia e hipótesis

| Id | Relación | Estado | Evidencia en la importación activa | Uso |
|---|---|---|---|---|
| H1 | `onTargetScoringAttempt`, `shotOffTarget` y `blockedScoringAttempt` son subconjuntos **disjuntos** de `totalShots`. | E (no demostrado como convención universal de SofaScore) | 78/78 registros con las cuatro claves explícitas cumplen total = a puerta + fuera + bloqueados; 3/3 muestras de temporada de equipo del scraper (`shots = shotsOnTarget + shotsOffTarget + blockedScoringAttempt`). | Fase 1 |
| H1b | `hitWoodwork` **no** es una categoría complementaria: se solapa con las anteriores. | E | Registros con total 1, fuera 1, palo 1; en las muestras de equipo va aparte de la suma. | Excluido de toda regla |
| H2 | Ganados ≤ intentados: `wonContest ≤ totalContest`, `accuratePass ≤ totalPass`, `wonTackle ≤ totalTackle`. | E | 0 violaciones en 784, 2468 y 1086 registros con ambas claves explícitas. Nunca aparece «ganados» sin «intentados». | Fase 1 (regates, pases) |
| H3 | `goals ≤ onTargetScoringAttempt ≤ totalShots` (goles del jugador, sin autogoles). | E | 190/190 registros con goles > 0 tienen ambas claves explícitas y cumplen la relación. | Fase 1, solo como control de coherencia |
| H4 | El total de equipo (`statistics`, periodo ALL) es la suma de los jugadores para la misma métrica. | **H, no verificada** | Coincide con la suma explícita en la mayoría de lados, pero: si SofaScore calcula el total desde sus filas de jugador, la igualdad es tautológica y no revela datos no recogidos. Hay discrepancias (xG en 16 lados; bloqueados en 16599684; partidos 16599904 y 17118209). | **No se usa** |
| H5 | SofaScore omite las claves con valor 0. | H (compatible, no probada) | En 78 de 80 partidos las métricas auditadas no traen ningún 0 explícito; en 2 partidos («rellenos») sí, y esos ceros contradicen sus totales de equipo. `interceptionWon`, `shotOffTarget`, `blockedScoringAttempt` y `saves` no traen nunca 0, ni siquiera en esos 2. | Solo en la regla actual de goles (sección 6) |
| H6 | xG = 0 cuando `totalShots` es 0 explícito. | H (definición plausible) | Las 1279 ausencias de xG coinciden con `totalShots: 0`; las 31 presencias con 0 tiros valen 0. | Aplazada por decisión |
| H7 | `totalShots: 0` de un suplente sin minutos es relleno de banquillo, no prueba de participación ni de cero. | E (regla ya vigente) | 860 no utilizados traen solo `totalShots: 0` y `statisticsType`. | Ya aplicada en la participación |

Las relaciones H1–H3 se comprueban **en cada registro**; su cumplimiento global no autoriza a
saltarse la comprobación.

---

## 4. Reglas comunes

### 4.1 Orden de evaluación (por convocatoria y métrica)

1. Participación (normalización existente): `unknown` → todas las métricas `participation_unknown`;
   `unused` → recuentos 0 con `source: unused` (ni xG ni valoración); `played` → sigue.
2. Conflicto de identidad del partido (`version_conflict` del partido): todas las métricas
   `version_conflict` (no se sabe de qué partido son).
3. Duplicados: se unen con `comun()`; una clave que discrepa → `duplicate_disagree` para su grupo.
4. Versiones de alineación (4.3).
5. Dominio de cada clave → `invalid` o `reported_null` para esa clave.
6. Coherencia del grupo con los valores explícitos (4.4) → `inconsistent` para el grupo.
7. Valor explícito → `reported`.
8. Derivación (sección 5), solo si el grupo no está afectado por 3–6 → `derived`.
9. En otro caso → `not_reported`.

### 4.2 Dominio

- Recuentos: entero ≥ 0 (no booleano).
- `expectedGoals`: número finito ≥ 0. `rating`: número finito entre 0 y 10.
- Una clave inválida o `null` no se usa en ninguna derivación ni comprobación, **ni se trata como
  ausente**: impide las derivaciones que dependen de ella (`not_reported` si no hay otra causa).
  Un complemento inválido de tiros a puerta deja además la incidencia `stat_invalid`.

### 4.2b Goles como control

- La ausencia de `goals` **no** prueba 0 goles; solo no bloquea lo que es compatible con ella.
- Gol fiable: `statistics.goals` > 0 válido y coherente con las incidencias contrastables, o
  incidencias contrastables que atribuyen goles al jugador aunque `goals` falte.
- Un gol fiable incompatible con los tiros (más goles que tiros a puerta o que tiros, o goles cuando
  las categorías complementarias agotan el total) deja el grupo tiros en `inconsistent`.
- Una cifra de goles dudosa (duplicados o versiones que discrepan, inválida, `null`, o que contradice
  a las incidencias) no es un gol fiable ni prueba de 0: impide la deducción, sin incoherencia.
- Las reglas de goles y asistencias no cambian (sección 6).

### 4.3 Versiones

- Actual: `jugador_partido` sale de la versión seleccionada (la más reciente comparable; la
  confirmada sobre la preliminar). `discrepancias.json` conserva las demás. La API marca
  `lineup_versions_disagree` solo si difieren suplente, minutos o la existencia de estadísticas
  individuales; **no compara los valores**.
- Conflicto (`conflictos_de_estadisticas`): con al menos dos versiones distintas posteriores al
  partido (con minutos), el grupo del jugador queda `version_conflict`, con las versiones en la
  evidencia, si alguna clave trae valores explícitos distintos, si un `null` o un valor inválido se
  enfrenta a cualquier otra lectura (también a una ausencia: no son una omisión), o si los valores que
  dan entre todas las versiones incumplen las relaciones del grupo (4.4; p. ej. una versión con 1 a
  puerta y otra cuyas categorías complementarias ya agotan los tiros). Los demás grupos conservan su
  valor. Un desacuerdo de participación sigue llevando a `unknown` (todas las métricas
  `participation_unknown`).
- No es conflicto que una versión omita una clave que otra da con el mismo valor válido (p. ej. la
  alineación del 27-10 de 16599363, que añade ceros explícitos que la del 21-10 omitía). Se normaliza
  la versión seleccionada, sin tomar valores de otra: si la seleccionada omite la clave, `not_reported`.
  Decisión del 27-10-2026, sustituye a «difiere en valor o en presencia».
- En la importación activa no hay ningún caso (`discrepancias.json` vacío; todas las versiones de
  alineación de cada partido tienen la misma huella).

### 4.4 Grupos relacionados

| Grupo | Claves | Controles (solo con valores explícitos) |
|---|---|---|
| Tiros | `totalShots`, `onTargetScoringAttempt`, `shotOffTarget`, `blockedScoringAttempt` (+ `goals` solo como control) | Suma de categorías explícitas > `totalShots`; `goals` > `onTargetScoringAttempt` explícito; `goals` > `totalShots`. |
| Regates | `totalContest`, `wonContest` | `wonContest` > `totalContest`. |
| Pases | `totalPass`, `accuratePass` (+ precisión) | `accuratePass` > `totalPass`. |
| Entradas (no mostrada) | `totalTackle`, `wonTackle` | `wonTackle` > `totalTackle`. |
| Independientes | `expectedGoals`, `ballRecovery`, `possessionLostCtrl`, `duelWon`, `duelLost`, `fouls`, `wasFouled`, `interceptionWon`, `saves` | Solo dominio, duplicados y versiones. Ninguna descomposición de duelos se cumple (la mejor, 38/80 registros). |

Una incoherencia en un grupo deja todo el grupo en `inconsistent` (no se sabe qué valor está mal);
los originales y la evidencia se conservan y el resto de grupos no se toca. Una suma explícita
**inferior** al total no es incoherencia.

Decisión (24-10-2026): un valor explícito contradicho queda normalizado como desconocido (`null`,
`inconsistent`), con el original intacto en `statistics` y la incidencia `stat_inconsistent` con su
evidencia, como hace `goals_mismatch`. Un conflicto entre versiones o duplicados no se resuelve
eligiendo un valor: su grupo queda `version_conflict` / `duplicate_disagree` y los demás no cambian.

---

## 5. Matriz por métrica

Mostrada = la usa hoy la ficha de jugador del dashboard (detalle por partido). Fase: cuándo se
aplica la derivación; «—» = sin derivación.

| Métrica (pantalla) | Clave original | Significado conocido | Derivación | Condiciones exactas | Bloquean | Fase |
|---|---|---|---|---|---|---|
| Tiros | `totalShots` | Remates del jugador (E: siempre presente en jugados) | — | — | Grupo tiros incoherente | 1 (solo explícito) |
| **Tiros a puerta** | `onTargetScoringAttempt` | Remates entre palos, incluidos goles (E por H1, H3) | `shots_partition` → 0 | Clave ausente; `totalShots` explícito y válido; las categorías complementarias explícitas (`shotOffTarget`, `blockedScoringAttempt`: una, ambas o ninguna si `totalShots == 0`) suman exactamente `totalShots`; cada complemento está ausente o es válido; sin gol fiable ni cifra de goles dudosa (4.2b); sin incoherencia, duplicado discrepante ni conflicto de versión en el grupo. `hitWoodwork` no se cuenta. Keita (`totalShots: 2`, `blockedScoringAttempt: 2`) queda cubierto. | Complemento inválido, `null` o en conflicto; gol fiable (→ `inconsistent`) o dudoso | 1 |
| (fuera / bloqueados, internos) | `shotOffTarget`, `blockedScoringAttempt` | Remates fuera / bloqueados por un rival (E por H1) | `shots_partition` → 0 | Mismas condiciones, con las otras dos categorías explícitas | Igual | 1 (no mostradas) |
| xG | `expectedGoals` | Goles esperados de sus remates (H) | — (H6 aplazada) | — | Dominio | — |
| Pases | `totalPass` | Pases intentados | — | — | Grupo pases | 1 (solo explícito) |
| Pases completados | `accuratePass` | Pases completados (E por H2) | `won_bound` → 0 | Clave ausente; `totalPass` explícito y == 0 | Grupo pases | Posterior (sin casos en datos activos) |
| Precisión de pase | cociente | `accuratePass / totalPass` | `ratio` | Ambos con valor (`reported` o `derived`) y `totalPass > 0`; si `totalPass == 0` → `not_applicable` | Cualquiera de los dos `null` | 1 (pasa de la pantalla a la API) |
| Regates | `totalContest` | Regates intentados | — | — | Grupo regates | 1 (solo explícito) |
| **Regates ganados** | `wonContest` | Regates logrados (E por H2) | `won_bound` → 0 | Clave ausente; `totalContest` explícito y == 0 | Grupo regates | Posterior (sin casos en datos activos) |
| Recuperaciones | `ballRecovery` | Balones recuperados | — | — | Dominio, duplicados, versiones | 1 (solo explícito) |
| Pérdidas | `possessionLostCtrl` | Pérdidas de posesión | — | — | Igual | 1 (solo explícito) |
| **Duelos ganados / perdidos** | `duelWon` / `duelLost` | Duelos (sin descomposición verificada) | — | — | Igual | 1 (solo explícito) |
| **Faltas** | `fouls` | Faltas cometidas | — | — | Igual | 1 (solo explícito) |
| **Paradas** (porteros) | `saves` | Paradas del portero | — | — | Igual | 1 (solo explícito) |
| *Faltas recibidas* (no mostrada) | `wasFouled` | Faltas recibidas | — | — | Igual | — |
| *Entradas / ganadas* (no mostrada) | `totalTackle` / `wonTackle` | Entradas intentadas / ganadas (E por H2) | `won_bound` para `wonTackle` | Ausente; `totalTackle` explícito == 0 | Grupo entradas | — (fuera de fase 1) |
| *Intercepciones* (no mostrada) | `interceptionWon` | Intercepciones | — | — | Igual | — |
| Goles | `goals` | Goles del jugador (sin autogol) | `omitted_zero` (actual, sección 6) | Sin cambios | Contraste con incidencias | Actual, sin cambios |
| Asistencias | `goalAssist` | Asistencias | `omitted_zero` (actual) | Sin cambios | Contraste con incidencias | Actual, sin cambios |

### 5.1 Cifras de la importación activa (actuaciones jugadas; paradas solo porteros)

Columnas: explícito > 0 / 0 explícito / derivable en fase 1 / desconocido tras fase 1.

| Métrica | > 0 | 0 explícito | Derivable fase 1 | Desconocido tras fase 1 | Nota |
|---|---|---|---|---|---|
| Tiros a puerta | 546 | 45 | 1928 | 0 | 649 por (b), el resto con `totalShots: 0` |
| Tiros | 1209 | 1310 | — | 0 | |
| xG | 1207 | 33 | — | 1279 | Todas con 0 tiros (H6 aplazada) |
| Pases / completados | 2493 / 2464 | 1 / 4 | — | 25 / 51 | El único `totalPass: 0` trae `accuratePass` explícito |
| Precisión de pase | — | — | 2467 `ratio` | 51 + 1 `not_applicable` | Cociente de dos valores con dato |
| Regates / ganados | 1140 / 746 | 31 / 38 | — | 1348 / 1735 | Los 31 `totalContest: 0` ya traen `wonContest: 0` explícito |
| Recuperaciones | 2064 | 9 | — | 446 | |
| Pérdidas | 2445 | 2 | — | 72 | |
| Duelos ganados / perdidos | 2084 / 2130 | 9 / 11 | — | 426 / 378 | |
| Faltas | 1365 | 29 | — | 1125 | |
| Paradas | 147 | 0 | — | 13 | |

Los ceros explícitos de los partidos 16599904 y 17118209 son `reported`: la fase 1 no los cuestiona,
porque la única prueba en contra es el total de equipo (H4, no verificada). Quedan anotados como
observación de calidad en la sección 9.

---

## 6. Goles y asistencias: regla actual `omitted_zero`

Actual (`actuacion()` en `api.py`; README, «Normalización de convocatorias»):

- `unused` → 0 (`unused`); `unknown` → sin dato.
- Clave presente y entera ≥ 0 → `reported`.
- Clave ausente en todos los duplicados, minutos > 0 y al menos `MIN_CLAVES_SUSTANCIALES = 3`
  claves individuales además de minutos y del relleno de banquillo → 0 `omitted_zero`.
- Si las incidencias cuadran con el marcador (clases `regular`, `penalty`, `ownGoal` y su número
  igual a la suma del marcador), goles (`regular` y `penalty`) y asistencias (`assist1`) deben
  coincidir; si no, sin dato y `goals_mismatch` / `assists_mismatch`.
- **Marcador corregido con secciones anteriores** (`anterior_a`, corrección `score`). El evento trae la discrepancia
  `final_score_corrected` y una sección se leyó antes de observarse el marcador vigente, por ejemplo porque su
  relectura falló y se conservó la anterior:
  - la sección se sirve igual, como evidencia, con `meta.predates_score_correction: true`;
  - unas incidencias así no se contrastan, aunque su número de goles cuadre por casualidad;
  - unas alineaciones así dejan sin dato los goles y asistencias de los que jugaron, con la incidencia
    `lineups_before_score_correction` y su evidencia (fechas de lectura, cuándo se observó el marcador y cuál es).
    El gol se trata como dudoso: no permite deducir tiros;
  - la valoración (`rating`) de esas alineaciones tampoco es la del partido vigente, porque la fuente la calcula con
    los goles: queda sin dato con el motivo `before_score_correction` y no entra en medias. El original se conserva;
  - la participación, los minutos, las demás estadísticas individuales y las del equipo no dependen del marcador:
    se siguen usando;
  - el resultado del partido cuenta con el marcador corregido.

  Si una fecha no se puede leer, no se afirma nada. Cuando la sección se vuelve a leer, la señal desaparece.
- **Cambio de equipos con secciones anteriores** (corrección `teams`). El evento trae `identity_changed` con otros
  equipos, y una sección se leyó antes de observarse los equipos vigentes:
  - se sirve igual, como evidencia, con `meta.predates_team_change: true`;
  - la alineación nunca se usa como vigente. El importador asigna sus convocatorias al equipo del lado en el evento
    vigente; la API las aparta (`apartar_alineaciones_de_otros_equipos`, en `componer`). No cuentan para ningún
    equipo ni jugador del partido: ni plantilla observada, ni partidos del jugador, ni acumulados;
  - las incidencias no se usan para nada (ni entradas ni goles);
  - el partido ya queda fuera del balance y de los acumulados por el conflicto de versiones.

En la importación activa:

- Goles: 249 `reported`, 2267 `omitted_zero`, 3 sin dato (1 `goals_mismatch`, 2 actuaciones de
  1 minuto con una sola clave sustancial), 884 `unused`, 24 `unknown`.
- Asistencias: `goalAssist` siempre está presente en las jugadas (2517 `reported`, incluidos
  2394 ceros explícitos); `omitted_zero` no se usa. 2 `assists_mismatch`.
- Los 80 partidos son contrastables: todos los `omitted_zero` están confirmados por las incidencias.

Límites (no se cambia nada en fase 1; se documentan):

1. `omitted_zero` es una aplicación de H5 (hipótesis): la ausencia se trata como cero sin una
   relación dentro del registro. Lo que la respalda es el contraste con las incidencias, que solo
   existe cuando el partido es contrastable; en un partido no contrastable el 0 no queda verificado
   y hoy no se distingue en la salida.
2. El umbral de 3 claves es empírico (la menor actuación de la exportación del 21-10 traía 8).
3. Las actuaciones por debajo del umbral quedan sin dato aunque las incidencias digan 0.
4. El autogol no es gol del jugador; la tanda de penaltis es otro `incidentType`.
5. `assist1` solo identifica al primer asistente.

Propuesta futura (requiere decisión explícita, no en fase 1): añadir `verified_by_incidents`
(true/false) a goles y asistencias, y valorar sustituir `omitted_zero` por una derivación desde las
incidencias cuando el partido sea contrastable.

---

## 7. Presentación en pantalla (dashboard)

El dashboard muestra lo que da la API, sin recalcular:

| Resultado | Pantalla | Texto accesible / título |
|---|---|---|
| `reported` | número | — |
| `derived` | número con marca `*` | «(deducido)» junto al valor; leyenda visible bajo las estadísticas |
| `calculated` | número (porcentaje), sin marca | — |
| `unused` | La ficha ya muestra «No utilizado» sin estadísticas | — |
| `not_reported`, `reported_null`, `not_applicable` | «—» | «Dato no disponible» |
| `inconsistent` | «—» con marca `†` | «Cifras contradictorias en la fuente»; leyenda |
| `version_conflict`, `duplicate_disagree` | «—» con marca `‡` | «Versiones de la fuente que no coinciden»; leyenda |
| `invalid` | «—» con marca `§` | «Valor no válido en la fuente»; leyenda |
| `participation_unknown` | Sin bloque de estadísticas (comportamiento actual) | — |

Las marcas son discretas (superíndice, fuera de la caja del número para no desalinear la columna) y
la leyenda solo lista las presentes en el partido seleccionado: no dependen del puntero. Para
lectores de pantalla, el significado va junto a cada valor.

---

## 8. Agregados (fase posterior; calculados en la API)

Para una métrica de recuento sobre las convocatorias de un jugador en un ámbito:

- `of` = convocatorias `played` + `unknown`. Las `unknown` cuentan en `of`: pudieron jugar y no se
  conoce su cifra, así que la cobertura no puede ser completa. Las `unused` no cuentan.
- `matches` = convocatorias `played` con `value` (`reported` o `derived`).
- `value` (total) = suma de esos valores; `null` si `matches == 0`.
- Cobertura: `complete` si `matches == of`, `partial` si `0 < matches < of`, `none` si 0.
- Media por partido = total / `matches`, siempre con su cobertura («en 4 de 6 partidos»).
- Por 90 = total / minutos × 90, donde los minutos son los de las mismas convocatorias de
  `matches` con minutos conocidos. Nunca se usan minutos de partidos sin la cifra.
- Porcentajes = Σ numerador / Σ denominador en las convocatorias donde **ambos** tienen valor y no
  hay incoherencia; denominador total > 0. Nunca la media de porcentajes por partido.
- `inconsistent`, `version_conflict`, `invalid`, `not_reported` no entran en numerador ni en
  denominador y hacen la cobertura parcial.
- Los derivados cuentan como valores; los `omitted_zero` de goles también (comportamiento actual de
  los acumulados).

Hoy no existen en el dashboard agregados de estas métricas (solo apariciones, titularidades,
minutos, goles y asistencias), así que la fase 1 no cambia ninguna cifra agregada mostrada.

---

## 9. Ejemplos (importación activa)

### 9.1 Moha Keita (966303) ante Rayo Majadahonda (16599928, J8, 90′)

Original: `payload.home.players[9].statistics` (E1, E2 y E16 idénticas; E16 seleccionada).

| Métrica | Original | Fase 1 |
|---|---|---|
| Tiros | `totalShots: 2` | 2 `reported` |
| Tiros a puerta | ausente; `blockedScoringAttempt: 2`, `shotOffTarget` ausente, `goals` ausente | 0 `derived` (`shots_partition`, b: 2 bloqueados = 2 tiros) |
| Asistencias | `goalAssist: 0` | 0 `reported` (cero explícito) |
| Goles | ausente | 0 `omitted_zero` (actual; contrastado con incidencias) |
| Regates / ganados | `totalContest: 4` / ausente | 4 `reported` / `null` `not_reported` (no resoluble en fase 1) |
| Pases / completados / precisión | 21 / 13 | 21 / 13 `reported` / 61,9 % `derived` (`ratio`) |
| Faltas | `fouls: 1` | 1 `reported` |

El total de equipo del Real Jaén en ese partido (`dribblesPercentage` 0 de 11) haría 0 los regates
ganados de Keita, pero esa vía (H4) no se aplica: queda «—».

### 9.2 Cero explícito

Keita, `goalAssist: 0` (todas sus convocatorias). Partido 16599904: `onTargetScoringAttempt: 0`
explícito en 23 jugadores → `reported`, nunca `derived`.

### 9.3 Cero derivable

Keita J6 ante Hércules (16599748, 22′): `totalShots: 0`, tiros a puerta ausente → 0 `derived`
(`shots_partition`, a). Keita J4 ante UD Ibiza (16599916, 30′): `totalShots: 1`, `shotOffTarget: 1`
→ tiros a puerta 0 `derived` (b).

### 9.4 Ausencia no resoluble

Keita J4: `fouls` y `totalContest` ausentes → `not_reported`. Keita J8: `wonContest` ausente con
`totalContest: 4` → `not_reported`. Cualquier `wasFouled`, `saves` o `possessionLostCtrl` ausente.

### 9.5 Datos contradictorios

- Real, regla existente: partido 16599966, Carlos Segura Clausell, `statistics.goals: 1` frente a
  0 goles en incidencias → `goals_mismatch`, goles sin dato.
- Intra-registro (fase 1): no hay ningún caso en la importación activa; se cubre con pruebas
  sintéticas (sección 11, T5 y T6).
- Observado pero **no** aplicado (depende de H4): partido 16599904, `wonTackle: 0` en los 30
  jugadores mientras el equipo declara `wonTacklePercent` 9 y 11 «de 0» y `totalTackle: 0`; las
  recuperaciones de jugadores suman 168 frente a 83 del equipo. Partido 16599684, local: el equipo
  declara 1 bloqueado con total 6 = 2 a puerta + 4 fuera.

### 9.6 Conflicto entre versiones

No hay ninguno en la importación activa. Caso de prueba sintético: dos versiones posteriores al
partido con minutos idénticos, una con `blockedScoringAttempt: 2` y otra con 1 para el mismo jugador
→ grupo tiros `version_conflict` (sin derivación); pases, regates y el resto conservan su valor; la
participación no cambia.

---

## 10. Cambios de contrato

### API (`api-real-jaen`), fase 1: aditivo

En cada convocatoria normalizada (`/players/{id}/matches` → `normalized`, `/matches/{id}/lineups`
→ `normalized.players[]`) se añade:

```json
"stats": {
  "shots_on_target": {"value": 0, "source": "derived", "rule": "shots_partition", "reason": null},
  "dribbles_won":    {"value": null, "source": null, "rule": null, "reason": "not_reported"}
}
```

Métricas: `shots`, `shots_on_target`, `xg`, `passes`, `passes_accurate`, `pass_accuracy`,
`dribbles`, `dribbles_won`, `ball_recoveries`, `possession_lost`, `duels_won`, `duels_lost`, `fouls`,
`saves`, `rating`. Nuevos códigos en `issues`/`evidence`: `stat_inconsistent`, `stat_version_conflict`,
`stat_invalid`, `stat_duplicate_disagree`, con claves, valores originales, grupo y versiones.
`minutes`, `goals` y `assists` no cambian. `statistics` original se sigue sirviendo intacto.

### Dashboard (`rj-analyst`), implementado con aprobación (ampliación mínima)

- `Stat` = `{ value: number; derivation?: StatDerivation } | { value: null; reason: MissingReason }`,
  con `StatDerivation` = `derived`·`shots_partition` o `calculated`·`ratio`.
- `MissingReason` añade `inconsistent`, `conflicting` (versiones o duplicados) e `invalid`.
  `not_reported`, `reported_null`, `participation_unknown` y `not_applicable` se leen como
  `not_available`.
- `passAccuracy` pasa a ser una métrica del contrato servida por la API; la pantalla ya no la calcula.
- El adaptador lee solo `normalized.stats` (no `statistics`) y rechaza una respuesta sin ella o con
  una derivación, regla o motivo desconocidos: un servicio anterior a esta fase no es compatible.

---

## 11. Fase 1 (implementada)

Alcance: `api.py` (`actuacion()`), su README y sus pruebas, y el consumo en `rj-analyst`
(contrato, adaptador, ficha de jugador). Sin cambios en importador, datos ni servicios.

Efecto medido sobre la importación activa (2519 actuaciones jugadas): tiros a puerta pasa de
591 `reported` + 1928 «—» a 591 `reported` + 1928 `derived`; precisión de pase 2467 `calculated`,
51 sin dato y 1 `not_applicable`; el resto de métricas, sin cambios; ninguna incidencia `stat_*`.

1. Estructura `stats` para las métricas de la sección 10, con `reported`/`unused`/`null` + `reason`.
2. Derivaciones: `shots_partition` (tiros a puerta) y `ratio` (precisión de pase). Ninguna otra.
   `won_bound` (H2) queda especificada pero fuera de la fase 1: en la importación activa no
   resuelve ningún caso (todo «intentados = 0» trae ya «ganados = 0» explícito). H2 sí se usa en
   fase 1 como control de coherencia.
   Consecuencia explícita: la fase 1 **no** resuelve los «—» de regates ganados ni de faltas; solo
   dependerían de H4, que no se aplica.
3. Controles de coherencia de los grupos tiros, regates y pases; duplicados; dominio.
4. Conflicto de versiones por grupo (4.3).
5. Goles y asistencias sin cambios (prueba de regresión).

### Casos de prueba

| Id | Entrada (registro jugado salvo indicación) | Esperado |
|---|---|---|
| T1 | Keita J8: `totalShots: 2`, `blockedScoringAttempt: 2`, resto ausente | tiros a puerta 0 `derived` `shots_partition`; tiros 2 `reported` |
| T2 | `totalShots: 0`, a puerta ausente | 0 `derived` |
| T3 | `onTargetScoringAttempt: 0` explícito | 0 `reported` |
| T4 | `totalShots: 2`, `blockedScoringAttempt: 1`, resto ausente | `null` `not_reported` (no se suma un ausente como 0) |
| T5 | `totalShots: 1`, `onTargetScoringAttempt: 1`, `shotOffTarget: 1` | grupo tiros `inconsistent` + evidencia; pases y regates intactos |
| T6 | `totalShots: 1`, `shotOffTarget: 1`, `goals: 1` | tiros a puerta sin derivación, grupo `inconsistent` (goles > 0 con 0 a puerta) |
| T7 | `totalShots: 1`, `shotOffTarget: 1`, `hitWoodwork: 1` | tiros a puerta 0 `derived` (el palo no cuenta) |
| T8 | Suplente no utilizado (`totalShots: 0`, `statisticsType`) | recuentos 0 `unused`; xG `null`; no cuenta como jugado |
| T9 | Participación `unknown` con `totalShots: 0` | todo `participation_unknown`; ninguna derivación |
| T10 | Duplicados con `blockedScoringAttempt` 2 y 1 | grupo tiros `duplicate_disagree`; regates intactos |
| T11 | `blockedScoringAttempt` = -1 / `true` / 1,5 / `"2"` | esa clave `invalid`; sin derivación de tiros a puerta; otros grupos intactos |
| T12 | `onTargetScoringAttempt: null` | `reported_null`; no se deriva |
| T13 | Dos versiones posteriores con distinto `blockedScoringAttempt` | grupo tiros `version_conflict`; pases intactos |
| T14 | `totalContest: 4`, ganados ausente / `wonContest: 3`, `totalContest: 2` / `accuratePass: 22`, `totalPass: 21` | `not_reported` / grupo regates `inconsistent` / grupo pases `inconsistent` y precisión `null` |
| T15 | `totalPass: 0` / `totalPass: 21`, `accuratePass: 13` / `accuratePass` ausente con `totalPass: 21` | precisión `not_applicable` / 61,9 `ratio` / `null` |
| T16 | Sección de alineación guardada, clave ausente en todos los jugadores del partido | `not_reported` en todos (la sección no prueba cobertura) |
| T17 | Fixtures actuales de goles y asistencias | salida idéntica a la actual (`omitted_zero` incluido) |

Validación manual adicional (solo lectura): recalcular las cifras de la sección 5.1 sobre la
importación activa y comprobar que coinciden.

---

## 12. Hipótesis pendientes de verificar con la fuente

| Hipótesis | Cómo verificarla (requiere decisión y, en su caso, nuevas peticiones) |
|---|---|
| H1 como convención universal | Datos por evento (mapa de tiros) de varios partidos y competiciones. |
| H4 (equipo = suma de jugadores) | Comparar con una fuente por eventos independiente; si se confirma, permitiría resolver regates ganados, faltas, entradas, intercepciones, duelos, recuperaciones y paradas. |
| H5 (omisión de ceros) | Comparar los partidos «rellenos» con los demás y averiguar por qué cambian de formato. |
| H6 (xG con 0 tiros) | Decisión de producto; la evidencia lo hace compatible. |
