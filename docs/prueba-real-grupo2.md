# Prueba real del Grupo 2: una jornada, en Docker, sobre una copia aislada

Preparada y ensayada sin red el 29-10-2026. **Ejecutada una sola vez el 29-10-2026, con autorización**:
resultado en «Ejecución real». No toca la base activa ni los servicios en marcha: publica solo en la copia aislada de
un volumen nuevo.

## Alcance: detalles solo de la jornada 9

La restricción no depende de la fecha. Es explícita y se aplica en tres puntos independientes:

1. **Servicio de adquisición** (`ACQ_DETAIL_ROUNDS=9`, `acquisition/service.py`). El plan solo consulta partidos de
   la jornada 9: `group_plan.plan_matches(detail_rounds=...)`. Lo que el calendario detecte en otras jornadas no
   se consulta y se señala:
   - cambios de horario;
   - relecturas debidas;
   - partidos nuevos;
   - filas sin jornada.

   Todo eso queda aplazado con el motivo `restricted` en `plan.deferred`. Sus datos se conservan; un recurso sin
   datos previos queda pendiente con ese motivo.
2. **Verificador del bundle** (`persistencia/bundle.py`, `check_plan`). El manifiesto declara `plan.detail_rounds`
   y `plan.limits`. Se rechaza el bundle si:
   - algún intento de evento es de otra jornada (según su calendario);
   - los límites declarados superan los de producción;
   - los intentos superan lo declarado;
   - hay un `restricted` sin jornadas declaradas.
3. **Backend de la prueba** (`RJ_ACQUISITION_MAX_ATTEMPTS`, `RJ_ACQUISITION_MAX_SENDS`, `RJ_ACQUISITION_DETAIL_ROUNDS`
   en `control.py`). Exige que el bundle declare exactamente esa restricción y esos límites
   (`comprobar_restriccion`). Si no, no llega al coordinador.

Los listados de jornada no están restringidos: son los que detectan los cambios. El plan lista las jornadas 9 y 10
y seis lejanas.

## Presupuesto: dos contadores, solo reducibles

| Contador | Qué cuenta | Producción | Esta prueba | Dónde se aplica |
|---|---|---|---|---|
| Intentos lógicos | Cada GET de una ruta por el transporte, reintentos incluidos | 116 | **60** | `Transport.max_attempts`, anotado antes de cada envío; el plan programa como máximo esos |
| Envíos físicos | Cada envío del adaptador: curl o `fetch()` del navegador, comprobaciones del bridge incluidas | 120 | **70** | `SdkAdapter.max_sends`, tope absoluto |

- El servicio rechaza arrancar con valores no enteros, mayores que los de producción o con más intentos que envíos.
- Por debajo de una reserva de 4 envíos físicos (el peor caso de una petición), el transporte no abre más intentos.
- Agotar cualquiera de los dos no es un rechazo: lo obtenido se publica y el resto queda pendiente por `budget`, o
  aplazado, como en la política de cobertura parcial.
- Agotar el tope físico absoluto sí detiene sin publicar. La reserva lo evita.

Estimación con la copia: 8 listados + 40 detalles de la jornada 9 = 48 intentos y unos 49 envíos físicos.

## Herramientas

- `docker/acquisition/compose.real-test.yaml`: base **sin salida**. `acquisition` solo está en la red interna y
  `backend-control` solo publica en loopback.
- Capas sobre la base:

  | Archivo | Qué añade |
  |---|---|
  | `compose.real-test.fixture.yaml` | Proveedor local para el ensayo |
  | `compose.real-test.sofascore.yaml` | Proveedor real |
  | `compose.real-test.egress.yaml` | La red de salida (el único archivo que la da) |

- `docker/acquisition/real_group.py`, el ejecutor. Modos:
  - `rehearsal` (escenarios `ok`, `budget` y `refusal`);
  - `offline` (imágenes definitivas, proveedor real, sin salida);
  - `real`.
- `tests/acquisition/integration_flow.py`, órdenes del cliente del backend:
  - `prepare-real`: huella de la copia; sin marca de prueba salvo en el ensayo con el proveedor local;
  - `real-report`: informes del servicio mientras está inactivo;
  - `real-check`: integridad, identidades y bundle verificado de nuevo con la restricción.

## Salvaguardas

**Ejecución única.** El registro `%LOCALAPPDATA%\rj-acquisition\real-runs\sofascore-group2-2627-v1-round9.started`:
- se crea en exclusiva justo antes de arrancar `acquisition`;
- si existe, el ejecutor se niega antes de crear nada;
- los ensayos no lo tocan.

**Antes de crear nada, el ejecutor exige:**
- `--confirm-real-run`;
- estar dentro de la ventana (`--not-after`, por defecto 30-10 19:00 UTC);
- una copia de `copias/` con su sha256, nunca `real-jaen.sqlite`;
- una carpeta de evidencias nueva;
- imágenes por identificador.

