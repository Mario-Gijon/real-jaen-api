"""API local de solo lectura. Python 3.10+. Sin dependencias."""
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# Secciones de detalle de partido: ruta -> clave `key` de originales.json.
SECCIONES = {'statistics': 'statistics', 'lineups': 'lineups', 'incidents': 'incidents'}
NOTA_FETCHED_AT = 'fetched_at es la fecha en que se obtuvo el payload de la fuente, no la fecha de actualización de su contenido.'
# Competiciones que sirve esta versión (ver ambito.json). Una competición se identifica por los tres ids.
AMBITO = Path(__file__).parent / 'ambito.json'
CAMPOS_AMBITO = ('unique_tournament_id', 'tournament_id', 'season_id')

def cargar(carpeta, ambito=AMBITO):
    datos = {n: json.loads((carpeta / (n+'.json')).read_text(encoding='utf-8')) for n in ('equipos','jugadores','partidos','jugador_partido')}
    # Los originales son opcionales: sin ellos, ninguna sección de detalle figura como almacenada.
    ruta = carpeta / 'originales.json'
    datos['originales'] = indexar(json.loads(ruta.read_text(encoding='utf-8'))) if ruta.exists() else {}
    return aplicar_ambito(datos, leer_ambito(ambito))

def leer_ambito(ruta):
    """Competiciones permitidas. Un ámbito ausente o incompleto impide arrancar: nunca se sirve todo por defecto."""
    competiciones = json.loads(Path(ruta).read_text(encoding='utf-8')).get('competiciones')
    if not isinstance(competiciones, list) or not competiciones:
        raise ValueError(f'{ruta}: se esperaba una lista "competiciones" no vacía')
    for c in competiciones:
        if not isinstance(c, dict) or not isinstance(c.get('nombre'), str) or any(type(c.get(k)) is not int for k in CAMPOS_AMBITO):
            raise ValueError(f'{ruta}: cada competición necesita "nombre" y los enteros {", ".join(CAMPOS_AMBITO)}')
    return competiciones

def competicion_original(originales, ident):
    """(unique_tournament_id, tournament_id, season_id) del registro `event` guardado, o None si no hay."""
    entrada = originales.get((ident, 'event'))
    if entrada is None:
        return None
    payload = entrada['registro'].get('payload')
    evento = payload.get('event', payload) if isinstance(payload, dict) else {}
    torneo = evento.get('tournament') or {}
    return ((torneo.get('uniqueTournament') or {}).get('id'), torneo.get('id'), (evento.get('season') or {}).get('id'))

def competicion_de(originales, partido, competiciones):
    """La competición permitida del partido por su grupo (tournament_id) y temporada, confirmada con el `event`
    original cuando existe (si el original dice otra cosa, se excluye). Nunca por nombre ni por la competición general."""
    c = next((c for c in competiciones if (c['tournament_id'], c['season_id']) == (partido.get('tournament_id'), partido.get('season_id'))), None)
    original = competicion_original(originales, partido['match_id'])
    if c is None or (original is not None and original != tuple(c[k] for k in CAMPOS_AMBITO)):
        return None
    return c

def ficha_de_alineaciones(originales, ident):
    """Ficha del jugador como la construye el importador, pero sólo desde estas alineaciones (la más reciente)."""
    ficha = None
    for (_, key), entrada in originales.items():
        if key != 'lineups':
            continue
        r = entrada['registro']
        for lado in ('home', 'away'):
            for e in ((r.get('payload') or {}).get(lado) or {}).get('players', []):
                p = e.get('player') or {}
                if p.get('id') != ident or (ficha and (r.get('fetched_at') or '') < (ficha['fetched_at'] or '')):
                    continue
                nacimiento = p.get('dateOfBirthTimestamp')
                ficha = {'source': 'Sofascore', 'player_id': ident, 'name': p.get('name'), 'usual_position': p.get('position'),
                         'country': (p.get('country') or {}).get('name'),
                         'birth_date': datetime.fromtimestamp(nacimiento, timezone.utc).isoformat()[:10] if nacimiento is not None else None,
                         'height_cm': p.get('height'), 'preferred_foot': p.get('preferredFoot'), 'fetched_at': r.get('fetched_at')}
    return ficha

