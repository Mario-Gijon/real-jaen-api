# Receta del Grupo 2 (`sofascore-group2-2627-v1`) y publicación con cobertura parcial

Implementada el 28-10-2026 sobre el único recorrido existente: botón, `control.py`, cliente, servicio de
adquisición, coordinador. Validada con horas simuladas, sitio local y una copia aislada, y ejecutada una vez contra
SofaScore el 29-10-2026 sobre una copia aislada, con los detalles restringidos a la jornada 9
([prueba-real-grupo2.md](prueba-real-grupo2.md)). Contratos: [contrato-adquisicion-interno.md](contrato-adquisicion-interno.md) y
`../rj-analyst/docs/contrato-actualizaciones.md`.

## Decisiones aplicadas

- **Publicación por recurso**, con cobertura parcial; detalle en el apartado «Publicación con cobertura parcial».
- **Límites por pulsación:** 120 envíos físicos, 116 intentos lógicos en el transporte, 100 planificados, 900 s de
  trabajo y 1200 s de límite duro del proceso.
- **Jornadas próximas:** las que tienen partidos en los 7 días siguientes, más 6 jornadas lejanas por pulsación, en
  rotación.
- **Partidos en juego:** solo se pide su evento; sus secciones quedan «no aplicables» por fase.
- **Receta del piloto:** se conserva para las pruebas y para la evidencia del 28-10. En el servicio de control, la
  receta por defecto es la del grupo (`RJ_UPDATE_RECIPE`).
- **Actualización manual:** se mantiene; sin programación periódica.

## Plan (`acquisition/group_plan.py`, funciones puras)

**Jornadas que se listan:**
- **Próximas:**
  - con un partido vigente sin finalizar cuyo inicio ya pasó o está en los 7 días siguientes;
  - con un aplazado vigente;
  - con un finalizado dentro de la ventana de 72 h.
- **Lejanas, en rotación:** las 6 restantes con partidos por jugar, empezando por las comprobadas hace más tiempo. La
  última comprobación es la más reciente de tres fuentes: la observación del evento, la del calendario publicado y
  el último listado correcto que guarda el propio servicio de adquisición (`round_listings`, a partir de los
  registros de su transporte). Hace falta esa memoria del servicio porque un listado que no cambia nada no deja
  ninguna huella nueva en la base publicada; sin ella, la rotación se quedaba en las mismas jornadas (comprobado y
  corregido en la integración).

**Partidos que se consultan, en este orden y dentro del presupuesto:**

1. `pending`: finalizados en el inventario a los que les falta alguna sección. Solo se piden esas secciones y el
   evento.
2. `finished`: el listado los da por finalizados y el inventario no, **sin importar la antigüedad**. Así se
   recuperan resultados con más de 72 h y aplazados ya jugados.
3. `changed`: el listado difiere del inventario en identidad, estado, marcador u hora (`importar.rasgos_evento`).
   Puede ser una corrección o un cambio de calendario, también lejano. Si el partido no está finalizado, solo se
   pide su evento.
4. `refresh`: la regla del SDK. Finalizado, observado antes de 72 h tras el inicio y hace al menos 6 h.

Lo que no cabe queda **aplazado** (`deferred`) para la pulsación siguiente. Sus datos se conservan; no es un
pendiente.

**IDs nuevos detectados:** si una jornada listada contiene un ID que no está en el inventario, se informa en
`new_matches` para revisión. No se solicitan sus detalles ni se incluye en el calendario o las exportaciones de la
publicación. Si repite el emparejamiento de un partido sin finalizar, se muestra como posible sustitución; decide el
operador mediante la herramienta local, nunca se enlaza automáticamente. El histórico (`retained`) nunca se consulta
ni se toca.

**Caché:** el inventario publicado es la caché. Solo se pide lo que el plan decide. Las jornadas completas fuera de
la ventana no se listan, y lo no consultado viaja con su original exacto.

### Paridad temporal con el SDK

`group_plan.refresh_due` y `sofascore_scraper.services.planning.refresh_due` dan la misma decisión en una rejilla de
336 combinaciones de inicio, observación y hora actual, incluidos los bordes de 72 h y 6 h. Las constantes coinciden
con los valores por defecto de `RefreshSettings` del SDK (72 h y 6 h). Las pruebas `SdkParity` de
`tests/acquisition/test_group_plan.py` se ejecutan en la imagen con el SDK.

## Publicación con cobertura parcial

Cada recurso de cada partido vigente (evento, alineaciones, estadísticas e incidencias, más su fila de calendario)
lleva una cobertura. Hay tres clases que nunca se mezclan:

| Clase | Cobertura | Significado |
|---|---|---|
| **Dato nuevo** | `data` / `attempt` | Respuesta correcta y con datos, obtenida en esta actualización; ligada a su intento y su respuesta original |
| **Dato anterior conservado** | `data` / `inventory` | No se consultó (no tocaba, aplazado por presupuesto o en espera de un error anterior); viaja el original publicado exacto |
| **Pendiente** | `pending` + motivo | Se consultó sin datos utilizables, o no hay datos y hacían falta. `origin: inventory`: se sigue sirviendo la versión anterior. `origin: none`: no hay nada publicado |
| No aplicable | `not_applicable` / `phase` | Partido sin jugar o en juego |

