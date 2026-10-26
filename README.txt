API LOCAL REAL JAEN — Python 3.10+, sin instalar dependencias.

NECESITA DATOS (no incluidos en el repositorio)
La API sirve exportaciones de SofaScore ya importadas. Esos datos no se versionan
(.gitignore excluye datos/ y las exportaciones .jsonl): un clon NO arranca hasta
que se proporcionan. La carpeta de datos contiene la salida del importador:
  equipos.json, jugadores.json, partidos.json, jugador_partido.json  (obligatorios)
  originales.json      (opcional: sin el, ninguna seccion de detalle figura almacenada)
  exportaciones.json   (manifiesto: procedencia y equipos con exportacion propia)
  discrepancias.json   (versiones que no coinciden, conservadas)
  resumen.json         (lo genera el importador; la API no lo lee)
Una importacion anterior (sin manifiesto) sigue cargando, pero no declara ningun
equipo con exportacion propia; si tiene secciones repetidas sin seleccion, no carga.

IMPORTADOR (importar.py, en esta carpeta)
Es la unica implementacion: sustituye a sofascore_scraper/importador-real-jaen/importar.py,
que queda como copia historica sin mantener (no se ha modificado). api.py usa sus funciones
de fechas y de datos maestros, asi que importador y API aplican las mismas reglas.
  python importar.py --exportacion 2841=real-jaen-raw_AAAA-MM-DD.jsonl \
                     --exportacion 24368=ud-ibiza-raw_AAAA-MM-DD.jsonl --salida datos_AAAA-MM-DD
  python importar.py exportacion.jsonl --salida otra   (sin equipo seguido declarado)
--exportacion EQUIPO=RUTA declara que esa exportacion es la propia del equipo EQUIPO (id de
SofaScore). Se pueden combinar varias, tambien la misma dos veces. La carpeta de salida debe
ser nueva: se escribe en <salida>.parcial y se renombra al terminar, asi que un fallo no
deja datos a medias ni toca los activos. Las entradas no se modifican; no accede a Internet.
Procedencia: originales.json conserva TODAS las lineas de entrada, cada una con export (E1,
E2... del manifiesto), line, content_hash (SHA256 del payload canonico), fetched_instant
(UTC) y fetched_at_valid; selected marca la version operativa de cada seccion. El
manifiesto (exportaciones.json) guarda fichero, SHA256, equipo seguido, recuentos, rango
de fetched_at y la fecha de importacion local (imported_at), distinta de la de obtencion.
Politica de versiones (importar.seleccionar; la API sirve la marcada y no aplica otra):
  - Contenido identico (misma huella) con otra fecha: no es una discrepancia.
  - fetched_at se compara como instante (con su desfase); una fecha invalida o sin
    desfase queda señalada (fetched_at_valid=false) y nunca gana a una valida.
  - event: una version final (finished) prevalece sobre las preliminares (notstarted,
    inprogress); un aplazado o cancelado anterior a la final no es conflicto. Es conflicto
    de integridad un estado no final obtenido despues de una final o un cambio de
    identidad (equipos, torneo, temporada): el partido lleva version_conflict y no entra
    en agregados (excluded_by_version_conflict). Marcadores finales distintos: se usa el
    final mas reciente y se informa (final_score_corrected); los totales pueden cambiar.
  - lineups: una alineacion confirmada prevalece sobre una preliminar.
  - Resto, y versiones comparables: la mas reciente es la operativa, sin afirmar que sea la
    correcta; la discrepancia queda en discrepancias.json con todas las versiones.
  - No se exige que estadisticas o incidencias se obtuvieran despues del final.
Datos maestros (jugadores.json): campo a campo, el valor conocido mas reciente de todas las
versiones de alineacion; una ausencia nunca borra un valor conocido; valores distintos se
registran (player_attribute_values_differ). La ficha no lleva equipo: equipo y dorsal son
relaciones por partido (jugador_partido).

CALENDARIO SIN PAYLOAD (--calendario RUTA)
Un partido que el scraper sólo conoce por el calendario de la jornada (sin payload raw: el raw no
lo exporta y el scraper no pide el `event` de un pendiente conocido así) entra desde su dataset
normalizado `events` (ssc export --dataset events --format jsonl --event ID ...):
  python importar.py --exportacion ... --calendario grupo2-j38-ausentes-normalizado.jsonl --salida ...