def aplicar_ambito(datos, competiciones):
    """Los datos que sirve la API, limitados a las competiciones permitidas; todas las rutas parten de aquí:
    partidos, convocatorias, secciones de detalle y jugadores (sólo los observados en partidos incluidos)."""
    partidos = [m for m in datos['partidos'] if competicion_de(datos['originales'], m, competiciones)]
    incluidos = {m['match_id'] for m in partidos}
    convocatorias = [r for r in datos['jugador_partido'] if r['match_id'] in incluidos]
    originales = {clave: v for clave, v in datos['originales'].items() if clave[0] in incluidos}
    dentro = {r['player_id'] for r in convocatorias}
    fuera = {r['player_id'] for r in datos['jugador_partido'] if r['match_id'] not in incluidos}
    jugadores = []
    for p in datos['jugadores']:
        if p['player_id'] not in dentro:
            continue
        # La ficha guardada es la de la última alineación observada, quizá excluida: se rehace con las incluidas.
        if p['player_id'] in fuera:
            p = ficha_de_alineaciones(originales, p['player_id'])
            if p is None:
                continue
        jugadores.append(p)
    return dict(datos, partidos=partidos, jugador_partido=convocatorias, originales=originales, jugadores=jugadores,
                competiciones=competiciones, excluidos={m['match_id'] for m in datos['partidos']} - incluidos,
                jugadores_excluidos={p['player_id'] for p in datos['jugadores']} - {p['player_id'] for p in jugadores})

def instante(fetched_at):
    """fetched_at comparable; uno ilegible cuenta como el más antiguo."""
    try:
        return datetime.fromisoformat(fetched_at.replace('Z', '+00:00')).timestamp()
    except (AttributeError, TypeError, ValueError):
        return float('-inf')

def indexar(registros):
    """(event_id, key) -> {'registro': versión más reciente por fetched_at, 'versiones': n}. A igual fecha gana la última del archivo."""
    indice = {}
    for r in registros:
        clave = (r.get('event_id'), r.get('key'))
        actual = indice.get(clave)
        if actual is None:
            indice[clave] = {'registro': r, 'versiones': 1}
            continue
        actual['versiones'] += 1
        if instante(r.get('fetched_at')) >= instante(actual['registro'].get('fetched_at')):
            actual['registro'] = r
    return indice

def vacia(seccion, payload):
    """Contenido vacío: el registro existe pero no trae elementos de esa categoría."""
    if not payload:
        return True
    if not isinstance(payload, dict):
        return False
    if seccion == 'lineups':
        return not any(isinstance(payload.get(lado), dict) and payload[lado].get('players') for lado in ('home','away'))
    return not payload.get(seccion)

def disponibilidad(datos, ident, seccion):
    entrada = datos['originales'].get((ident, SECCIONES[seccion]))
    if entrada is None:
        return 'not_stored', None
    return ('empty' if vacia(seccion, entrada['registro'].get('payload')) else 'stored'), entrada

def detalle_partido(datos, ident, partes):
    partido = next((m for m in datos['partidos'] if m['match_id'] == ident), None)
    if partido is None:
        return 404, {'error':'Partido fuera del ámbito de esta versión' if ident in datos['excluidos'] else 'Partido no encontrado'}
    if len(partes) == 4:
        nombres = {e['team_id']: e['name'] for e in datos['equipos']}
        data = dict(partido, home_team_name=nombres.get(partido['home_team_id']), away_team_name=nombres.get(partido['away_team_id']))
        secciones = {s: disponibilidad(datos, ident, s)[0] for s in SECCIONES}
        return 200, {'data':data,'meta':{'sections':secciones}}
    seccion = partes[4]
    if seccion not in SECCIONES:
        return 404, {'error':'Ruta inexistente'}
    estado, entrada = disponibilidad(datos, ident, seccion)
    registro = entrada['registro'] if entrada else {}
    meta = {'match_id':ident,'section':seccion,'source':'Sofascore','source_key':SECCIONES[seccion],'availability':estado,
            'fetched_at':registro.get('fetched_at'),'versions':entrada['versiones'] if entrada else 0,'note':NOTA_FETCHED_AT}
    return 200, {'data':registro.get('payload'),'meta':meta}

