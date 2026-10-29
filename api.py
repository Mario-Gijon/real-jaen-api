"""API local de solo lectura. Python 3.10+. Sin dependencias."""
import argparse
import json
import math
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# Las reglas de versiones y de datos maestros son las del importador: una sola implementación.
from importar import ficha_combinada, ficha_de_entrada, rasgos_evento, validar_ficha

# Secciones de detalle de partido: ruta -> clave `key` de originales.json.
SECCIONES = {'statistics': 'statistics', 'lineups': 'lineups', 'incidents': 'incidents'}
NOTA_FETCHED_AT = 'fetched_at es la fecha en que se obtuvo el payload de la fuente, no la fecha de actualización de su contenido.'
# Competiciones que sirve esta versión (ver ambito.json). Una competición se identifica por los tres ids.
AMBITO = Path(__file__).parent / 'ambito.json'
CAMPOS_AMBITO = ('unique_tournament_id', 'tournament_id', 'season_id')
NOTA_SUSTITUCION = ('Decisión local de esta instalación (archivo de sustituciones), no un dato de SofaScore: '
                    'la fuente no publica el vínculo entre ambos eventos. Los dos se conservan íntegros.')

def cargar(carpeta, ambito=AMBITO, sustituciones=None):
    datos = {n: json.loads((carpeta / (n+'.json')).read_text(encoding='utf-8')) for n in ('equipos','jugadores','partidos','jugador_partido')}
    # Los originales son opcionales: sin ellos, ninguna sección de detalle figura como almacenada.
    ruta = carpeta / 'originales.json'
    datos['originales'] = indexar(json.loads(ruta.read_text(encoding='utf-8'))) if ruta.exists() else {}
    # Manifiesto y discrepancias del importador (formato 2); una importación anterior no los tiene.
    opcional = lambda nombre, defecto: json.loads((carpeta / nombre).read_text(encoding='utf-8')) if (carpeta / nombre).exists() else defecto
    datos['exportaciones'] = opcional('exportaciones.json', None)
    datos['discrepancias'] = opcional('discrepancias.json', [])
    datos['fichas_originales'] = opcional('fichas_originales.json', [])
    # Sin archivo de sustituciones, ninguna: los datos se sirven como los dejó el importador.
    return componer(datos, leer_ambito(ambito), leer_sustituciones(sustituciones) if sustituciones else [])

def componer(datos, competiciones, sustituciones):
    """Los datos que sirve la API a partir de lo almacenado (carpeta JSON o base SQL, con los mismos campos y los
    originales ya indexados): fichas validadas, ámbito y sustituciones locales. Un solo camino para ambos modos."""
    for r in datos['fichas_originales']:
        validar_ficha(r)
    datos = apartar_alineaciones_de_otros_equipos(aplicar_ambito(datos, competiciones))
    return aplicar_sustituciones(datos, sustituciones)

def apartar_alineaciones_de_otros_equipos(datos):
    """Las convocatorias de una alineación leída antes de un cambio de equipos del evento. El importador las asigna al
    equipo del lado en el evento vigente, pero son de los equipos anteriores: se apartan (`convocatorias_apartadas`)
    y no se atribuyen a ningún equipo ni jugador del partido vigente. La alineación original se conserva como
    evidencia en su sección (`meta.predates_team_change`)."""
    cambiados = {d['event_id'] for d in datos['discrepancias']
                 if d.get('key') == 'event' and 'identity_changed' in (d.get('reasons') or [])}
    apartados = {m for m in cambiados if 'teams' in anterior_a(datos, m, 'lineups')}
    if not apartados:
        return datos
    return dict(datos, jugador_partido=[r for r in datos['jugador_partido'] if r['match_id'] not in apartados],
                convocatorias_apartadas=[r for r in datos['jugador_partido'] if r['match_id'] in apartados])

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
    """(unique_tournament_id, tournament_id, season_id) del registro `event` guardado o, si no lo hay, de la fila
    `listing` de un calendario normalizado (tournament_id ahí es la competición general y stage el grupo); None si
    no hay ninguno."""
    entrada = originales.get((ident, 'event'))
    if entrada is None:
        listado = (originales.get((ident, 'listing')) or {}).get('registro', {}).get('payload')
        if isinstance(listado, dict):
            return (listado.get('tournament_id'), (listado.get('stage') or {}).get('id'), listado.get('season_id'))
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

def ficha_de_alineaciones(originales, ident, fichas=()):
    """Ficha del jugador como la construye el importador (`ficha_combinada`: campo a campo, el valor conocido más
    reciente), pero sólo desde las versiones de estas alineaciones."""
    observaciones = []
    for (_, key), entrada in originales.items():
        if key != 'lineups':
            continue
        for r in entrada['todas']:
            for lado in ('home', 'away'):
                for e in ((r.get('payload') or {}).get(lado) or {}).get('players', []):
                    if (e.get('player') or {}).get('id') == ident:
                        observaciones.append((r.get('fetched_at'), ficha_de_entrada(e, r.get('fetched_at'))))
    # Las fichas personales no dependen del equipo/competición, pero no acreditan pertenencia.
    observaciones.extend((r['fetched_at'], ficha_de_entrada(r['payload'], r['fetched_at']))
                         for r in fichas if r['player_id'] == ident)
    return ficha_combinada(observaciones)[0] if observaciones else None

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
            p = ficha_de_alineaciones(originales, p['player_id'], datos.get('fichas_originales', []))
            if p is None:
                continue
        jugadores.append(p)
    return dict(datos, partidos=partidos, jugador_partido=convocatorias, originales=originales, jugadores=jugadores,
                competiciones=competiciones, excluidos={m['match_id'] for m in datos['partidos']} - incluidos,
                jugadores_excluidos={p['player_id'] for p in datos['jugadores']} - {p['player_id'] for p in jugadores})

def leer_sustituciones(ruta):
    """Decisiones locales «este evento queda sustituido por este otro» (ver README.txt, SUSTITUCIONES LOCALES).
    Comprueba el archivo por sí mismo: campos, autorreferencias, eventos sustituidos dos veces y cadenas o ciclos
    (un evento vigente no puede estar sustituido). Cualquier error impide arrancar: nunca se aplica a medias."""
    contenido = json.loads(Path(ruta).read_text(encoding='utf-8'))
    lista = contenido.get('sustituciones') if isinstance(contenido, dict) else None
    if not isinstance(lista, list):
        raise ValueError(f'{ruta}: se esperaba una lista "sustituciones"')
    texto = lambda v: isinstance(v, str) and v.strip()
    for i, s in enumerate(lista):
        donde = f'{ruta}, sustituciones[{i}]'
        if not isinstance(s, dict) or any(type(s.get(k)) is not int or s[k] <= 0 for k in ('sustituido', 'vigente')):
            raise ValueError(f'{donde}: "sustituido" y "vigente" deben ser identificadores enteros de evento')
        procedencia = s.get('procedencia')
        if not texto(s.get('motivo')) or not isinstance(procedencia, dict) or not texto(procedencia.get('decidido_por')):
            raise ValueError(f'{donde}: faltan "motivo" o "procedencia.decidido_por"')
        try:
            date.fromisoformat(procedencia.get('fecha'))
        except (TypeError, ValueError):
            raise ValueError(f'{donde}: "procedencia.fecha" debe ser una fecha AAAA-MM-DD') from None
        if s['sustituido'] == s['vigente']:
            raise ValueError(f'{donde}: un evento no puede sustituirse a sí mismo ({s["sustituido"]})')
    sustituidos = [s['sustituido'] for s in lista]
    repetidos = sorted({e for e in sustituidos if sustituidos.count(e) > 1})
    if repetidos:
        raise ValueError(f'{ruta}: eventos sustituidos más de una vez (asignaciones contradictorias): {repetidos}')
    encadenados = sorted(set(sustituidos) & {s['vigente'] for s in lista})
    if encadenados:
        raise ValueError(f'{ruta}: eventos que son a la vez sustituidos y vigentes (cadena o ciclo): {encadenados}')
    return lista