Cada fila se conserva en originales.json como registro `listing` (exportacion, linea, huella) y,
si el partido no tiene `event` raw, entra en partidos.json con calendar_source "listing", sin
secciones; tournament_id es el grupo (stage) y la temporada se nombra como la de los partidos raw
con el mismo season_id. Si tiene `event` raw, el raw manda y la diferencia queda en
discrepancias.json (listing_differs_from_event, sin conflicto de integridad). Un calendario no
declara equipo seguido. La API confirma el ambito con la fila `listing` cuando no hay `event`.
Motivo: el seguimiento de un equipo en el scraper lee una sola pagina de proximos partidos
(events/next/0, 30 partidos); con un partido de Copa por delante, el ultimo de liga queda fuera.
El parche local del scraper (abajo) corrige la paginacion para los seguimientos futuros, pero
no da payload raw a esos partidos: un pendiente conocido por el calendario sigue sin `event`
hasta que termine. Este complemento se conserva.

PARCHE LOCAL DEL SCRAPER (paginas de proximos partidos)
Repositorio: C:\Users\broke\dev\sofascore_scraper\sofascore_scraper, commit c697852 en la rama
local real-jaen/follow-next-pages, sobre dd9f061 (main, sin cambios: igual que el original). Para
sincronizar con la correccion, esa rama debe estar activa. Copia del parche fuera de Git:
  C:\Users\broke\dev\sofascore_scraper\importador-real-jaen\parches\scraper-next-paginas_2026-10-22.patch
Archivos: sofascore_scraper/services/follow_sync.py (list_follow lee next/0, next/1, ...),
services/sync.py (motivos del FailedListing), tests/test_fx19_follow_sync.py (11 casos nuevos),
docs/design/02-services.md y CHANGELOG.md. No toca frontend/package-lock.json (cambio ajeno previo).
Comportamiento: sigue hasNextPage hasta false, 404 o pagina vacia; un partido cuenta una vez;
como mucho MAX_NEXT_PAGES = 10 paginas. La lista es incompleta (se conservan los partidos ya
leidos, failed_listings y trabajo `partial`, nunca exito completo) si llega al limite con mas
paginas (page_limit), si una pagina anunciada no trae partidos nuevos (repeated_page), si una
pagina posterior falla (motivo de la peticion: 403, 429, 5xx...), si llega mal formada (parse)
o si tiene registros y ninguno se puede leer (unreadable_page). Solo hasNextPage false, 404 o
una pagina sin partidos son final normal. Paginas pasadas, ventana, presupuesto de peticiones,
pausas, cortacircuitos y politica de descarga (un pendiente solo con /event) no cambian.
Tras actualizar el repositorio original:
  0. main no lleva el parche: git switch main y git pull.
  1. Si upstream ya lo corrige, en tests/test_fx19_follow_sync.py o en follow_sync.py aparecera
     la lectura de next/1 y la rama se abandona; si no: git switch real-jaen/follow-next-pages y
     git rebase main (si no aplica, revisar list_follow a mano con el commit o el .patch como guia).
  2. Probar sin red: python -m pytest -q tests/test_fx19_follow_sync.py
     (las pruebas de varias paginas fallan si falta la correccion).
  3. Comprobacion real puntual, opcional: sincronizar un unico seguimiento de equipo con partido
     de Copa pendiente y ver en el registro del trabajo /team/{id}/events/next/1.
Limitaciones: el limite de 10 paginas es una salvaguarda sin medir con datos reales; una pagina
con parte de sus registros ilegibles aporta los legibles y no marca la lista como incompleta
(los ilegibles no se cuentan); un fallo de una pagina posterior a next/0 no lee las paginas
pasadas (igual que antes con next/0); los partidos
ya guardados como listing (los dos de J38) no reciben payload hasta terminar.

COBERTURA
GET /api/v1/scope publica teams_with_own_export: los equipos seguidos en el manifiesto,
con sus exportaciones. Una exportacion propia no acredita un calendario ni unas
estadisticas completas; los rivales que solo aparecen en partidos ajenos no figuran.

ARRANCAR
  python api.py                       (lee datos/ y ambito.json de esta carpeta)
  python api.py --datos C:\ruta\datos_normalizados --ambito ambito.json --port 8001
  python api.py ... --sustituciones correcciones\sustituciones.json   (decisiones locales, abajo)
Abre http://127.0.0.1:8001/health y http://127.0.0.1:8001/api/v1/scope
--almacen json (predeterminado) es todo lo anterior. Modo SQL, solo con la base de la persistencia
(entorno .venv con requirements-sql.txt; docs/persistencia-sqlite.md):
  .venv\Scripts\python api.py --almacen sql --base C:\ruta\real-jaen.sqlite --ambito ambito.json --port 8001