`--preflight-only` comprueba todo esto sin crear nada.

**Aislamiento:**
- proyecto y volúmenes nuevos y etiquetados, que se conservan;
- la red de `acquisition` comprobada: con salida solo en `real`;
- la base activa (sha256, tamaño y fecha) y los contenedores en marcha comparados antes y después;
- secretos temporales que se borran al terminar, y su ausencia comprobada en todas las salidas y registros.

**Condiciones de parada.** Se detiene sin publicar ante:
- un 403 sin challenge (también tras un 5xx);
- un challenge repetido;
- un 429 o un 503;
- 3 errores 5xx seguidos;
- un fallo de red o del navegador;
- una ruta fuera de la lista;
- un sandbox no acreditado.

Tras un 403 o un 429 queda un bloqueo persistente.

**Publicación:** solo sobre la copia del volumen `state`, por el mismo camino que el botón (`control.py`, cliente y
coordinador).

## Ensayos sin red (29-10)

Con las imágenes definitivas y la misma copia, cuatro ensayos:

| Ensayo | Comprobado |
|---|---|
| `rehearsal ok` | Publica en la copia. Solo los 10 partidos de la jornada 9 consultados. Los cambios de horario simulados en la jornada 10 y en una jornada lejana, señalados como `restricted`. 48 intentos y 49 envíos. |
| `rehearsal budget` (30/40) | Publica lo obtenido. Los partidos que no caben quedan aplazados y sus secciones pendientes por `budget`. |
| `rehearsal refusal` | 500 en el navegador y 403 sin challenge: falla sin publicar, sin bundle sellado. |
| `offline` | Imagen definitiva con el proveedor real y sin salida: el primer envío falla por DNS, se detiene sin publicar y nada recibe respuesta. |

Todos comprueban, además:
- los dos contadores;
- los límites en vigor;
- los detalles solo de la jornada 9;
- la red de `acquisition` solo interna;
- la base activa sin cambios y los demás contenedores sin cambios;
- ningún secreto en las salidas.

Evidencias: `%TEMP%\rj-real-group-v2-*`. Los ensayos anteriores (`rj-real-group-ensayo-*` y `-final-*`)
detectaron que las secciones de un partido aplazado por presupuesto salían como «no aplicables»; corregido.

## Comprobaciones posteriores de la ejecución real

Las hace el mismo ejecutor y quedan en `results.json`:
- trabajo y cobertura;
- contadores e informe del servicio (envíos, sandbox, documentos del navegador, conexiones);
- detalles solo de la jornada 9;
- integridad e identidades;
- bundle verificado de nuevo con la restricción;
- generación publicada sobre la copia;
- base activa y contenedores sin cambios.

Pendiente de revisión manual: los resultados de la jornada 9 frente a la fuente y el dashboard sobre la copia.

## Ejecución real (29-10-2026)

**Autorización:**
- una sola ejecución;
- imágenes verificadas;
- detalles solo de J9;
- 60 intentos y 70 envíos físicos;
- caducidad: 30-10-2026, 19:00 UTC.

**Cómo se lanzó:**
- con el comando guardado y comprobado antes de lanzarlo: hashes, límites, J9, caducidad, copia, y ejecutor y
  Compose iguales al árbol de las imágenes;
- el árbol git era `5c5fa98c20bdfd8feb03dff42dadd544a3f3d22b`;
- cada archivo de las cuatro imágenes es idéntico (sha256) al del árbol;
- el commit que lo consolida tiene el mismo código: solo cambian los documentos, que ninguna imagen contiene.

**Imágenes:**

| Imagen | Identificador |
|---|---|
| Adquisición | `sha256:338139cca602896cc8394965663349f594bb28966fb219166ffaebb238d13aed` |
| Backend | `sha256:4f7a56a60fef654e8aa6faa1088886b596d6fd85f5066b651e811704b4186974` |
| Cliente del backend | `sha256:cc05daa5a04c554d10e6236c746826d02938be33ff8be86a599d66038ebc1b12` |

Huella del productor: `da30e4cfe990139e1cf0ca8dd190110564176f981996ce469faf9bb55891ffbb`.

**Desarrollo:**
- Trabajo de 10:47:29 a 10:49:44 UTC, completado y publicado en la copia (proyecto `rj-real-group-real-9207547e`).
- 48 intentos lógicos de 60 y 49 envíos físicos de 70:
  - 1 por curl: 403 con challenge;
  - 48 `fetch()` del navegador: todos 200.
- Una sola apertura del navegador, con sandbox acreditado. Dominios de las páginas: `www.sofascore.com`,
  `challenges.cloudflare.com` y `brunhild.challenges.cloudflare.com`.
- No saltó ninguna condición de parada.
- El registro de ejecución única `sofascore-group2-2627-v1-round9.started` queda creado: no se repite.