PENDIENTES = ('notstarted', 'postponed')
NOTA_RENDIMIENTO = ('Resultados de los partidos finalizados observados en cada competición del ámbito; '
                    'no es necesariamente la temporada completa. Sin clasificación ni puntos.')

# Producción y concesión por partido: indicador -> (clave del periodo ALL de `statistics`, lado). `own` es el valor
# del lado del equipo; `opponent`, el del rival (lo que el equipo concede). Los goles salen del marcador.
INDICADORES = {
    'xg': ('expectedGoals', 'own'), 'xgAgainst': ('expectedGoals', 'opponent'),
    'shots': ('totalShotsOnGoal', 'own'), 'shotsAgainst': ('totalShotsOnGoal', 'opponent'),
    'shotsOnTarget': ('shotsOnGoal', 'own'), 'shotsOnTargetAgainst': ('shotsOnGoal', 'opponent'),
    'possession': ('ballPossession', 'own'), 'corners': ('cornerKicks', 'own'),
}
ORDEN_INDICADORES = ('goals', 'xg', 'shots', 'shotsOnTarget', 'goalsAgainst', 'xgAgainst', 'shotsAgainst',
                     'shotsOnTargetAgainst', 'possession', 'corners')

def _numero(valor):
    return type(valor) in (int, float) and math.isfinite(valor)

# Dominio de cada clave: fuera de él, el valor no existe (nunca se convierte en cero).
DOMINIOS = {
    'expectedGoals': lambda v: _numero(v) and v >= 0,
    'totalShotsOnGoal': lambda v: type(v) is int and v >= 0,
    'shotsOnGoal': lambda v: type(v) is int and v >= 0,
    'cornerKicks': lambda v: type(v) is int and v >= 0,
    'ballPossession': lambda v: _numero(v) and 0 <= v <= 100,
}

def estadisticas_all(originales, ident):
    """{clave: (local, visitante)} del periodo ALL de `statistics` (versión más reciente), sólo para las claves usadas.
    None si no hay registro, está vacío o no tiene un único periodo ALL: nada de eso se convierte en ceros. Una clave
    repetida (aparece en varios grupos) vale si todas sus apariciones coinciden; si no, se descarta."""
    entrada = originales.get((ident, 'statistics'))
    payload = entrada['registro'].get('payload') if entrada else None
    if not isinstance(payload, dict) or not isinstance(payload.get('statistics'), list):
        return None
    todos = [p for p in payload['statistics'] if isinstance(p, dict) and p.get('period') == 'ALL']
    if len(todos) != 1 or not isinstance(todos[0].get('groups'), list):
        return None
    valores, conflictos = {}, set()
    for grupo in todos[0]['groups']:
        for item in (grupo.get('statisticsItems') if isinstance(grupo, dict) else None) or []:
            if not isinstance(item, dict) or item.get('key') not in DOMINIOS:
                continue
            par = (item.get('homeValue'), item.get('awayValue'))
            if item['key'] in valores and valores[item['key']] != par:
                conflictos.add(item['key'])
            valores.setdefault(item['key'], par)
    return {k: v for k, v in valores.items() if k not in conflictos}

def valores_por_partido(originales, resultado):
    """Cada indicador en un partido contado, desde el lado del equipo; None si falta o no es válido."""
    stats = estadisticas_all(originales, resultado['match']['match_id'])
    valores = {'goals': resultado['goals_for'], 'goalsAgainst': resultado['goals_against']}
    for indicador, (clave, lado) in INDICADORES.items():
        par = stats.get(clave) if stats else None
        propio = 0 if resultado['side'] == 'home' else 1
        valor = par[propio if lado == 'own' else 1 - propio] if par else None
        valores[indicador] = valor if valor is not None and DOMINIOS[clave](valor) else None
    return valores