Motivos de un pendiente: `empty` (respuesta sin datos), `not_found` (404), `parse` (ilegible), `error` (5xx o red),
`budget` (no se llegó a pedir) y `not_requested` (su evento falló).

**Ningún motivo es ausencia confirmada y ninguno se convierte en cero.** La API sigue diciendo «no disponible» para
una sección sin datos, y `confirmed_absence` sigue prohibido.

Garantías del verificador (`persistencia/bundle.py`, `_verify_group`):
- un recurso con un original anterior **nunca desaparece**: es dato nuevo, el mismo original o un pendiente que lo
  conserva;
- lo no consultado es idéntico al inventario;
- lo consultado coincide con su intento;
- un vacío nunca cuenta como dato;
- los IDs nuevos detectados se informan, pero no se importan ni reciben consultas de detalle;
- el histórico, la generación, el productor aprobado y el transporte, como en el piloto.

Si el proveedor rechaza (403 sin challenge, 429, challenge repetido o interruptor abierto), o falla el navegador, la
actualización termina **sin publicar nada**, como en el piloto. El agotamiento del presupuesto o del plazo no es un
rechazo: se publica lo obtenido.

El resultado del trabajo trae el resumen (`coverage`):
- datos nuevos, conservados y pendientes por partido, con su motivo;
- aplazados y posibles sustituciones, pendientes de decisión local;
- jornadas listadas;
- nombres de los partidos, sacados de la generación recién publicada.

Si quedan pendientes, la interfaz muestra **«Actualización completada con pendientes»**.

**Secciones antiguas con eventos nuevos.** El importador elige la versión de cada sección por separado. Un evento
corregido (marcador nuevo) cuyas secciones no se pudieron releer se sirve con esas secciones: mismos datos, mismo
`fetched_at` y sin discrepancia. Solo crece el número de copias idénticas de esa evidencia, porque cada bundle
reenvía el original conservado.

- **Señal.** La API marca cada sección leída antes del marcador corregido con `meta.predates_score_correction`. El
  resumen de la actualización la da como pendiente con la versión anterior conservada.
- **Exclusión de los análisis.** Se excluye lo que depende del marcador:
  - las incidencias no se contrastan;
  - los goles y asistencias de unas alineaciones anteriores quedan sin dato (`lineups_before_score_correction`).

  Lo demás se sigue usando: participación, minutos, estadísticas del equipo y resultado con el marcador corregido.
  Detalle: [normalizacion-estadisticas-individuales.md](normalizacion-estadisticas-individuales.md), apartado 6.
- **Al releerlas,** la señal desaparece y vuelven a usarse.
- **En el dashboard,** la ficha del partido avisa: «El marcador se ha actualizado, pero algunas secciones siguen
  pendientes de revisión», y nombra esas secciones. Los goles y asistencias de unas incidencias anteriores no se
  marcan; los cambios, sí. Contrato: `ObservedMatchDetail.review` en `rj-analyst/src/contract/index.ts`.
- **Cambio de equipos.** Las secciones leídas antes de un cambio de equipos se marcan con
  `meta.predates_team_change`. Nunca se usan como vigentes: en el dashboard su estado es `outdated` y no se muestran.

**Revisiones aplazadas visibles.** El estado del control (`last_published.coverage`) conserva la cobertura de la
última publicación, con los nombres. Si una actualización posterior falla o se cancela, la pantalla del operador
sigue mostrando los pendientes y las revisiones aplazadas de esa publicación, que siguen sirviendo sus datos
anteriores. Las aplazadas se listan por partido, no solo como número.

## Corrección encontrada durante la validación

Ante un 5xx en el navegador, el SDK reintenta por curl, recibe el 403 normal del challenge y resume «403». El
adaptador lo trataba como un rechazo y detenía todo. Ahora, si todos los 403 de esa petición fueron de challenge y
hubo un 5xx, el resultado es el 5xx: pendiente con motivo `error`, sin detener nada.

Un 403 sin challenge sigue deteniéndolo todo, también tras un 5xx: el adaptador se detiene en ese envío y el
trabajo termina sin publicar. Escenarios de `test_sdk_adapter.py`:
- `servidor-500`: 500 y después el challenge normal; es un 500.
- `servidor-500-prohibido`: 500 y después un 403 sin challenge. Es un rechazo: el último envío es ese 403 y no
  sale nada más.

## Presupuesto (estimación con el inventario del 27-10)

1 envío por jornada listada, 4 por partido (o 1 si solo se pide el evento) y 1 o 2 del arranque del navegador.

| Situación | Envíos aprox. |
|---|---|
| Una jornada jugada, sin otras novedades | 8 jornadas (2 próximas y 6 lejanas) + 40 ≈ 50 |
| Dos jornadas en 72 h | ≈ 90 |
| Repetir sin novedades | ≈ 10 (solo listados) |