Lee solo esa base, en solo lectura, con su revision de esquema y sus sustituciones guardadas; no
admite --datos ni --sustituciones. Si la base falta, no es de este esquema o no se puede leer, la API
no arranca: nunca crea una base ni recurre a los JSON. Mismas rutas, contratos y respuestas.
La API SQL mantiene un bloqueo cooperativo <base>.lock hasta terminar. Cierre la API antes de importar,
reconstruir, migrar, incorporar sustituciones o restaurar: esas operaciones se rechazan mientras esté activa.
El sistema operativo libera el bloqueo ante una caída; no borre el archivo .lock ni WAL/SHM.
Coordina nuestras herramientas, no programas SQLite externos. Procedimiento y límites: docs/fase4-sincronizacion.md, 11.

SUSTITUCIONES LOCALES (--sustituciones RUTA)
Decisiones de esta instalacion, no datos de SofaScore: "el evento X queda sustituido por el Y"
(p. ej. un aplazado que se jugo con otro id; la fuente no enlaza ambos). Sin --sustituciones no se
aplica ninguna. Archivo: correcciones\sustituciones.json, aparte de las exportaciones y de la
importacion, que no se tocan. Cada entrada: sustituido, vigente (ids de evento), motivo y
procedencia {decidido_por, fecha AAAA-MM-DD, evidencia opcional}. Nunca se deducen por equipos,
jornada, slug ni customId: solo cuentan las entradas escritas.
Al arrancar se comprueba y, si algo falla, la API no arranca (nunca se aplica a medias): ids
enteros; motivo y procedencia; sin autorreferencias; un evento sustituido una sola vez; ningun
evento a la vez sustituido y vigente (sin cadenas ni ciclos); ambos eventos servidos (no
inexistentes ni fuera del ambito); misma competicion, temporada, local y visitante; el sustituido
no finalizado.
Efecto: el sustituido deja de ser encuentro vigente y pendiente (matches.in_scope son los
vigentes; matches.replaced_locally los sustituidos) y sale de /teams/{id}/matches
(meta.replaced_locally); sigue en /matches/{id} con replaced_by (decision "local", motivo,
procedencia, nota) y el vigente lleva replaces. /scope publica matches {events, current,
replaced_locally} y local_replacements. Originales, convocatorias, resultados y estadisticas no
cambian; las otras reglas de aplazamientos tampoco.
Revertir una decision: borrar su entrada del archivo (o arrancar sin --sustituciones) y
reiniciar la API. No hay que reimportar.

Rutas:
/health
/api/v1/teams/2841
/api/v1/teams/2841/players
/api/v1/teams/2841/matches
/api/v1/players/966303
/api/v1/players/966303/matches
/api/v1/matches/16599363
/api/v1/matches/16599363/statistics
/api/v1/matches/16599363/lineups
/api/v1/matches/16599363/incidents
/api/v1/scope
/api/v1/teams/2841/performance
/api/v1/teams/2841/player-stats

AMBITO (ambito.json)
Esta version solo sirve las competiciones de ambito.json. Hoy: Primera Federacion,
Grupo 2, temporada 26/27. Cada entrada es una competicion en una temporada:
  nombre, temporada, unique_tournament_id (competicion general, p. ej. 17073 =
  Primera Federacion, ambos grupos), tournament_id (el grupo, 94191 = Grupo 2) y
  season_id (97382 = 26/27).
Un partido se incluye si su tournament_id y season_id coinciden con una entrada y,
cuando hay registro `event` en originales.json, este confirma los tres ids. Nunca se
filtra por nombre ni solo por la competicion general (incluye los dos grupos). El
id del grupo puede cambiar cada temporada: cada temporada es una entrada nueva.
El ambito se aplica al cargar y todas las rutas parten de el: partidos del equipo,
partidos y ficha del jugador, jugadores observados (solo con convocatorias
incluidas) y detalle de partido con sus secciones. Un partido excluido responde
404 {"error": "Partido fuera del ambito de esta version"}, tambien en sus secciones;
un jugador observado solo fuera del ambito, 404 "Jugador fuera del ambito...". La
ficha de un jugador con convocatorias excluidas se rehace con sus alineaciones
incluidas: nunca sale de un partido excluido. Los JSON de datos/ no se modifican:
lo excluido sigue ahi para trazabilidad. Sin ambito.json valido la API no arranca.
Otro archivo: python api.py --ambito ruta\ambito.json
GET /api/v1/scope devuelve las competiciones permitidas con sus ids.