def aplicar_sustituciones(datos, sustituciones):
    """Marca cada evento sustituido (`replaced_by`) y su vigente (`replaces`). Ambos deben ser partidos servidos de la
    misma competición, con el mismo local y el mismo visitante, y el sustituido no puede estar finalizado (sus
    resultados nunca se retiran así). No se borra nada: cambia sólo qué evento es el encuentro vigente."""
    if not sustituciones:
        return dict(datos, sustituciones=[])
    partidos = {m['match_id']: m for m in datos['partidos']}
    for s in sustituciones:
        for papel in ('sustituido', 'vigente'):
            if s[papel] not in partidos:
                fuera = 'está fuera del ámbito servido' if s[papel] in datos['excluidos'] else 'no existe en la importación'
                raise ValueError(f'sustitución {s["sustituido"]} -> {s["vigente"]}: el evento {papel} {s[papel]} {fuera}')
        antiguo, vigente = partidos[s['sustituido']], partidos[s['vigente']]
        for campo, nombre in (('tournament_id', 'competición'), ('season_id', 'temporada'),
                              ('home_team_id', 'equipo local'), ('away_team_id', 'equipo visitante')):
            if antiguo[campo] != vigente[campo]:
                raise ValueError(f'sustitución {s["sustituido"]} -> {s["vigente"]}: no coincide {nombre} '
                                 f'({antiguo[campo]} frente a {vigente[campo]})')
        if antiguo['status'] == 'finished':
            raise ValueError(f'sustitución {s["sustituido"]} -> {s["vigente"]}: el evento sustituido está finalizado')
    decision = lambda s, otro: {'match_id': otro, 'decision': 'local', 'reason': s['motivo'],
                                'decided_by': s['procedencia']['decidido_por'], 'decided_on': s['procedencia']['fecha'],
                                'evidence': s['procedencia'].get('evidencia'), 'note': NOTA_SUSTITUCION}
    marcados = {}
    for s in sustituciones:
        marcados[s['sustituido']] = dict(partidos[s['sustituido']], replaced_by=decision(s, s['vigente']))
    for s in sustituciones:
        vigente = marcados.get(s['vigente']) or dict(partidos[s['vigente']], replaces=[])
        vigente['replaces'] = vigente['replaces'] + [decision(s, s['sustituido'])]
        marcados[s['vigente']] = vigente
    return dict(datos, partidos=[marcados.get(m['match_id'], m) for m in datos['partidos']], sustituciones=sustituciones)

def vigente(partido):
    """Un evento es un encuentro vigente salvo que una decisión local lo dé por sustituido."""
    return 'replaced_by' not in partido

def indexar(registros):
    """(event_id, key) -> {'registro': la versión operativa, 'versiones': n, 'todas': [versiones]}. La elige el
    importador (`selected`, ver importar.seleccionar); la API no aplica otra política. Una importación sin marcas
    (formato 1) sólo se admite si cada sección tiene una única versión."""
    grupos = {}
    for r in registros:
        grupos.setdefault((r.get('event_id'), r.get('key')), []).append(r)
    indice = {}
    for clave, versiones in grupos.items():
        if any('selected' in v for v in versiones):
            marcadas = [v for v in versiones if v.get('selected') is True]
            if len(marcadas) != 1:
                raise ValueError(f'originales.json: {clave} necesita exactamente una versión seleccionada por el importador')
            elegida = marcadas[0]
        elif len(versiones) == 1:
            elegida = versiones[0]
        else:
            raise ValueError(f'originales.json: {clave} tiene {len(versiones)} versiones sin selección; impórtalo con importar.py')
        indice[clave] = {'registro': elegida, 'versiones': len(versiones), 'todas': versiones}
    return indice

def version(r):
    """Procedencia de una versión de una sección (sin su contenido)."""
    return {'export': r.get('export'), 'line': r.get('line'), 'content_hash': r.get('content_hash'),
            'fetched_at': r.get('fetched_at'), 'selected': r.get('selected', True)}

def discrepancia_de(datos, ident, key):
    """La discrepancia que registró el importador para esa sección del partido, resumida; None si no hay."""
    d = next((d for d in datos['discrepancias'] if d.get('event_id') == ident and d.get('key') == key), None)
    return {'reasons': d['reasons'], 'integrity': d['integrity']} if d else None

def instante(texto):
    """Un instante ISO-8601 en segundos, o None si no se puede leer."""
    try:
        return datetime.fromisoformat(texto.replace('Z', '+00:00')).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None

# Correcciones del evento que dejan atrás las secciones leídas antes: los campos que cambian en cada una.
CORRECCIONES = {'score': ('status', 'home_score', 'away_score'), 'teams': ('home_team_id', 'away_team_id')}

def correcciones_del_evento(datos, ident):
    """Las correcciones del evento vigente, {'score' | 'teams': (fetched_at, instante)}, con el momento en que se
    observó por primera vez lo vigente: un marcador final que corrige a otro (discrepancia `final_score_corrected`) o
    unos equipos distintos de los de otra versión (`identity_changed`). Sin una fecha legible, esa corrección no se
    afirma."""
    d = discrepancia_de(datos, ident, 'event')
    evento = datos['originales'].get((ident, 'event'))
    if not d or not evento:
        return {}
    def rasgos(r, campos):
        valores = rasgos_evento(r.get('payload'))
        return tuple(valores[c] for c in campos)
    motivos = {'score': 'final_score_corrected', 'teams': 'identity_changed'}
    correcciones = {}
    for tipo, campos in CORRECCIONES.items():
        if motivos[tipo] not in d['reasons'] or len({rasgos(r, campos) for r in evento['todas']}) < 2:
            continue
        vigente = rasgos(evento['registro'], campos)
        vistas = [(instante(r.get('fetched_at')), r.get('fetched_at')) for r in evento['todas'] if rasgos(r, campos) == vigente]
        if vistas and all(i is not None for i, _ in vistas):
            momento, texto = min(vistas)
            correcciones[tipo] = (texto, momento)
    return correcciones

def anterior_a(datos, ident, key):
    """Las correcciones del evento ('score', 'teams') posteriores a la lectura de la sección elegida: sus datos son de
    antes (por ejemplo, porque su relectura falló y se conservó la anterior). Vacío si no hay correcciones o si la
    fecha de la sección no se lee: no se afirma lo que no se sabe."""
    correcciones = correcciones_del_evento(datos, ident)
    seccion = datos['originales'].get((ident, key))
    if not correcciones or seccion is None:
        return set()
    leida = instante(seccion['registro'].get('fetched_at'))
    return set() if leida is None else {tipo for tipo, (_, momento) in correcciones.items() if leida < momento}

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
    anteriores = anterior_a(datos, ident, SECCIONES[seccion])
    meta = {'match_id':ident,'section':seccion,'source':'Sofascore','source_key':SECCIONES[seccion],'availability':estado,
            'fetched_at':registro.get('fetched_at'),'versions':entrada['versiones'] if entrada else 0,
            'discrepancy':discrepancia_de(datos, ident, SECCIONES[seccion]),
            # Leída antes del marcador corregido o de los equipos vigentes: se sirve como evidencia, no se ha actualizado.
            'predates_score_correction':'score' in anteriores,'predates_team_change':'teams' in anteriores,
            'note':NOTA_FETCHED_AT}
    respuesta = {'data':registro.get('payload'),'meta':meta}
    if seccion == 'lineups':
        # La participación de cada convocado según las reglas de esta fuente; el payload original no se toca.
        respuesta['normalized'] = {'players': list(actuaciones_del_partido(datos, ident).values())}
    return 200, respuesta

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

