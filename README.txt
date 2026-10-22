API LOCAL REAL JAEN — Python 3.10+, sin instalar dependencias.

NECESITA DATOS (no incluidos en el repositorio)
La API sirve una exportacion de SofaScore ya importada. Esos datos no se versionan
(.gitignore excluye datos/ y las exportaciones .jsonl): un clon NO arranca hasta
que se proporcionan. datos/ debe contener la salida del importador:
  equipos.json, jugadores.json, partidos.json, jugador_partido.json  (obligatorios)
  originales.json  (opcional: sin el, ninguna seccion de detalle figura almacenada)
  resumen.json     (lo genera el importador; la API no lo lee)
Para generarlos con el importador (proyecto aparte, sofascore_scraper/importador-real-jaen):
  python importar.py real-jaen-raw_AAAA-MM-DD_xxxx.jsonl --salida datos_normalizados
  y copia los JSON de datos_normalizados/ a datos/ de esta carpeta
  (o arranca con --datos apuntando a datos_normalizados).
El importador exige una carpeta de salida nueva y no modifica la exportacion.

ARRANCAR
  python api.py                       (lee datos/ y ambito.json de esta carpeta)
  python api.py --datos C:\ruta\datos_normalizados --ambito ambito.json --port 8001
Abre http://127.0.0.1:8001/health y http://127.0.0.1:8001/api/v1/scope

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
                  finished_without_valid_score, unknown_status},
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

  Disponibilidad:
  - stored: hay registro y tiene contenido.
  - empty: hay registro pero sin elementos de su categoria (payload vacio, o
    statistics/incidents con lista vacia, o lineups sin jugadores en ningun
    lado). data trae el payload tal cual.
  - not_stored: no hay ningun registro de esa seccion para el partido (normal
    en partidos pendientes). data es null. No es un error: responde 200.
  Si hay varias versiones de una seccion se sirve la de fetched_at mas
  reciente (a igual fecha, la ultima del archivo; una fecha ilegible cuenta
  como la mas antigua). fetched_at es la fecha en que se obtuvo el payload,
  no la fecha de actualizacion de su contenido.
  Los identificadores son los de SofaScore y el payload no se transforma:
  ningun valor ausente se convierte en cero.

REINICIAR EL SERVICIO
Los datos y el codigo se cargan al arrancar: tras cambiar api.py o los JSON,
detenlo con Ctrl+C en su terminal y vuelve a ejecutar: python api.py
Si no encuentras la terminal (Windows, PowerShell):
  Get-NetTCPConnection -LocalPort 8001 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess }
  python api.py
Comprueba: http://127.0.0.1:8001/health

PRUEBAS
Desde esta carpeta: python -m unittest discover -s tests -v
No necesitan que el servicio del puerto 8001 este en marcha: arrancan api.py en un
puerto libre para comprobar las respuestas HTTP.
- tests/test_api.py: muestra pequena y sintetica (tests/fixtures/datos, versionada;
  se regenera con python tests/fixtures/crear.py) con el ambito.json del proyecto.
  Funcionan en un clon sin datos/.
- tests/test_exportacion.py: la exportacion real de datos/ (recuentos incluidos). Sin
  datos/ se omiten. Usan los JSON sin modificarlos y construyen en una carpeta
  temporal los casos que no estan en la muestra (varias versiones, contenido vacio,
  sin originales, otros grupos o temporadas).