Los IDs pueden cambiarse por cualquier identificador incluido en los datos.
Con la exportacion del 2026-10-21 (38 partidos) el ambito sirve 37: 8 finalizados y
29 pendientes de Primera Federacion, Grupo 2; Copa del Rey queda fuera.
Los JSON se cargan al arrancar. Reinicia para leer una nueva importacion.
Solo lectura; no descarga ni refresca SofaScore. No modifica los JSON.
Escucha exclusivamente en 127.0.0.1, puerto 8001. Sin autenticacion: prototipo
local, no destinado a exponerse a Internet ni a despliegue de produccion.
El backend del dashboard podra consultar este servicio. No incluye CORS
para llamadas directas desde el navegador ni conexion al dashboard aun.
Los jugadores del equipo son los observados en las convocatorias de la muestra,
no una plantilla completa/actual. La ficha es la ultima observada en la muestra.
played=null indica minutos desconocidos. Los ausentes no se convierten en cero.
No admite filtros ni paginacion en esta primera version.

RENDIMIENTO DEL EQUIPO
GET /api/v1/teams/{teamId}/performance
  200 {"data": {"team_id", "competitions": [una por entrada de ambito.json:
        name, season, unique_tournament_id, tournament_id, season_id,
        matches: {in_scope, finished, pending, counted,
                  finished_without_valid_score, excluded_by_version_conflict, unknown_status},
        record: {played, wins, draws, losses, goals_for, goals_against,
                 goal_difference},
        venues: {home|away: {wins, draws, losses}},
        results: [{match, side, opponent_team_id, goals_for, goals_against,
                   result: win|draw|loss}]  (por fecha, del mas antiguo),
        output: {season: {indicador: {value, matches, of}},
                 last3: {match_ids, values: {indicador: {value, matches, of}}},
                 undated}]},
       "meta": {"note"}}
  404 si el equipo no esta en equipos.json.
Solo cuenta partidos del ambito con status finished y marcador valido: dos
enteros no negativos (True/False no lo son, aunque Python los trate como int).
Un finalizado sin marcador valido no cuenta (nunca como 0-0) y se informa en
finished_without_valid_score; notstarted y postponed son pending; cualquier otro
estado es unknown_status. Sin clasificacion ni puntos: son los partidos
observados, no necesariamente la temporada completa. Un equipo sin partidos en
el ambito responde 200 con ceros.
Orden: por instante real de kickoff_utc (no por jornada ni por el texto de la fecha);
a igual instante, por match_id; sin fecha, al final.
Produccion y concesion (output), sobre los mismos partidos contados que el balance:
  goals / goalsAgainst            marcador (goals_for / goals_against)
  xg / xgAgainst                  expectedGoals      (lado propio / rival)
  shots / shotsAgainst            totalShotsOnGoal   (tiros totales)
  shotsOnTarget / ...Against      shotsOnGoal        (tiros a puerta)
  possession                      ballPossession     (lado propio)
  corners                         cornerKicks        (lado propio)
Las claves salen del periodo ALL de statistics (homeValue/awayValue segun el lado del
equipo); nunca se suman 1ST y 2ND. Validas: xG numero finito >= 0; tiros, a puerta y
corners enteros >= 0; posesion numero finito entre 0 y 100; nunca booleanos. Una
clave repetida en varios grupos vale si coincide; si no, se descarta. Sin registro,
vacio o sin un unico periodo ALL: sin dato (nunca ceros).
Cada media = suma de los valores validos / numero de partidos con valor valido
(matches), de los partidos considerados (of); sin valores validos, value null. La
posesion es la media aritmetica de los porcentajes. Precision completa: se redondea
solo al presentar. last3: los tres partidos contados con fecha mas recientes; la
cobertura se calcula despues, sin buscar partidos anteriores. undated: contados sin
fecha (entran en season, no en last3).
Tras actualizar api.py hay que reiniciar el servicio (ver REINICIAR EL SERVICIO).

ACUMULADOS DE JUGADORES
GET /api/v1/teams/{teamId}/player-stats
  200 {"data": {"team_id", "competitions": [una por entrada de ambito.json:
        name, season, unique_tournament_id, tournament_id, season_id,
        matches: {counted, with_call_ups},
        call_ups: {total, confirmed, contradictions, duplicate_records, discarded_records},
        players: [{player_id, name, usual_position, height_cm, preferred_foot,
                   last_shirt_number, call_ups,
                   starts|appearances|minutes|goals|assists: {value, matches, of}}]]},
       "meta": {"note"}}
  404 si el equipo no esta en equipos.json.