def entero_no_negativo(valor):
    """bool es subclase de int en Python: True/False no son enteros aquí."""
    return type(valor) is int and valor >= 0

def marcador_valido(valor):
    """Un marcador es un entero no negativo."""
    return entero_no_negativo(valor)

def partidos_contados(datos, ident, c):
    """Recuentos por estado de los partidos del equipo en la competición `c` y, por orden cronológico, los contados:
    finalizados con marcador válido, con su resultado. Un finalizado sin marcador válido no cuenta (ni como 0-0)."""
    partidos = sorted((m for m in datos['partidos'] if ident in (m['home_team_id'], m['away_team_id'])
                       and (m['tournament_id'], m['season_id']) == (c['tournament_id'], c['season_id'])),
                      key=orden_cronologico)
    # in_scope son los encuentros vigentes; los eventos sustituidos por decisión local se cuentan aparte.
    sustituidos = sum(not vigente(m) for m in partidos)
    cuenta = {'in_scope': len(partidos) - sustituidos, 'finished': 0, 'pending': 0, 'counted': 0,
              'finished_without_valid_score': 0, 'excluded_by_version_conflict': 0, 'unknown_status': 0,
              'replaced_locally': sustituidos}
    resultados = []
    for m in partidos:
        if not vigente(m):
            continue
        if m['status'] in PENDIENTES:
            cuenta['pending'] += 1
            continue
        if m['status'] != 'finished':
            cuenta['unknown_status'] += 1
            continue
        cuenta['finished'] += 1
        # Versiones que se contradicen en la identidad o el estado del partido: señalado, nunca en los agregados.
        if m.get('version_conflict') is True:
            cuenta['excluded_by_version_conflict'] += 1
            continue
        if not (marcador_valido(m.get('home_score')) and marcador_valido(m.get('away_score'))):
            cuenta['finished_without_valid_score'] += 1
            continue
        lado = 'home' if m['home_team_id'] == ident else 'away'
        favor, contra = (m['home_score'], m['away_score']) if lado == 'home' else (m['away_score'], m['home_score'])
        cuenta['counted'] += 1
        resultados.append({'match': m, 'side': lado, 'opponent_team_id': m['away_team_id'] if lado == 'home' else m['home_team_id'],
                           'goals_for': favor, 'goals_against': contra,
                           'result': 'win' if favor > contra else 'draw' if favor == contra else 'loss'})
    return cuenta, resultados

def rendimiento(datos, ident):
    """Balance del equipo en cada competición del ámbito, calculado sólo con sus partidos finalizados con marcador
    válido. Un finalizado sin marcador válido no cuenta (ni como 0-0) y se informa aparte; los pendientes tampoco."""
    competiciones = []
    for c in datos['competiciones']:
        cuenta, resultados = partidos_contados(datos, ident, c)
        balance = {'played': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'goals_for': 0, 'goals_against': 0}
        lados = {lado: {'wins': 0, 'draws': 0, 'losses': 0} for lado in ('home', 'away')}
        for r in resultados:
            clave = {'win': 'wins', 'draw': 'draws', 'loss': 'losses'}[r['result']]
            balance['played'] += 1
            balance[clave] += 1
            balance['goals_for'] += r['goals_for']
            balance['goals_against'] += r['goals_against']
            lados[r['side']][clave] += 1
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

NOTA_JUGADORES = ('Acumulados de los jugadores observados en las convocatorias de los partidos contados (finalizados '
                  'con marcador válido) de cada competición del ámbito. No es una plantilla completa ni necesariamente '
                  'actual; cada cifra dice de cuántas convocatorias tiene dato.')

def participacion(jugado, minutos):
    """(confirmada, contradicción) según played y los minutos: played true (con minutos > 0 o sin ellos) o minutos > 0
    sin played la confirman; played true con 0 minutos o played false con minutos > 0 se contradicen."""
    con_minutos = minutos is not None and minutos > 0
    if (jugado is True and minutos == 0) or (jugado is False and con_minutos):
        return False, True
    return jugado is True or (jugado is not False and con_minutos), False

def comun(valores):
    """El valor de un campo en los registros de una misma convocatoria: el común, o None si discrepan (1 y True
    son distintos)."""
    return valores[0] if len({(type(v), json.dumps(v, sort_keys=True)) for v in valores}) == 1 else None

# Lo único que la fuente da de un suplente no utilizado: no prueba participación (totalShots=0 incluido).
ESTADISTICAS_DE_BANQUILLO = {'totalShots', 'statisticsType'}
# Una actuación con estadísticas sustanciales: minutos > 0 y al menos estas claves individuales además de minutos y
# del resto de banquillo (en la exportación del 21-10 la menor actuación jugada trae 8).
MIN_CLAVES_SUSTANCIALES = 3
# Clases de gol de las incidencias que cuentan como goles del jugador; el autogol no, y una tanda de penaltis no es
# un gol (otro incidentType). Una clase desconocida impide contrastar ese partido.
CLASES_DE_GOL = {'regular', 'penalty'}