**Plan ejecutado:**
- Jornadas listadas: próximas 9 y 10; lejanas 11 a 15 y 38. Ninguna falló.
- Los listados reales traen los dos grupos (80 filas de cada uno); el filtro de ámbito dejó solo el Grupo 2.
- Ni partidos nuevos ni posibles sustituciones.

### Jornada 9

Los 10 partidos tienen como dato nuevo el evento, las estadísticas, las alineaciones (confirmadas) y las
incidencias: 40 recursos. El resto, 608, se conserva como estaba.

- Ningún pendiente ni aplazado.
- Ninguna sección anterior a una corrección.
- En los 10, el número de goles de las incidencias cuadra con el marcador.

| Partido | Fecha (UTC) | Resultado |
|---|---|---|
| 16599975 CE Europa – Real Zaragoza | 23-10 19:15 | 0-1 |
| 16599980 Juventud Torremolinos CF – Real Murcia | 24-10 11:00 | 1-0 |
| 16599982 Rayo Majadahonda – UE Sant Andreu | 24-10 14:00 | 2-1 |
| 16599977 Huesca – Antequera | 24-10 14:30 | 1-1 |
| 16599986 CD Teruel – CDA Águilas FC | 24-10 16:45 | 1-1 |
| 16599967 AD Alcorcón – UD Ibiza | 25-10 11:00 | 3-3 |
| 16599973 Atlético Madrileño – Real Jaén | 25-10 11:00 | 1-1 |
| 16599988 Villarreal B U23 – FC Cartagena | 25-10 13:15 | 4-1 |
| 16599970 Algeciras CF – Real Madrid Castilla U21 | 25-10 15:30 | 2-0 |
| 16599984 Gimnàstic de Tarragona – Hércules CF | 25-10 17:45 | 4-2 |

En tres partidos (16599967, 16599977 y 16599988), algún convocado trae en la fuente un `teamId` distinto del de su
lado de la alineación. La API lo marca como advertencia (`source_team_mismatch`) y no cambia ninguna cifra.

### Cambios fuera de alcance

Los listados detectaron estos cambios y el plan los aplazó como `restricted`: no se consultó su evento.

| Partido | Jornada | Publicado | Listado |
|---|---|---|---|
| 16599709 Real Jaén – Real Zaragoza | J12 | 15-11-2026 16:00 | 14-11-2026 20:00 |
| 16599744 AD Alcorcón – Algeciras CF | J13 | 22-11-2026 16:00 | 22-11-2026 11:00 |
| 16599747 CDA Águilas FC – CE Europa | J13 | 22-11-2026 16:00 | 21-11-2026 17:45 |
| 16599751 Antequera – Juventud Torremolinos CF | J13 | 22-11-2026 16:00 | 21-11-2026 15:30 |
| 16599754 FC Cartagena – Real Jaén | J13 | 22-11-2026 16:00 | 22-11-2026 15:00 |
| 16599756 Huesca – UD Ibiza | J13 | 22-11-2026 16:00 | 22-11-2026 15:00 |
| 16599769 UE Sant Andreu – Atlético Madrileño | J13 | 22-11-2026 16:00 | 21-11-2026 20:00 |
| 16599774 Real Zaragoza – Rayo Majadahonda | J13 | 22-11-2026 16:00 | 21-11-2026 15:30 |
| 16599776 CDA Águilas FC – UE Sant Andreu | J38 | solo calendario, sin evento | 23-05-2027 15:00 |
| 16599788 Huesca – FC Cartagena | J38 | solo calendario, sin evento | 23-05-2027 15:00 |

Horas en UTC.

- Los partidos con evento siguen sirviendo su hora anterior: el listado se guarda como evidencia, pero no sustituye
  al evento.
- Los dos de J38 solo existían en el calendario. Su hora no cambia; el plan pedía su evento por no tenerlo.
- Se aplicarán con una actualización sin restricción.

### Comprobaciones posteriores

**Con todos los servicios detenidos:**
- integridad de la copia correcta;
- identificadores conservados (equipos, jugadores, partidos y observaciones);
- las dos sustituciones locales intactas: 16599919 → 16969545 y 16599956 → 17118209;
- 380 partidos vigentes;
- el bundle sellado, verificado de nuevo con la restricción exigida y los límites declarados.

**Aislamiento:**
- la base activa tiene la misma huella antes y después;
- los demás contenedores no cambian;
- ningún secreto en las salidas;
- volúmenes conservados.

**Dashboard de producción sobre una API de lectura de la copia** (1440 y 390 px):
- Partidos;
- resumen y alineaciones de Atlético Madrileño – Real Jaén;
- alineaciones de CE Europa – Real Zaragoza;
- Equipos (Real Jaén: 9 jugados, 4-4-1, 13-9);
- Inicio.

Sin desbordamientos ni errores.

**Pendiente:**
- revisión manual de los resultados frente a la fuente;
- aplicar los cambios fuera de alcance;
- el mantenimiento único de la base activa antes de usar este código sobre ella.