Partidos: los mismos contados que el balance (finalizados del ambito con marcador valido),
por equipo (team_id) y competicion-temporada; with_call_ups, cuantos tienen alguna
convocatoria del equipo. Un registro cuyo side no es el del equipo en ese partido se
descarta (discarded_records). Cada jugador cuenta una vez por partido: sus registros
duplicados se unen y un campo que discrepa entre ellos queda sin dato.
Cada convocatoria se normaliza con las reglas de NORMALIZACION (abajo).
  of            convocatorias del jugador (call_ups)
  starts        substitute false explicito; nunca se deduce de los minutos
  appearances   value: jugadas; matches: con participacion conocida (jugo o no utilizado)
  minutes       los normalizados (un no utilizado suma 0)
  goals         los normalizados
  assists       los normalizados
value = suma de los validos; matches = convocatorias con dato; sin ninguno, value null
(nunca 0). Un valor con matches < of es parcial. call_ups: total, played, unused,
unknown, contradictions (de participacion), mismatches (goles o asistencias contra las
incidencias), team_warnings, duplicate_records y discarded_records (lado ilegible).
El equipo es el del lado (side) de la alineacion; un team_id o source_team_id distinto
es una advertencia (issues), no un descarte.

NORMALIZACION DE CONVOCATORIAS
El resto de estadisticas individuales va en normalized.stats: {metrica: {value, source, rule,
reason}}. source: reported (lo da la fuente, tambien un 0), derived (rule shots_partition: tiros a
puerta 0 cuando las categorias fuera/bloqueados explicitas suman totalShots), calculated (rule ratio:
precision de pase) o unused; sin valor, reason: not_reported, reported_null, invalid,
duplicate_disagree, version_conflict, inconsistent, participation_unknown o not_applicable. Una
ausencia nunca es 0 sin una relacion con valores explicitos; nunca se usa el total del equipo. Una
incoherencia o un conflicto de versiones afecta solo a su grupo (incidencias stat_*). Reglas,
evidencia e hipotesis: docs/normalizacion-estadisticas-individuales.md.
Las mismas reglas para acumulados, /players/{id}/matches (campo normalized de cada
fila) y /matches/{id}/lineups (normalized.players). Los registros originales no se
tocan: el normalizado va al lado, con el origen de cada cifra.
  participation  played | unused | unknown, con basis:
    played   minutes / played_flag (played true o minutos > 0), substitution_in
             (entrada en las incidencias), starting_lineup (titular de una alineacion
             confirmada)
    unused   unused_substitute: suplente sin minutos ni entrada, cuyas estadisticas son
             solo las de banquillo (totalShots, statisticsType), en una alineacion
             confirmada con incidencias cuyas entradas de ese lado coinciden exactamente
             con los suplentes con minutos (una seccion guardada no basta por si sola)
    unknown  contradiction (played true con 0 minutos, played false con minutos o con
             entrada/titularidad), stats_without_entry, events_without_entry,
             insufficient_coverage
  minutes   {value, source}: reported (minutes_played o statistics.minutesPlayed),
            unused (0) o sin dato. Nunca se derivan de los eventos.
  goals     statistics.goals; assists: statistics.goalAssist. source: reported,
            omitted_zero (clave omitida en una actuacion con minutos > 0 y al menos
            MIN_CLAVES_SUSTANCIALES claves individuales: regla de esta fuente, que omite
            los ceros; no es un 0 del payload), unused (0) o sin dato.
  Contraste: si las incidencias cuadran con el marcador (goles regular, penalty y
  ownGoal; ninguna otra clase), los goles de cada jugador (regular y penalty, nunca el
  autogol ni una tanda, que es otro incidentType) y sus asistencias (assist1) deben
  coincidir con goals/goalAssist; si no, esa cifra queda sin dato y se anota
  goals_mismatch / assists_mismatch en issues. Nunca se suman las dos fuentes.
  Versiones de alineacion: si dos versiones posteriores al partido (las que traen minutos)
  no coinciden para un jugador (suplente, minutos o estadisticas individuales), su
  participacion queda unknown (lineup_versions_disagree) aunque una sea mas reciente.
  issues: participation_contradiction, stats_without_entry, events_without_entry, lineup_versions_disagree,
          goals_mismatch, assists_mismatch, team_id_mismatch, source_team_mismatch.
  evidence: por cada incidencia de participacion o de goles/asistencias, el jugador, el
          partido, los campos en conflicto con sus valores originales (sin validar), las
          fuentes (jugador_partido, originales:lineups, originales:incidents), el
          fetched_at de cada registro y todas las versiones de alineacion e incidencias del
          partido (exportacion, linea, huella). Nada se resuelve a mano: queda para
          contrastarlo con otras fuentes.
  Discrepancia pendiente (exportacion del 21-10): Francisco Gallego (1982588) en J4,
  Real Jaen 2-1 UD Ibiza (16599916). Suplente sin minutos ni entrada registrada (el lado
  cuadra sus 5 cambios), pero sus estadisticas traen touches, totalPass, accuratePass,
  expectedAssists y pases en campo rival: stats_without_entry, participacion sin resolver.
  La regla del 0 no se aplica a xG, valoracion, otras estadisticas ni datos personales.