def contexto_partido(datos, ident):
    """Lo que alineaciones e incidencias originales dicen del partido, para resolver participaciones y contrastar
    goles. `cobertura[lado]`: alineación confirmada, incidencias guardadas y las entradas de ese lado coinciden
    exactamente con los suplentes con minutos (una sección guardada no basta por sí sola). `goles` es None si las
    incidencias no cuadran con el marcador, traen una clase de gol desconocida o se leyeron antes de un marcador
    corregido: entonces no se contrasta. Unas incidencias leídas antes de un cambio de equipos no se usan para nada.
    `anteriores`: las correcciones del evento posteriores a cada sección."""
    partido = next((m for m in datos['partidos'] if m['match_id'] == ident), {})
    alineacion = (datos['originales'].get((ident, 'lineups')) or {}).get('registro', {}).get('payload')
    versiones = {k: [version(r) for r in (datos['originales'].get((ident, k)) or {}).get('todas', [])] for k in ('lineups', 'incidents')}
    incidencias = ((datos['originales'].get((ident, 'incidents')) or {}).get('registro', {}).get('payload') or {})
    incidencias = incidencias.get('incidents') if isinstance(incidencias, dict) else None
    anteriores = {k: anterior_a(datos, ident, k) for k in ('lineups', 'incidents')}
    if 'teams' in anteriores['incidents']:
        incidencias = None  # de los equipos anteriores: ni entradas ni goles del partido vigente
    confirmada = isinstance(alineacion, dict) and alineacion.get('confirmed') is True
    lista = [i for i in incidencias if isinstance(i, dict)] if isinstance(incidencias, list) and incidencias else None
    def jugador(valor):
        return valor.get('id') if isinstance(valor, dict) else None
    entradas = {lado: set() for lado in ('home', 'away')}
    con_eventos = set()
    goles, asistencias, clases = {}, {}, []
    for i in lista or []:
        lado = 'home' if i.get('isHome') is True else 'away' if i.get('isHome') is False else None
        if i.get('incidentType') == 'substitution' and lado:
            entradas[lado].add(jugador(i.get('playerIn')))
            con_eventos.add(jugador(i.get('playerOut')))
        elif i.get('incidentType') in ('card', 'goal'):
            con_eventos.add(jugador(i.get('player')))
        if i.get('incidentType') == 'goal':
            clases.append(i.get('incidentClass'))
            if i.get('incidentClass') in CLASES_DE_GOL:
                goles[jugador(i.get('player'))] = goles.get(jugador(i.get('player')), 0) + 1
                if jugador(i.get('assist1')) is not None:
                    asistencias[jugador(i.get('assist1'))] = asistencias.get(jugador(i.get('assist1')), 0) + 1
    cobertura = {}
    for lado in ('home', 'away'):
        usados = set()
        for e in ((alineacion or {}).get(lado) or {}).get('players') or [] if confirmada else []:
            minutos = (e.get('statistics') or {}).get('minutesPlayed') if isinstance(e, dict) else None
            if isinstance(e, dict) and e.get('substitute') is True and entero_no_negativo(minutos) and minutos > 0:
                usados.add(jugador(e.get('player')))
        cobertura[lado] = confirmada and lista is not None and usados == entradas[lado]
    marcador = (partido.get('home_score'), partido.get('away_score'))
    contrastable = (lista is not None and all(c in CLASES_DE_GOL | {'ownGoal'} for c in clases)
                    and all(marcador_valido(v) for v in marcador) and len(clases) == sum(marcador)
                    and 'score' not in anteriores['incidents'])
    return {'alineacion_confirmada': confirmada, 'entradas': entradas, 'con_eventos': con_eventos, 'cobertura': cobertura,
            'goles': goles if contrastable else None, 'asistencias': asistencias if contrastable else None,
            'versiones': versiones, 'desacuerdos': desacuerdos_de_alineacion(datos, ident),
            'anteriores': anteriores, 'correcciones': correcciones_del_evento(datos, ident),
            'conflictos_estadisticas': conflictos_de_estadisticas(datos, ident)}

def jugadores_de_alineacion(payload):
    for lado in ('home', 'away'):
        for e in ((payload or {}).get(lado) or {}).get('players') or []:
            if isinstance(e, dict) and isinstance(e.get('player'), dict):
                yield e

def alineaciones_comparables(datos, ident):
    """Las versiones distintas de la alineación posteriores al partido: las que traen minutos de algún jugador (una
    instantánea previa no se compara)."""
    todas = (datos['originales'].get((ident, 'lineups')) or {}).get('todas', [])
    unicas = list({r.get('content_hash', id(r)): r for r in todas}.values())
    return [r for r in unicas if any(isinstance((e.get('statistics') or {}).get('minutesPlayed'), int)
                                     for e in jugadores_de_alineacion(r.get('payload')))]

def desacuerdos_de_alineacion(datos, ident):
    """Jugadores cuya participación difiere entre versiones distintas de la alineación posteriores al partido:
    suplente, minutos o estadísticas individuales. {jugador: [lo que dice cada versión]}. Una versión más reciente no
    resuelve el desacuerdo."""
    comparables = alineaciones_comparables(datos, ident)
    if len(comparables) < 2:
        return {}
    dichos = {}
    for r in comparables:
        vistos = {}
        for e in jugadores_de_alineacion(r.get('payload')):
            stats = e.get('statistics') if isinstance(e.get('statistics'), dict) else {}
            vistos[e['player'].get('id')] = {'substitute': e.get('substitute'), 'minutesPlayed': stats.get('minutesPlayed'),
                                             'individual_statistics': sorted(set(stats) - ESTADISTICAS_DE_BANQUILLO - {'minutesPlayed'})}
        for jugador in set(vistos) | set(dichos):
            dichos.setdefault(jugador, []).append(dict(version(r), **(vistos.get(jugador) or {'listed': False})))
    def firma(v):
        return (v.get('listed', True), v.get('substitute'), v.get('minutesPlayed'), bool(v.get('individual_statistics')))
    return {j: vs for j, vs in dichos.items() if len(vs) < len(comparables) or len({firma(v) for v in vs}) > 1}

def se_completan(lecturas):
    """Las lecturas de una clave en varias versiones sólo difieren en que alguna la omite: las que la dan traen el mismo
    valor válido. Un `null` o un valor inválido no es una omisión: frente a cualquier otra lectura es un desacuerdo."""
    valores = {v for estado, v in lecturas if estado == 'valida'}
    return all(estado in ('ausente', 'valida') for estado, _ in lecturas) and len(valores) == 1

def incoherencia_combinada(grupo, valores):
    """Las relaciones del grupo (las de estadisticas_de_actuacion) sobre los valores explícitos y válidos que dan entre
    todas las versiones. Versiones que se completan no pueden, juntas, superar un total: p. ej. una con 1 tiro a puerta
    y otra cuyas categorías complementarias ya agotan los tiros."""
    v = valores.get
    if grupo == 'tiros':
        partes = [v(c) for c in ('onTargetScoringAttempt', 'shotOffTarget', 'blockedScoringAttempt') if v(c) is not None]
        return v('totalShots') is not None and sum(partes) > v('totalShots')
    relacion = {'pases': ('accuratePass', 'totalPass'), 'regates': ('wonContest', 'totalContest')}.get(grupo)
    return relacion is not None and None not in (v(relacion[0]), v(relacion[1])) and v(relacion[0]) > v(relacion[1])

def conflictos_de_estadisticas(datos, ident):
    """Grupos de estadísticas (GRUPOS_ESTADISTICA, más `goles`) en los que versiones comparables de la alineación se
    contradicen: {jugador: {grupo: [versión con sus claves del grupo]}}. Hay contradicción si alguna clave del grupo
    trae valores explícitos distintos, o un `null` o un valor inválido frente a otra lectura, o si los valores que dan
    entre todas incumplen las relaciones del grupo. Que una versión omita una clave que otra da no es contradicción:
    la selección de versiones (y sus originales) decide qué se usa, sin tomar valores de otra versión. Ninguna versión
    gana un conflicto: ese grupo queda sin dato y las demás estadísticas del jugador no se tocan. Un jugador que falta en
    alguna versión es un desacuerdo de participación (desacuerdos_de_alineacion), no de estadísticas."""
    comparables = alineaciones_comparables(datos, ident)
    if len(comparables) < 2:
        return {}
    vistas = {}
    for r in comparables:
        for e in jugadores_de_alineacion(r.get('payload')):
            stats = e.get('statistics') if isinstance(e.get('statistics'), dict) else {}
            vistas.setdefault(e['player'].get('id'), []).append((version(r), stats))
    conflictos = {}
    for jugador, dichas in vistas.items():
        if len(dichas) < len(comparables):
            continue
        for grupo, claves in dict(GRUPOS_ESTADISTICA, goles=('goals',)).items():
            lecturas = {c: [lectura([s], c) for _, s in dichas] for c in claves}
            distintas = [c for c in claves if len({json.dumps(l) for l in lecturas[c]}) > 1]
            if not distintas:
                continue
            combinados = {c: next(v for estado, v in lecturas[c] if estado == 'valida')
                          for c in claves if any(estado == 'valida' for estado, _ in lecturas[c])}
            if not all(se_completan(lecturas[c]) for c in distintas) or incoherencia_combinada(grupo, combinados):
                firmas = [{c: s[c] for c in claves if c in s} for _, s in dichas]
                conflictos.setdefault(jugador, {})[grupo] = [dict(v, statistics=f) for (v, _), f in zip(dichas, firmas)]
    return conflictos

