"""API local de solo lectura. Python 3.10+. Sin dependencias."""
import argparse
import json
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