def medias(partidos):
    """Media por partido de cada indicador sobre los partidos dados, con cuántos tienen dato (`matches`) de cuántos
    (`of`). Sin ningún dato válido el valor es None, nunca 0. La posesión es la media aritmética de los porcentajes."""
    salida = {}
    for indicador in ORDEN_INDICADORES:
        validos = [p['valores'][indicador] for p in partidos if p['valores'][indicador] is not None]
        salida[indicador] = {'value': sum(validos) / len(validos) if validos else None,
                             'matches': len(validos), 'of': len(partidos)}
    return salida

def inicio(partido):
    """Instante de inicio, o None si falta o no se puede leer."""
    try:
        return datetime.fromisoformat(partido['kickoff_utc'].replace('Z', '+00:00')).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None

def orden_cronologico(partido):
    """Por instante real de inicio (nunca por jornada); a igual instante, por id. Sin fecha, al final."""
    momento = inicio(partido)
    return (momento is None, momento or 0, partido['match_id'])

def marcador_valido(valor):
    """Entero no negativo. bool es subclase de int en Python: True/False no son marcadores."""
    return type(valor) is int and valor >= 0

def rendimiento(datos, ident):
    """Balance del equipo en cada competición del ámbito, calculado sólo con sus partidos finalizados con marcador
    válido. Un finalizado sin marcador válido no cuenta (ni como 0-0) y se informa aparte; los pendientes tampoco."""
    competiciones = []
    for c in datos['competiciones']:
        partidos = sorted((m for m in datos['partidos'] if ident in (m['home_team_id'], m['away_team_id'])
                           and (m['tournament_id'], m['season_id']) == (c['tournament_id'], c['season_id'])),
                          key=orden_cronologico)
        cuenta = {'in_scope': len(partidos), 'finished': 0, 'pending': 0, 'counted': 0,
                  'finished_without_valid_score': 0, 'unknown_status': 0}
        balance = {'played': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'goals_for': 0, 'goals_against': 0}
        lados = {lado: {'wins': 0, 'draws': 0, 'losses': 0} for lado in ('home', 'away')}
        resultados = []
        for m in partidos:
            if m['status'] in PENDIENTES:
                cuenta['pending'] += 1
                continue
            if m['status'] != 'finished':
                cuenta['unknown_status'] += 1
                continue
            cuenta['finished'] += 1
            if not (marcador_valido(m.get('home_score')) and marcador_valido(m.get('away_score'))):
                cuenta['finished_without_valid_score'] += 1
                continue
            lado = 'home' if m['home_team_id'] == ident else 'away'
            favor, contra = (m['home_score'], m['away_score']) if lado == 'home' else (m['away_score'], m['home_score'])
            resultado = 'win' if favor > contra else 'draw' if favor == contra else 'loss'
            clave = {'win': 'wins', 'draw': 'draws', 'loss': 'losses'}[resultado]
            cuenta['counted'] += 1
            balance['played'] += 1
            balance[clave] += 1
            balance['goals_for'] += favor
            balance['goals_against'] += contra
            lados[lado][clave] += 1
            resultados.append({'match': m, 'side': lado, 'opponent_team_id': m['away_team_id'] if lado == 'home' else m['home_team_id'],
                               'goals_for': favor, 'goals_against': contra, 'result': resultado})
        balance['goal_difference'] = balance['goals_for'] - balance['goals_against']
        # Producción y concesión: los mismos partidos que el balance. «Últimos 3» son los tres contados más recientes
        # con fecha; la cobertura de cada media se calcula después, sin buscar partidos anteriores que la completen.
        elegibles = [{'match_id': r['match']['match_id'], 'valores': valores_por_partido(datos['originales'], r)}
                     for r in resultados]
        con_fecha = [e for e, r in zip(elegibles, resultados) if inicio(r['match']) is not None]
        ultimos = con_fecha[-3:]
        produccion = {'season': medias(elegibles),
                      'last3': {'match_ids': [e['match_id'] for e in ultimos], 'values': medias(ultimos)},
                      'undated': len(elegibles) - len(con_fecha)}
        competiciones.append(dict({'name': c['nombre'], 'season': c.get('temporada')}, **{k: c[k] for k in CAMPOS_AMBITO},
                                  matches=cuenta, record=balance, venues=lados, results=resultados, output=produccion))
    return {'team_id': ident, 'competitions': competiciones}