def valor(dato, origen):
    return {'value': dato, 'source': origen if dato is not None else None}

# Estadísticas individuales normalizadas (docs/normalizacion-estadisticas-individuales.md). Cada grupo reúne claves
# relacionadas: una incoherencia, un duplicado que discrepa o un conflicto de versiones deja sin dato su grupo y no
# toca los demás. Las relaciones entre categorías son una interpretación documentada, respaldada por la muestra
# auditada, no una garantía de SofaScore.
GRUPOS_ESTADISTICA = {
    'tiros': ('totalShots', 'onTargetScoringAttempt', 'shotOffTarget', 'blockedScoringAttempt'),
    'pases': ('totalPass', 'accuratePass'),
    'regates': ('totalContest', 'wonContest'),
    'xg': ('expectedGoals',), 'recuperaciones': ('ballRecovery',), 'perdidas': ('possessionLostCtrl',),
    'duelos_ganados': ('duelWon',), 'duelos_perdidos': ('duelLost',), 'faltas': ('fouls',), 'paradas': ('saves',),
    'valoracion': ('rating',),
}
GRUPO_DE_CLAVE = {clave: grupo for grupo, claves in GRUPOS_ESTADISTICA.items() for clave in claves}
# Métrica publicada -> clave original (shotOffTarget y blockedScoringAttempt sólo intervienen en tiros a puerta).
ESTADISTICAS = {
    'shots': 'totalShots', 'shots_on_target': 'onTargetScoringAttempt', 'xg': 'expectedGoals',
    'passes': 'totalPass', 'passes_accurate': 'accuratePass', 'dribbles': 'totalContest', 'dribbles_won': 'wonContest',
    'ball_recoveries': 'ballRecovery', 'possession_lost': 'possessionLostCtrl', 'duels_won': 'duelWon',
    'duels_lost': 'duelLost', 'fouls': 'fouls', 'saves': 'saves', 'rating': 'rating',
}
# No son recuentos: un no utilizado no tiene 0 de ellos, sino ningún valor.
NO_RECUENTOS = {'expectedGoals', 'rating'}
# Motivo de un grupo sin dato -> código de incidencia.
INCIDENCIA_DE_MOTIVO = {'inconsistent': 'stat_inconsistent', 'version_conflict': 'stat_version_conflict',
                        'duplicate_disagree': 'stat_duplicate_disagree', 'invalid': 'stat_invalid'}

def valida(clave, v):
    """Dominio de cada clave: recuentos enteros >= 0; xG número finito >= 0; valoración número finito entre 0 y 10."""
    if clave in NO_RECUENTOS:
        return type(v) in (int, float) and math.isfinite(v) and v >= 0 and (clave != 'rating' or v <= 10)
    return entero_no_negativo(v)

def dato(value, source=None, rule=None, reason=None):
    return {'value': value, 'source': source, 'rule': rule, 'reason': None if value is not None else reason}

def lectura(registros, clave):
    """Lo que dicen de una clave los registros de una convocatoria: ('discrepa', None) si los duplicados no coinciden
    (en valor o en presencia), ('ausente' | 'nula' | 'invalida', None) o ('valida', valor)."""
    vistas = {json.dumps([clave in s, s.get(clave)]) for s in registros}
    if len(vistas) > 1:
        return 'discrepa', None
    if clave not in registros[0]:
        return 'ausente', None
    v = registros[0][clave]
    if v is None:
        return 'nula', None
    return ('valida', v) if valida(clave, v) else ('invalida', None)

def estado_de_gol(registros, goles_incidencias, conflictos):
    """(estado, goles fiables): 'positivo' si hay goles fiables (statistics.goals > 0 coherente con las incidencias
    contrastables, o incidencias que se los atribuyen), 'dudoso' si la cifra de goles no es fiable (discrepa entre
    duplicados o versiones, es inválida o contradice a las incidencias) y 'ninguno' en otro caso. La ausencia de goals
    no prueba 0 goles: sólo deja de bloquear lo que es compatible con ella."""
    estado, v = lectura(registros, 'goals')
    if estado in ('discrepa', 'nula', 'invalida') or 'goles' in conflictos:
        return 'dudoso', None
    if estado == 'valida' and goles_incidencias is not None and v != goles_incidencias:
        return 'dudoso', None
    goles = v if estado == 'valida' else goles_incidencias or 0
    return ('positivo', goles) if goles > 0 else ('ninguno', 0)

def estadisticas_de_actuacion(registros, estado, conflictos, gol, conflicto_de_partido=False):
    """Las estadísticas individuales de una convocatoria normalizadas: {métrica: {value, source, rule, reason}} y las
    incidencias de sus grupos [(código, grupo, motivo)]. `registros` son los statistics de los registros duplicados;
    `conflictos` los grupos que discrepan entre versiones; `gol` el estado_de_gol. source: `reported` (lo da la
    fuente, también un 0), `derived` (fijado por una relación con valores explícitos del mismo registro; `rule`),
    `calculated` (cociente; `rule`) o `unused` (0 de un no utilizado). Sin valor, `reason`: not_reported,
    reported_null, invalid, duplicate_disagree, version_conflict, inconsistent, participation_unknown, not_applicable
    (actuacion añade before_score_correction a la valoración de una alineación anterior a un marcador corregido).
    Nunca se usa una clave ausente como 0 ni el total del equipo."""
    if estado == 'unknown' or conflicto_de_partido:
        motivo = 'participation_unknown' if estado == 'unknown' else 'version_conflict'
        return {m: dato(None, reason=motivo) for m in [*ESTADISTICAS, 'pass_accuracy']}, []
    if estado == 'unused':
        resultado = {m: dato(None, reason='not_applicable') if c in NO_RECUENTOS else dato(0, 'unused')
                     for m, c in ESTADISTICAS.items()}
        return dict(resultado, pass_accuracy=dato(None, reason='not_applicable')), []
    leidas = {c: lectura(registros, c) for c in GRUPO_DE_CLAVE}
    valor_de = lambda c: leidas[c][1] if leidas[c][0] == 'valida' else None
    sin_dato = {}
    for grupo, claves in GRUPOS_ESTADISTICA.items():
        if grupo in conflictos:
            sin_dato[grupo] = 'version_conflict'
        elif any(leidas[c][0] == 'discrepa' for c in claves):
            sin_dato[grupo] = 'duplicate_disagree'
    # Coherencia con los valores explícitos y válidos (una suma inferior al total no es incoherencia: pueden faltar).
    total, a_puerta = valor_de('totalShots'), valor_de('onTargetScoringAttempt')
    complementos = [leidas[c] for c in ('shotOffTarget', 'blockedScoringAttempt')]
    explicitos = sum(v for e, v in complementos if e == 'valida')
    if total is not None and explicitos + (a_puerta or 0) > total:
        sin_dato.setdefault('tiros', 'inconsistent')
    # Si las categorías explícitas agotan el total, no hubo tiro a puerta: un gol fiable lo contradice, igual que
    # más goles que tiros a puerta o que tiros.
    agotan = (total is not None and leidas['onTargetScoringAttempt'][0] == 'ausente' and explicitos == total
              and all(e in ('ausente', 'valida') for e, _ in complementos))
    estado_gol, goles = gol
    if estado_gol == 'positivo' and (agotan or any(v is not None and goles > v for v in (a_puerta, total))):
        sin_dato.setdefault('tiros', 'inconsistent')
    for grupo, ganados, intentos in (('pases', 'accuratePass', 'totalPass'), ('regates', 'wonContest', 'totalContest')):
        if valor_de(ganados) is not None and valor_de(intentos) is not None and valor_de(ganados) > valor_de(intentos):
            sin_dato.setdefault(grupo, 'inconsistent')
    resultado, incidencias = {}, []
    for metrica, clave in ESTADISTICAS.items():
        grupo = GRUPO_DE_CLAVE[clave]
        estado_clave, v = leidas[clave]
        if grupo in sin_dato:
            resultado[metrica] = dato(None, reason=sin_dato[grupo])
        elif estado_clave == 'valida':
            resultado[metrica] = dato(v, 'reported')
        elif estado_clave == 'nula':
            resultado[metrica] = dato(None, reason='reported_null')
        elif estado_clave == 'invalida':
            resultado[metrica] = dato(None, reason='invalid')
            incidencias.append(('stat_invalid', grupo, [clave]))
        elif metrica == 'shots_on_target' and agotan and estado_gol == 'ninguno':
            resultado[metrica] = dato(0, 'derived', 'shots_partition')
        else:
            resultado[metrica] = dato(None, reason='not_reported')
    for grupo, motivo in sin_dato.items():
        incidencias.append((INCIDENCIA_DE_MOTIVO[motivo], grupo, list(GRUPOS_ESTADISTICA[grupo])))
    # Un complemento inválido (no mostrado) también es una incidencia de su grupo.
    for c in ('shotOffTarget', 'blockedScoringAttempt'):
        if leidas[c][0] == 'invalida' and 'tiros' not in sin_dato:
            incidencias.append(('stat_invalid', 'tiros', [c]))
    pases, completados = resultado['passes'], resultado['passes_accurate']
    if pases['value'] is not None and completados['value'] is not None:
        resultado['pass_accuracy'] = (dato(completados['value'] / pases['value'] * 100, 'calculated', 'ratio')
                                      if pases['value'] > 0 else dato(None, reason='not_applicable'))
    else:
        motivos = [m['reason'] for m in (pases, completados) if m['value'] is None]
        resultado['pass_accuracy'] = dato(None, reason=next((m for m in motivos if m != 'not_reported'), 'not_reported'))
    return resultado, incidencias