height_cm (entero > 0) y preferred_foot (texto) salen de la ficha; last_shirt_number es
el dorsal de la ultima convocatoria con fecha: historico, no necesariamente el actual.

DETALLE DE PARTIDO
Fuente: datos/partidos.json (partido normalizado), datos/equipos.json (nombres)
y datos/originales.json (payloads originales de SofaScore). Cada registro de
originales.json tiene event_id (id de partido de SofaScore), key (categoria:
event, statistics, lineups, incidents, h2h, team_streaks, pregame_form), sub
(vacio en la muestra), fetched_at y payload. originales.json es opcional: sin
el, la API arranca y ninguna seccion figura como almacenada.

GET /api/v1/matches/{matchId}
  200 {"data": partido normalizado (mismos campos que en /teams/{id}/matches)
        + "home_team_name", "away_team_name" (null si el equipo no esta en
        equipos.json),
       "meta": {"sections": {"statistics": D, "lineups": D, "incidents": D}}}
  D es la disponibilidad de cada seccion (ver abajo). El resultado es el de
  partidos.json; nunca se recalcula a partir de las incidencias. Un partido
  pendiente tiene home_score/away_score null.
  404 {"error": "Partido no encontrado"} si el partido no esta en partidos.json.

GET /api/v1/matches/{matchId}/statistics | /lineups | /incidents
  200 {"data": payload original de esa categoria (key) tal como se guardo, o null,
       "meta": {"match_id", "section", "source": "Sofascore",
                "source_key": clave de originales.json,
                "availability": "stored" | "empty" | "not_stored",
                "fetched_at": fecha de obtencion del payload o null,
                "versions": registros guardados de esa seccion (0 si ninguno),
                "note": aviso sobre fetched_at}}
  404 {"error": "Partido no encontrado"} si el partido no existe;
      {"error": "Ruta inexistente"} para otra seccion.
  400 {"error": "Identificador invalido"} si matchId no es un entero.

  meta.versions cuenta todas las versiones guardadas de la seccion; meta.discrepancy
  resume la del importador ({reasons, integrity}) o es null.
  Disponibilidad:
  - stored: hay registro y tiene contenido.
  - empty: hay registro pero sin elementos de su categoria (payload vacio, o
    statistics/incidents con lista vacia, o lineups sin jugadores en ningun
    lado). data trae el payload tal cual.
  - not_stored: no hay ningun registro de esa seccion para el partido (normal
    en partidos pendientes). data es null. No es un error: responde 200.
  Si hay varias versiones de una seccion se sirve la que eligio el importador
  (selected; ver IMPORTADOR). fetched_at es la fecha en que se obtuvo el
  payload, no la fecha de actualizacion de su contenido.
  Los identificadores son los de SofaScore y el payload no se transforma:
  ningun valor ausente se convierte en cero.

REINICIAR EL SERVICIO
Los datos y el codigo se cargan al arrancar: tras cambiar api.py o los JSON,
detenlo con Ctrl+C en su terminal y vuelve a ejecutar: python api.py
Si no encuentras la terminal (Windows, PowerShell):
  Get-NetTCPConnection -LocalPort 8001 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess }
  python api.py
Comprueba: http://127.0.0.1:8001/health

PERSISTENCIA FUTURA
Los JSON son una etapa del prototipo. Lo que este formato ya prepara para una base de datos:
  - Datos maestros (jugadores.json: identidad, nacimiento, estatura, pie, pais) separados de
    las relaciones por partido (jugador_partido: equipo, dorsal, lado) y de los datos
    deportivos (partidos, originales). jugador-equipo-temporada y dorsales se derivan de
    jugador_partido; hoy no hay tabla propia.
  - Claves naturales de SofaScore (event_id, key, sub; match_id; player_id; team_id) para
    reimportar sin duplicar; content_hash para versiones; export/line para procedencia.
  - Una exportacion parcial no borra nada: cada importacion se hace desde todas las
    exportaciones conservadas. Para actualizar se añade la nueva exportacion y se reimporta
    a una carpeta nueva; los datos activos siguen disponibles si falla.
  - fetched_at (obtencion) e imported_at (actualizacion local) son distintos.