La rotación de las 28 jornadas lejanas completa una vuelta en 5 pulsaciones.

## Validación (28-10-2026)

**Pruebas:**
- `tests/acquisition/test_group_plan.py` (16, más 3 de paridad con el SDK en la imagen):
  - jornadas próximas y rotación lejana, también con los listados del servicio;
  - finalizado recuperado fuera de las 72 h;
  - cambio lejano de calendario que solo pide el evento;
  - regla 72 h / 6 h;
  - prioridad de pendientes y presupuesto;
  - ID detectado y posible sustitución, sin plan de detalles;
  - histórico intacto.
- `tests/acquisition/test_sdk_adapter.py`: 10 escenarios con el navegador real, sandbox exigido. Incluyen el 500
  tras el challenge y el 500 seguido de un 403 sin challenge.
- `tests/test_api.py` (`CorreccionDeMarcador`, 7): marcador corregido con secciones anteriores. Comprueba la señal,
  la evidencia intacta, las exclusiones, el resultado con el marcador corregido, la relectura y una fecha ilegible.
- `tests/test_control.py`: las revisiones aplazadas de la última publicación siguen en el estado tras un rechazo.
- Contrato y transporte: 27; control: 9; suite del anfitrión: 261; frontend: Vitest 612 y e2e 91.

**Recorrido completo:** `docker/acquisition/test_group.py`. Usa el sitio local con la temporada construida a partir
de los originales de la copia inactiva, el servicio de adquisición con el navegador y el sandbox, el control del
backend y la copia aislada. Comprueba también que un ID nuevo en una jornada se informa sin pedir sus detalles ni
incluirlo en el calendario publicado. Cuatro pulsaciones con horas simuladas:

1. **Día siguiente a la jornada 9.** Resultados nuevos. Unas estadísticas vacías y unas alineaciones con 404 quedan
   pendientes sin datos (la API responde `data: null`, nunca ceros). Un partido sigue sin resultado. Un cambio de
   hora en la jornada 11, lejana, se publica.
2. **8 h después, con poco presupuesto.**
   - Los pendientes se recuperan primero.
   - Una corrección de marcador trae un evento nuevo mientras sus estadísticas fallan con 500: se conservan las
     anteriores.
   - Seis relecturas quedan aplazadas con sus datos conservados.
   - La rotación pasa a otras jornadas lejanas.
3. **Un día después, con poco presupuesto.**
   - Primero se releen las secciones del partido corregido (pendientes): la señal desaparece y sus goles vuelven a
     usarse.
   - Después se recupera el partido sin resultado. Ya está finalizado y han pasado más de 72 h desde su inicio.
   - Sin pendientes. El resto de relecturas queda aplazado y la rotación vuelve a avanzar.

   **Por qué se recupera en esta pulsación.** No es por el presupuesto, sino por el listado. En las pulsaciones 1
   y 2 el sitio lo daba como no empezado: no era candidato y el plan no lo consideraba. En la 3, el listado lo da
   por finalizado y el inventario no, así que entra por el motivo `finished`. Ese motivo no depende de la ventana de
   72 h y va por delante de las relecturas, detrás solo de los pendientes.
4. **Una hora después, rechazo.**
   - Un cambio de hora en una jornada próxima hace pedir un evento.
   - El navegador recibe un 500 y el reintento, un 403 sin challenge.
   - El trabajo falla (`ACQUISITION_FAILED`) y no se publica nada: misma última publicación y hora sin cambiar.
   - El estado sigue mostrando las revisiones aplazadas de la pulsación 3, con nombre, y sus partidos sirven los
     mismos datos.

**Con el backend detenido:**
- integridad e identidades conservadas;
- los tres bundles sellados siguen verificándose;
- cinco manipulaciones rechazadas: un pendiente presentado como dato, un original anterior eliminado, un partido
  nuevo no listado, un ID detectado añadido al calendario y un original no consultado alterado.

## Restricciones de una instalación

- **Solo para pruebas.** Solo pueden reducir los máximos de producción:
  - `ACQ_MAX_ATTEMPTS` y `ACQ_MAX_SENDS`;
  - `ACQ_DETAIL_ROUNDS`, que limita los detalles a unas jornadas; lo demás queda como `restricted`.

  El manifiesto las declara (`plan.limits`, `plan.detail_rounds`), el verificador las comprueba y el backend puede
  exigirlas (`RJ_ACQUISITION_*`). Detalle en [prueba-real-grupo2.md](prueba-real-grupo2.md).

## Pendiente

- **Hecho el 29-10: una prueba real con una jornada.**
  - 48 intentos y 49 envíos.
  - Los listados reales traen los dos grupos y el filtro funciona.
  - Hubo un challenge al principio y 48 peticiones seguidas por el navegador sin otro challenge.
  - Pendiente: una actualización real sin restricción.
- **El mantenimiento único de la base activa** antes de usarlo sobre ella.
- **Las correcciones de jornadas completas con más de 72 h** no se detectan, salvo las que cambian el listado de una
  jornada que se vuelve a listar. Es el alcance de la regla 72 h / 6 h.