def actuacion(filas, partido, contexto):
    """La convocatoria de un jugador en un partido, normalizada con las reglas de esta fuente (una sola vez para
    acumulados, ficha y alineaciones). Los registros duplicados se unen; un campo que discrepa o queda fuera de su
    dominio no tiene dato. Participación `played`, `unused` o `unknown` con su base; minutos, goles y asistencias con
    su origen: `reported` (lo da la fuente), `omitted_zero` (clave omitida en una actuación con estadísticas
    sustanciales: regla del adaptador, no un cero explícito) o `unused` (no utilizado). Nunca minutos derivados.
    Lo que contradice algo queda sin dato y como incidencia de calidad (`issues`), con su evidencia (`evidence`):
    jugador, partido, campos en conflicto con los valores originales y fuentes, para poder contrastarla más adelante
    con otras fuentes. Nada se resuelve a mano."""
    def campo(obtener, valido):
        v = comun([obtener(r) for r in filas])
        return v if valido(v) else None
    def estadisticas_de(r):
        return r['statistics'] if isinstance(r.get('statistics'), dict) else {}
    booleano = lambda v: type(v) is bool
    lado = campo(lambda r: r.get('side'), lambda v: v in ('home', 'away'))
    equipo = None if lado is None else partido[f'{lado}_team_id']
    jugador = filas[0]['player_id']
    incidencias = []
    if any(r.get('team_id') != equipo for r in filas):
        incidencias.append('team_id_mismatch')
    if any(r.get('source_team_id') not in (None, equipo) for r in filas):
        incidencias.append('source_team_mismatch')
    suplente = campo(lambda r: r.get('substitute'), booleano)
    jugado = campo(lambda r: r.get('played'), booleano)
    # minutes_played y, si falta, statistics.minutesPlayed.
    minutos = campo(lambda r: r['minutes_played'] if r.get('minutes_played') is not None else estadisticas_de(r).get('minutesPlayed'),
                    entero_no_negativo)
    # Las claves que traen todos los registros (un duplicado puede discrepar en otras).
    claves = set.intersection(*(set(estadisticas_de(r)) for r in filas))
    solo_banquillo = all(set(estadisticas_de(r)) <= ESTADISTICAS_DE_BANQUILLO | ({'minutesPlayed'} if minutos == 0 else set())
                         for r in filas)
    entra = lado is not None and jugador in contexto['entradas'][lado]
    evidencias = []
    def evidencia(codigo, campos, fuentes):
        # Los valores tal como vienen en los registros (sin validar), para no perder la discrepancia.
        evidencias.append({'issue': codigo, 'match_id': filas[0].get('match_id'), 'player_id': jugador, 'fields': campos,
                           'sources': fuentes, 'records': [r.get('fetched_at') for r in filas],
                           'versions': contexto['versiones']})
    def participacion_original():
        individuales = sorted(set().union(*(set(estadisticas_de(r)) for r in filas)) - ESTADISTICAS_DE_BANQUILLO)
        return {'substitute': [r.get('substitute') for r in filas], 'played': [r.get('played') for r in filas],
                'minutes_played': [r.get('minutes_played') for r in filas], 'statistics_keys': individuales,
                'substitution_in': entra, 'lineup_confirmed': contexto['alineacion_confirmada'],
                'substitutions_cover_side': contexto['cobertura'].get(lado) if lado else None}
    FUENTES_PARTICIPACION = ['jugador_partido', 'originales:lineups', 'originales:incidents']
    confirmada, contradiccion = participacion(jugado, minutos)
    if jugador in contexto['desacuerdos']:
        # Las versiones de la alineación no dicen lo mismo de este jugador: ninguna gana por ser más reciente.
        estado, base = 'unknown', 'lineup_versions_disagree'
        incidencias.append('lineup_versions_disagree')
        evidencia('lineup_versions_disagree', dict(participacion_original(), lineup_versions=contexto['desacuerdos'][jugador]),
                  ['originales:lineups'])
    elif contradiccion or (jugado is False and (entra or (suplente is False and contexto['alineacion_confirmada']))):
        estado, base = 'unknown', 'contradiction'
        incidencias.append('participation_contradiction')
        evidencia('participation_contradiction', participacion_original(), FUENTES_PARTICIPACION)
    elif confirmada:
        estado, base = 'played', 'minutes' if minutos else 'played_flag'
    elif entra:
        estado, base = 'played', 'substitution_in'
    elif suplente is False and contexto['alineacion_confirmada']:
        estado, base = 'played', 'starting_lineup'
    elif suplente is True and lado is not None and contexto['cobertura'][lado] and solo_banquillo and jugador not in contexto['con_eventos']:
        estado, base = 'unused', 'unused_substitute'
    else:
        estado = 'unknown'
        base = ('stats_without_entry' if not solo_banquillo else 'events_without_entry' if jugador in contexto['con_eventos']
                else 'insufficient_coverage')
        if base != 'insufficient_coverage':
            incidencias.append(base)
            evidencia(base, participacion_original(), FUENTES_PARTICIPACION)
    sustanciales = (minutos is not None and minutos > 0
                    and len(claves - ESTADISTICAS_DE_BANQUILLO - {'minutesPlayed'}) >= MIN_CLAVES_SUSTANCIALES)
    # Una alineación leída antes del marcador corregido no dice los goles ni las asistencias del partido corregido.
    previa = estado == 'played' and 'score' in contexto['anteriores']['lineups']
    if previa:
        incidencias.append('lineups_before_score_correction')
        evidencia('lineups_before_score_correction',
                  {'lineups.fetched_at': [r.get('fetched_at') for r in filas],
                   'score_observed_at': contexto['correcciones']['score'][0],
                   'score': [partido.get('home_score'), partido.get('away_score')]},
                  ['originales:lineups', 'originales:event'])
    def contribucion(clave, eventos, codigo):
        if estado == 'unused':
            return valor(0, 'unused')
        if estado != 'played' or previa:
            return valor(None, None)
        presente = comun([clave in estadisticas_de(r) for r in filas])
        if presente:
            dato = campo(lambda r: estadisticas_de(r).get(clave), entero_no_negativo)
            origen = 'reported'
        elif presente is False and sustanciales:
            dato, origen = 0, 'omitted_zero'
        else:
            dato, origen = None, None
        if dato is not None and eventos is not None and dato != eventos.get(jugador, 0):
            incidencias.append(codigo)
            evidencia(codigo, {f'statistics.{clave}': [estadisticas_de(r).get(clave) for r in filas], 'source_rule': origen,
                                'incidents': eventos.get(jugador, 0)},
                      ['jugador_partido:statistics', 'originales:incidents'])
            return valor(None, None)
        return valor(dato, origen)
    goles = contribucion('goals', contexto['goles'], 'goals_mismatch')
    asistencias = contribucion('goalAssist', contexto['asistencias'], 'assists_mismatch')
    registros = [estadisticas_de(r) for r in filas]
    conflictos = contexto['conflictos_estadisticas'].get(jugador, {})
    gol = (('dudoso', None) if previa else
           estado_de_gol(registros, None if contexto['goles'] is None else contexto['goles'].get(jugador, 0), conflictos))
    estadisticas, problemas = estadisticas_de_actuacion(registros, estado, conflictos, gol,
                                                        conflicto_de_partido=partido.get('version_conflict') is True)
    if previa:
        # La valoración de la fuente incluye los goles: la de una alineación anterior al marcador corregido no es la
        # del partido vigente. El original se conserva en la sección; vuelve a usarse al releerla.
        estadisticas['rating'] = dato(None, reason='before_score_correction')
    for codigo, grupo, claves_grupo in problemas:
        if codigo not in incidencias:
            incidencias.append(codigo)
        campos = {f'statistics.{c}': [r.get(c) for r in registros] for c in claves_grupo}
        fuentes = ['jugador_partido:statistics']
        if codigo == 'stat_version_conflict':
            campos['lineup_versions'] = conflictos[grupo]
            fuentes = ['originales:lineups']
        elif grupo == 'tiros':
            campos.update({'statistics.goals': [r.get('goals') for r in registros],
                           'incidents_goals': None if contexto['goles'] is None else contexto['goles'].get(jugador, 0)})
            fuentes.append('originales:incidents')
        evidencia(codigo, dict(campos, group=grupo), fuentes)
    return {
        'player_id': jugador, 'side': lado, 'team_id': equipo,
        'starter': None if suplente is None else not suplente,
        'participation': estado, 'basis': base,
        'minutes': valor(minutos, 'reported') if estado == 'played' else valor(0, 'unused') if estado == 'unused' else valor(None, None),
        'goals': goles,
        'assists': asistencias,
        'stats': estadisticas,
        'shirt_number': campo(lambda r: r.get('shirt_number'), entero_no_negativo),
        'duplicate_records': len(filas) - 1,
        'issues': incidencias,
        'evidence': evidencias,
    }