Decisiones que afectaran a la persistencia: la reimportacion completa (no incremental), la
seleccion por seccion y el formato de discrepancias.json; la API carga todo en memoria.
Diseno propuesto (SQLite, SQLAlchemy, Alembic; sin implementar): docs/persistencia-sqlite.md.

PRUEBAS
Desde esta carpeta: python -m unittest discover -s tests -v
No necesitan que el servicio del puerto 8001 este en marcha: arrancan api.py en un
puerto libre para comprobar las respuestas HTTP.
Persistencia SQL (docs/persistencia-sqlite.md): entorno propio
  py -3.14 -m venv .venv  y  .venv\Scripts\python -m pip install -r requirements-sql.txt
  .venv\Scripts\python -m unittest discover -s tests -v
Sin esas dependencias, tests/test_persistencia.py, test_carga.py, test_lectura_sql.py y
test_sincronizacion.py se omiten y el resto funciona igual. Base fuera de los repositorios:
  .venv\Scripts\python -m persistencia cargar --importacion CARPETA --base RUTA.sqlite
  .venv\Scripts\python -m persistencia sustituciones --base RUTA.sqlite --archivo SUSTITUCIONES.json --ambito ambito.json
  .venv\Scripts\python -m persistencia importar --base RUTA.sqlite --exportacion EQUIPO=RUTA [--calendario RUTA] [--fichas-jugadores RUTA]
  .venv\Scripts\python -m persistencia verificar --base RUTA.sqlite
  .venv\Scripts\python -m persistencia reconstruir --base RUTA.sqlite [--confirmar]
  .venv\Scripts\python -m persistencia migrar --base RUTA.sqlite
  .venv\Scripts\python -m persistencia copiar --base RUTA.sqlite
  .venv\Scripts\python -m persistencia restaurar --copia COPIA.sqlite --base RUTA.sqlite --confirmar
Cada escritura en una base existente hace antes una copia verificada (en <carpeta de la base>\copias)
y la base se verifica antes de confirmar.
La API lee la base al arrancar: cierrala antes de importar, reconstruir, migrar o restaurar y arrancala despues.
Mientras una API SQL nueva tenga la base reservada, los comandos se rechazan (docs/fase4-sincronizacion.md, 11).
Importacion incremental y vigencia de convocatorias: docs/fase4-sincronizacion.md.
La carga solo admite carpetas que las reglas actuales del importador reproducen: una carpeta con
duplicados legados en jugador_partido (que la API en modo JSON si lee y une) se rechaza.
- tests/test_api.py: muestra pequena y sintetica (tests/fixtures/datos, versionada;
  se regenera con python tests/fixtures/crear.py) con el ambito.json del proyecto.
  Funcionan en un clon sin datos/.
- tests/test_importar.py: el importador con exportaciones sinteticas pequeñas (repeticion,
  solapamiento, contenido identico y discrepante, desfases y fechas invalidas, estados,
  alineaciones, jugador en varios equipos, datos maestros, cobertura) y como las sirve api.py.
- tests/test_exportacion.py: la exportacion real de datos/ (recuentos incluidos). Sin
  datos/ se omiten. Usan los JSON sin modificarlos y construyen en una carpeta
  temporal los casos que no estan en la muestra (varias versiones, contenido vacio,
  sin originales, otros grupos o temporadas).

FICHAS INDIVIDUALES COMPLEMENTARIAS (opcional; no descarga desde el importador)
  python importar.py --exportacion EQUIPO=raw.jsonl --calendario calendario.jsonl \
                    --fichas-jugadores fichas.jsonl --salida carpeta_nueva
--fichas-jugadores RUTA es repetible. Cada linea JSONL conserva el sobre:
  {"player_id":1545211,"endpoint":"/player/1545211",
   "fetched_at":"2026-10-23T12:44:55+00:00","payload":{"player":{"id":1545211,"preferredFoot":"Left"}}}