def consultar(datos, ruta):
    partes = ruta.strip('/').split('/')
    if ruta == '/health':
        return 200, {'data': {'status':'ok'}}
    if ruta == '/api/v1/scope':
        competiciones = [dict({'name': c['nombre'], 'season': c.get('temporada')}, **{k: c[k] for k in CAMPOS_AMBITO}) for c in datos['competiciones']]
        return 200, {'data': {'competitions': competiciones},
                     'meta': {'note': 'Sólo se sirven partidos, convocatorias y jugadores de estas competiciones.'}}
    if len(partes) not in (4,5) or partes[:2] != ['api','v1'] or partes[2] not in ('teams','players','matches'):
        return 404, {'error': 'Ruta inexistente'}
    try:
        ident = int(partes[3])
    except ValueError:
        return 400, {'error': 'Identificador inválido'}
    tipo = partes[2]
    if tipo == 'matches':
        return detalle_partido(datos, ident, partes)
    registros = datos['equipos' if tipo == 'teams' else 'jugadores']
    campo = 'team_id' if tipo == 'teams' else 'player_id'
    entidad = next((r for r in registros if r[campo] == ident), None)
    if entidad is None:
        fuera = tipo == 'players' and ident in datos['jugadores_excluidos']
        return 404, {'error':'Jugador fuera del ámbito de esta versión' if fuera else 'Entidad no encontrada'}
    if len(partes) == 4:
        return 200, {'data':entidad}
    sub = partes[4]
    if tipo == 'teams' and sub == 'performance':
        return 200, {'data': rendimiento(datos, ident), 'meta': {'note': NOTA_RENDIMIENTO}}
    if tipo == 'teams' and sub == 'matches':
        resultado = [m for m in datos['partidos'] if ident in (m['home_team_id'],m['away_team_id'])]
    elif tipo == 'teams' and sub == 'players':
        ids = {r['player_id'] for r in datos['jugador_partido'] if r['team_id']==ident}
        resultado = [{'player_id':p['player_id'],'name':p['name'],'usual_position':p['usual_position']} for p in datos['jugadores'] if p['player_id'] in ids]
        return 200, {'data':resultado,'meta':{'count':len(resultado),'scope':'Jugadores observados en convocatorias; no es una plantilla completa ni necesariamente actual.'}}
    elif tipo == 'players' and sub == 'matches':
        partidos = {m['match_id']:m for m in datos['partidos']}
        resultado = [dict(r,match=partidos[r['match_id']]) for r in datos['jugador_partido'] if r['player_id']==ident]
    else:
        return 404, {'error':'Ruta inexistente'}
    return 200, {'data':resultado,'meta':{'count':len(resultado)}}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--datos',type=Path,default=Path(__file__).parent/'datos')
    ap.add_argument('--port',type=int,default=8001)
    ap.add_argument('--ambito',type=Path,default=AMBITO)
    args=ap.parse_args()
    datos=cargar(args.datos,args.ambito)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url=urlsplit(self.path)
            if url.query:
                estado,respuesta=400,{'error':'Esta versión no admite filtros de consulta.'}
            else:
                estado,respuesta=consultar(datos,url.path)
            cuerpo=json.dumps(respuesta,ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(estado)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(cuerpo)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(cuerpo)
    print(f'API: http://127.0.0.1:{args.port} — Ctrl+C para detener',flush=True)
    servidor=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    try:servidor.serve_forever()
    except KeyboardInterrupt:pass
    finally:servidor.server_close()

if __name__=='__main__':main()