def actuaciones_del_partido(datos, ident, contexto=None):
    """Todas las convocatorias normalizadas de un partido del ámbito, por jugador (ambos equipos)."""
    partido = next(m for m in datos['partidos'] if m['match_id'] == ident)
    contexto = contexto or contexto_partido(datos, ident)
    grupos = {}
    for r in datos['jugador_partido']:
        if r.get('match_id') == ident and type(r.get('player_id')) is int:
            grupos.setdefault(r['player_id'], []).append(r)
    return {jugador: actuacion(filas, partido, contexto) for jugador, filas in grupos.items()}

def suma(valores, de):
    """Total de los valores con dato (`matches`) de `de` convocatorias; sin ninguno, None (nunca 0)."""
    validos = [v for v in valores if v is not None]
    return {'value': sum(validos) if validos else None, 'matches': len(validos), 'of': de}

def acumulados_jugadores(datos, ident):
    """Acumulados de cada jugador del equipo en cada competición del ámbito, sobre los mismos partidos contados que el
    balance, con las convocatorias normalizadas (`actuacion`). El equipo es el del lado de la alineación: un team_id
    o source_team_id discrepante se conserva como advertencia. Titularidades por `substitute` explícito; partidos
    jugados sobre las convocatorias con participación conocida (jugó o no utilizado); minutos, goles y asistencias
    de las convocatorias que los tienen (un no utilizado suma 0)."""
    fichas = {p['player_id']: p for p in datos['jugadores']}
    competiciones = []
    for c in datos['competiciones']:
        cuenta, resultados = partidos_contados(datos, ident, c)
        con_fecha = {r['match']['match_id'] for r in resultados if inicio(r['match']) is not None}
        convocatorias, descartados = {}, 0
        for r in resultados:  # orden cronológico
            for jugador, a in actuaciones_del_partido(datos, r['match']['match_id']).items():
                if a['side'] is None:
                    descartados += 1 if any(f.get('team_id') == ident for f in datos['jugador_partido']
                                            if f.get('match_id') == r['match']['match_id'] and f.get('player_id') == jugador) else 0
                elif a['team_id'] == ident:
                    convocatorias.setdefault(jugador, []).append(dict(a, partido=r['match']['match_id']))
        jugadores = []
        for jugador, filas in sorted(convocatorias.items()):
            n = len(filas)
            ficha = fichas.get(jugador) or {}
            altura = ficha.get('height_cm')
            pie = ficha.get('preferred_foot')
            # El de su última convocatoria con fecha: histórico, no necesariamente el actual.
            ultima = next((f for f in reversed(filas) if f['partido'] in con_fecha), None)
            jugadas = sum(f['participation'] == 'played' for f in filas)
            conocidas = sum(f['participation'] != 'unknown' for f in filas)
            jugadores.append({
                'player_id': jugador, 'name': ficha.get('name'), 'usual_position': ficha.get('usual_position'),
                'height_cm': altura if entero_no_negativo(altura) and altura > 0 else None,
                'preferred_foot': pie if isinstance(pie, str) else None,
                'last_shirt_number': ultima['shirt_number'] if ultima else None,
                'call_ups': n,
                'starts': suma([None if f['starter'] is None else int(f['starter']) for f in filas], n),
                'appearances': {'value': jugadas, 'matches': conocidas, 'of': n},
                'minutes': suma([f['minutes']['value'] for f in filas], n),
                'goals': suma([f['goals']['value'] for f in filas], n),
                'assists': suma([f['assists']['value'] for f in filas], n),
            })
        todas = [f for fs in convocatorias.values() for f in fs]
        competiciones.append(dict({'name': c['nombre'], 'season': c.get('temporada')}, **{k: c[k] for k in CAMPOS_AMBITO},
                                  matches={'counted': cuenta['counted'], 'with_call_ups': len({f['partido'] for f in todas})},
                                  call_ups={'total': len(todas),
                                            'played': sum(f['participation'] == 'played' for f in todas),
                                            'unused': sum(f['participation'] == 'unused' for f in todas),
                                            'unknown': sum(f['participation'] == 'unknown' for f in todas),
                                            'contradictions': sum('participation_contradiction' in f['issues'] for f in todas),
                                            'mismatches': sum(any(i in f['issues'] for i in ('goals_mismatch', 'assists_mismatch')) for f in todas),
                                            'team_warnings': sum(any(i in f['issues'] for i in ('team_id_mismatch', 'source_team_mismatch')) for f in todas),
                                            'duplicate_records': sum(f['duplicate_records'] for f in todas),
                                            'discarded_records': descartados},
                                  players=jugadores))
    return {'team_id': ident, 'competitions': competiciones}