payload es la respuesta ORIGINAL completa; el ejemplo anterior esta abreviado.
Se exige identidad entera positiva coincidente en sobre, endpoint y payload.player.id,
fecha ISO con zona horaria y tipos validos de los atributos personales admitidos.
Endpoint es la ruta relativa consultada por el transporte del scraper.
No se normalizan ni inventan valores de preferredFoot: la API del dashboard ya distingue
Left/Right/Both (sin distinguir mayusculas) y muestra como desconocidos otros valores.

La salida nueva guarda fichas_originales.json con TODAS las versiones, exportacion,
linea, SHA256 canonico y fecha de obtencion; exportaciones.json incluye los ficheros
complementarios, su SHA256 y rango de fechas. No se mezclan con originales de partidos.
Un mismo fichero repetido conserva sus referencias de procedencia, sin duplicar jugadores,
partidos ni convocatorias. No es una duplicacion deportiva.
Solo se enriquecen jugadores ya observados en alineaciones y los campos name,
usual_position, country, birth_date, height_cm y preferred_foot. Equipo, dorsal y
estadisticas de la ficha nunca sustituyen relaciones de partido. Una ficha de un jugador
no observado se conserva como evidencia sin crear pertenencia ni convocatorias.
Todas las versiones participan en ficha_combinada: ultimo valor conocido por instante,
ausencia/null no borra conocido y valores explicitos distintos generan discrepancia
player_attribute_values_differ con valores y referencias de las versiones de origen.
Al aplicar el ambito, la API conserva los complementos personales al reconstruir fichas
desde alineaciones incluidas; nunca acredita pertenencia al equipo con la ficha personal.

Tambien se admiten, en el mismo fichero o mezclados, los registros Slice que escribe
el scraper con `ssc export --dataset player_profiles` (uno por jugador, con su payload):
  {"owner_kind":"player","owner_id":1545211,"key":"player_profile","sub":null,
   "state":"ok","has_payload":true,"fetched_at_utc":"2026-10-23T14:04:17Z",...,"payload":{...}}
Se exige owner_kind player, key player_profile sin sub y owner_id entero positivo; el
registro se convierte al sobre anterior (player_id=owner_id, endpoint /player/ID,
fetched_at=fetched_at_utc) y se valida igual. Un registro sin ficha (404 o error sin
version previa: has_payload false y payload null) no aporta datos: se cuenta en
without_profile de exportaciones.json. payload sin has_payload es un error.

Obtencion con el scraper (flujo normal; el importador no descarga):
1. ssc --rate 1 fetch player --ids-file ids.txt   (una id por linea; # comenta)
   Con SOFASCORE_CLIENT__MAX_CONCURRENT=1 para ir de una en una. Usa la sesion, los
   limites, los reintentos, el puente de navegador y el cortacircuitos del scraper.
   No crea seguimientos ni descarga partidos. --dry-run cuenta lo pendiente sin pedir.
2. Las fichas validas guardadas no se vuelven a pedir: reanudar es repetir el comando.
   Un error se reintenta en la siguiente ejecucion; un 404 queda registrado con fecha,
   se pregunta otra vez una vez y despues solo con --refresh.
3. --refresh pide de nuevo cada ficha dada; una ficha distinta se anade al historial
   sin perder la anterior.
4. ssc export --dataset player_profiles [--player ID ...] --out fichas.jsonl
   Reimportar TODOS los raw, calendarios y complementos conservados a una carpeta nueva.
   Revisar discrepancias antes de cambiar servicios. Arrancar con --ambito y
   --sustituciones explicitos: importar no activa datos ni aplica decisiones locales.

Pruebas: tests/test_fichas.py cubre identidad, estructura, zonas horarias, nulos,
conflictos de valores, reimportacion, aislamiento de relaciones/deporte, reconstruccion
por ambito y registros Slice del scraper (propietario, clave, identidad, sin ficha).
Todas las pruebas de la API se ejecutan con unittest discover -s tests.


DOCKER LOCAL (sin activar servicios existentes)
Imagen de producción local: Dockerfile, Python 3.14.4 fijado y dependencias SQL. Compose vive en
el repositorio hermano rj-analyst/compose.yaml; procedimiento y límites en docs/docker.md y
rj-analyst/docs/docker.md. api.py admite --host (por defecto 127.0.0.1); la imagen pide 0.0.0.0
solo dentro de su red, sin publicar backend al host. Datos y copias viven en un volumen externo,
no en la imagen. No hay migraciones automáticas, recarga en caliente ni botón Actualizar.

COORDINADOR INTERNO (fixtures, sin activar): diseno y limites en docs/coordinador-fixtures.md.
No hay endpoints nuevos ni cambios en el arranque habitual; no ejecuta red ni scraper.