NOTA_EXPORTACIONES = ('teams_with_own_export: equipos con una exportación propia declarada en el manifiesto del importador; '
                      'no acredita que su calendario ni sus estadísticas estén completos.')

def equipos_con_exportacion(datos):
    """Equipos con exportación propia según el manifiesto (exportaciones.json); sin manifiesto, ninguno declarado."""
    manifiesto = datos.get('exportaciones') or {}
    nombres = {e['team_id']: e['name'] for e in datos['equipos']}
    equipos = []
    for equipo in manifiesto.get('followed_team_ids') or []:
        propias = [e for e in manifiesto.get('exports') or [] if e.get('followed_team_id') == equipo]
        equipos.append({'team_id': equipo, 'name': nombres.get(equipo),
                        'exports': [{k: e.get(k) for k in ('file', 'sha256', 'fetched_from', 'fetched_to')} for e in propias]})
    return equipos

def consultar(datos, ruta):
    partes = ruta.strip('/').split('/')
    if ruta == '/health':
        return 200, {'data': {'status':'ok'}}
    if ruta == '/api/v1/scope':
        competiciones = [dict({'name': c['nombre'], 'season': c.get('temporada')}, **{k: c[k] for k in CAMPOS_AMBITO}) for c in datos['competiciones']]
        sustituidos = sum(not vigente(m) for m in datos['partidos'])
        return 200, {'data': {'competitions': competiciones, 'teams_with_own_export': equipos_con_exportacion(datos),
                              'matches': {'events': len(datos['partidos']), 'current': len(datos['partidos']) - sustituidos,
                                          'replaced_locally': sustituidos},
                              'local_replacements': [{'replaced': s['sustituido'], 'current': s['vigente'], 'reason': s['motivo'],
                                                      'decided_by': s['procedencia']['decidido_por'],
                                                      'decided_on': s['procedencia']['fecha']} for s in datos['sustituciones']]},
                     'meta': {'note': 'Sólo se sirven partidos, convocatorias y jugadores de estas competiciones. '
                                      + NOTA_EXPORTACIONES + ' local_replacements: ' + NOTA_SUSTITUCION}}
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
    if tipo == 'teams' and sub == 'player-stats':
        return 200, {'data': acumulados_jugadores(datos, ident), 'meta': {'note': NOTA_JUGADORES}}
    if tipo == 'teams' and sub == 'matches':
        # El calendario del equipo: sus encuentros vigentes. Un evento sustituido sigue en /matches/{id}.
        delequipo = [m for m in datos['partidos'] if ident in (m['home_team_id'],m['away_team_id'])]
        resultado = [m for m in delequipo if vigente(m)]
        apartados = [m['match_id'] for m in delequipo if not vigente(m)]
        return 200, {'data':resultado,'meta':{'count':len(resultado),'replaced_locally':apartados}}
    elif tipo == 'teams' and sub == 'players':
        ids = {r['player_id'] for r in datos['jugador_partido'] if r['team_id']==ident}
        resultado = [{'player_id':p['player_id'],'name':p['name'],'usual_position':p['usual_position']} for p in datos['jugadores'] if p['player_id'] in ids]
        return 200, {'data':resultado,'meta':{'count':len(resultado),'scope':'Jugadores observados en convocatorias; no es una plantilla completa ni necesariamente actual.'}}
    elif tipo == 'players' and sub == 'matches':
        partidos = {m['match_id']:m for m in datos['partidos']}
        # Cada convocatoria con su versión normalizada (las mismas reglas que acumulados y alineaciones).
        normalizadas = {m: actuaciones_del_partido(datos, m).get(ident) for m in {r['match_id'] for r in datos['jugador_partido'] if r['player_id']==ident}}
        resultado = [dict(r,match=partidos[r['match_id']],normalized=normalizadas[r['match_id']]) for r in datos['jugador_partido'] if r['player_id']==ident]
    else:
        return 404, {'error':'Ruta inexistente'}
    return 200, {'data':resultado,'meta':{'count':len(resultado)}}

def datos_de_sql(ap, args, recursos):
    """Modo SQL: solo la base SQLite indicada, en solo lectura y con su revisión de esquema; las sustituciones son
    las guardadas en ella. Si falta, no es compatible o no se lee, la API no arranca: nunca crea una base ni recurre
    a los JSON. Las dependencias (requirements-sql.txt) solo se importan aquí."""
    if args.base is None:
        ap.error('--almacen sql necesita --base RUTA')
    if args.datos is not None or args.sustituciones is not None:
        ap.error('--almacen sql lee solo la base: no admite --datos ni --sustituciones (las sustituciones están en la base)')
    try:
        from persistencia.lectura import BaseNoUtilizable, cargar_desde_base
        from persistencia.bloqueo import BaseEnUso, bloquear
    except ImportError as e:
        ap.exit(1, f'El modo SQL necesita las dependencias de requirements-sql.txt (entorno .venv): {e}\n')
    try:
        if not args.base.is_file():
            raise BaseNoUtilizable(f'No existe la base {args.base} (el modo SQL nunca crea una)')
        recursos.enter_context(bloquear(args.base))
        return cargar_desde_base(args.base, args.ambito)
    except (BaseNoUtilizable, BaseEnUso) as e:
        ap.exit(1, f'Base no utilizable: {e}\n')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--almacen',choices=('json','sql'),default='json',
                    help='json (predeterminado): carpeta de importación; sql: solo la base de --base')
    ap.add_argument('--datos',type=Path,default=None,help='carpeta de importación (modo json; por defecto datos/)')
    ap.add_argument('--base',type=Path,default=None,help='base SQLite (solo modo sql)')
    ap.add_argument('--host',default='127.0.0.1',help='dirección de escucha (por defecto solo loopback)')
    ap.add_argument('--port',type=int,default=8001)
    ap.add_argument('--ambito',type=Path,default=AMBITO)
    ap.add_argument('--sustituciones',type=Path,default=None,
                    help='decisiones locales de eventos sustituidos (modo json; sin él, ninguna)')
    args=ap.parse_args()
    with ExitStack() as recursos:
        if args.almacen=='sql':
            datos=datos_de_sql(ap,args,recursos)
        else:
            if args.base is not None:
                ap.error('--base solo se usa con --almacen sql')
            datos=cargar(args.datos or Path(__file__).parent/'datos',args.ambito,args.sustituciones)
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
        print(f'API: http://{args.host}:{args.port} (almacén {args.almacen}) — Ctrl+C para detener',flush=True)
        servidor=ThreadingHTTPServer((args.host,args.port),Handler)
        try:servidor.serve_forever()
        except KeyboardInterrupt:pass
        finally:servidor.server_close()

if __name__=='__main__':main()
