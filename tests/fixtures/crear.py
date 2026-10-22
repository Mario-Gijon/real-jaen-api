"""Genera tests/fixtures/datos: una muestra pequeña y sintética con la forma de la salida del importador.

Equipos (salvo el Real Jaén), jugadores, partidos y estadísticas son inventados. Los ids de
competición y temporada son los de ambito.json, para que las pruebas usen el ámbito real.
Ejecutar desde la carpeta de la API:  python tests/fixtures/crear.py
"""
import json
from pathlib import Path

DESTINO = Path(__file__).parent / 'datos'
JAEN, RIVAL_A, RIVAL_B, RIVAL_COPA, RIVAL_G1 = 2841, 9001, 9002, 9003, 9004
GRUPO_2 = {'unique_tournament_id': 17073, 'tournament_id': 94191, 'season_id': 97382, 'competition': 'Primera Federacion, Group 2'}
COPA = {'unique_tournament_id': 329, 'tournament_id': 150, 'season_id': 104108, 'competition': 'Copa del Rey'}
GRUPO_1 = {'unique_tournament_id': 17073, 'tournament_id': 94190, 'season_id': 97382, 'competition': 'Primera Federacion, Group 1'}
FINALIZADO, PENDIENTE, EN_COPA, EN_GRUPO_1 = 1001, 1002, 1003, 1004
# Más partidos del Grupo 2 para el balance: empate y derrota en casa, finalizados sin marcador válido
# (ausente, booleano, negativo), un estado desconocido y pendientes (uno con marcador, que no cuenta).
EMPATE_CASA, DERROTA_CASA, SIN_MARCADOR, MARCADOR_BOOLEANO, MARCADOR_NEGATIVO = 1005, 1006, 1007, 1008, 1009
EN_JUEGO, PENDIENTE_CON_MARCADOR, APLAZADO = 1010, 1011, 1012
# Para el orden: MISMO_INSTANTE empieza a la vez que DERROTA_CASA (escrito con otro desfase horario) y tiene una
# jornada mayor; SIN_FECHA está contado pero sin fecha.
MISMO_INSTANTE, SIN_FECHA = 1013, 1014


def item(clave, local, visitante):
    return {'key': clave, 'name': clave, 'home': str(local), 'away': str(visitante), 'homeValue': local, 'awayValue': visitante}


def estadisticas(ident, periodos, fetched_at='2026-10-21T12:00:00+00:00'):
    return original(ident, 'statistics', {'statistics': [{'period': p, 'groups': g} for p, g in periodos]}, fetched_at)
UNO, SOLO_COPA, SUPLENTE = 5001, 5002, 5003


def partido(ident, competicion, local, visitante, estado, marcador, kickoff, ronda, secciones):
    return {'source': 'Sofascore', 'match_id': ident, 'kickoff_utc': kickoff, 'status': estado,
            'tournament_id': competicion['tournament_id'], 'competition': competicion['competition'],
            'season_id': competicion['season_id'], 'season': '26/27', 'round': ronda,
            'home_team_id': local, 'away_team_id': visitante,
            'home_score': marcador[0] if marcador else None, 'away_score': marcador[1] if marcador else None,
            'fetched_at': '2026-10-21T12:00:00+00:00', 'available_sections': secciones}


def original(ident, key, payload, fetched_at='2026-10-21T12:00:00+00:00'):
    return {'event_id': ident, 'key': key, 'sub': '', 'fetched_at': fetched_at, 'payload': payload}


def evento(ident, competicion):
    return original(ident, 'event', {'event': {'id': ident, 'tournament': {
        'id': competicion['tournament_id'], 'name': competicion['competition'],
        'uniqueTournament': {'id': competicion['unique_tournament_id']}}, 'season': {'id': competicion['season_id'], 'year': '26/27'}}})


def jugador_sofascore(ident, nombre, posicion, altura):
    return {'id': ident, 'name': nombre, 'position': posicion, 'height': altura, 'country': {'name': 'Spain'},
            'dateOfBirthTimestamp': 946684800}


def alineacion(ident, local, visitante, fetched_at):
    def lado(jugadores):
        return {'players': [{'player': j, 'shirtNumber': n, 'substitute': s, 'position': j['position'],
                             'statistics': st} for j, n, s, st in jugadores]}
    return original(ident, 'lineups', {'confirmed': True, 'home': lado(local), 'away': lado(visitante)}, fetched_at)


def convocatoria(ident, jugador, lado, suplente, minutos, estadisticas, fetched_at):
    return {'source': 'Sofascore', 'match_id': ident, 'team_id': JAEN, 'player_id': jugador, 'side': lado,
            'source_team_id': JAEN, 'substitute': suplente, 'match_position': 'M', 'shirt_number': jugador % 100,
            'minutes_played': minutos, 'played': (minutos > 0) if minutos is not None else None,
            'statistics': estadisticas, 'fetched_at': fetched_at}


def crear():
    uno = jugador_sofascore(UNO, 'Jugador Sintético Uno', 'M', 175)
    uno_copa = jugador_sofascore(UNO, 'Jugador Sintético Uno (Copa)', 'F', 199)
    solo_copa = jugador_sofascore(SOLO_COPA, 'Jugador Sólo Copa', 'D', 180)
    suplente = jugador_sofascore(SUPLENTE, 'Jugador Sintético Suplente', 'D', 182)
    stats_uno = {'minutesPlayed': 90, 'totalShots': 2, 'goals': 1, 'goalAssist': 0, 'totalPass': 30, 'accuratePass': 25}
    datos = {
        'equipos': [{'source': 'Sofascore', 'team_id': t, 'name': n} for t, n in
                    ((JAEN, 'Real Jaén'), (RIVAL_A, 'Club Sintético A'), (RIVAL_B, 'Club Sintético B'),
                     (RIVAL_COPA, 'Club Sintético Copa'), (RIVAL_G1, 'Club Sintético Grupo 1'))],
        'partidos': [
            partido(FINALIZADO, GRUPO_2, RIVAL_A, JAEN, 'finished', (0, 2), '2026-08-30T17:15:00+00:00', 1,
                    ['event', 'statistics', 'lineups', 'incidents']),
            partido(PENDIENTE, GRUPO_2, JAEN, RIVAL_B, 'notstarted', None, '2026-11-07T15:30:00+00:00', 11, ['event']),
            partido(EN_COPA, COPA, RIVAL_COPA, JAEN, 'finished', (1, 1), '2026-10-28T19:00:00+00:00', 1, ['event', 'lineups']),
            partido(EN_GRUPO_1, GRUPO_1, RIVAL_G1, JAEN, 'finished', (0, 0), '2026-09-14T17:00:00+00:00', 3, ['event']),
            partido(EMPATE_CASA, GRUPO_2, JAEN, RIVAL_B, 'finished', (1, 1), '2026-09-06T17:00:00+00:00', 2, ['event']),
            partido(DERROTA_CASA, GRUPO_2, JAEN, RIVAL_A, 'finished', (0, 3), '2026-09-20T17:00:00+00:00', 4, ['event']),
            partido(SIN_MARCADOR, GRUPO_2, RIVAL_B, JAEN, 'finished', None, '2026-09-27T17:00:00+00:00', 5, ['event']),
            partido(MARCADOR_BOOLEANO, GRUPO_2, RIVAL_A, JAEN, 'finished', (True, 0), '2026-10-04T17:00:00+00:00', 6, ['event']),
            partido(MARCADOR_NEGATIVO, GRUPO_2, JAEN, RIVAL_B, 'finished', (-1, 2), '2026-10-11T17:00:00+00:00', 7, ['event']),
            partido(EN_JUEGO, GRUPO_2, RIVAL_A, JAEN, 'inprogress', (0, 0), '2026-10-18T17:00:00+00:00', 8, ['event']),
            partido(PENDIENTE_CON_MARCADOR, GRUPO_2, RIVAL_B, JAEN, 'notstarted', (0, 0), '2026-11-14T17:00:00+00:00', 12, ['event']),
            partido(APLAZADO, GRUPO_2, JAEN, RIVAL_A, 'postponed', None, None, 13, ['event']),
            partido(MISMO_INSTANTE, GRUPO_2, RIVAL_B, JAEN, 'finished', (0, 1), '2026-09-20T19:00:00+02:00', 9, ['event']),
            partido(SIN_FECHA, GRUPO_2, JAEN, RIVAL_A, 'finished', (2, 0), None, 10, ['event']),
        ],
        # La ficha guardada de UNO es la más reciente (la de Copa): la API debe rehacerla con el Grupo 2.
        'jugadores': [
            {'source': 'Sofascore', 'player_id': UNO, 'name': 'Jugador Sintético Uno (Copa)', 'usual_position': 'F',
             'country': 'Spain', 'birth_date': '2000-01-01', 'height_cm': 199, 'preferred_foot': None,
             'fetched_at': '2026-10-29T12:00:00+00:00'},
            {'source': 'Sofascore', 'player_id': SOLO_COPA, 'name': 'Jugador Sólo Copa', 'usual_position': 'D',
             'country': 'Spain', 'birth_date': '2000-01-01', 'height_cm': 180, 'preferred_foot': None,
             'fetched_at': '2026-10-29T12:00:00+00:00'},
            {'source': 'Sofascore', 'player_id': SUPLENTE, 'name': 'Jugador Sintético Suplente', 'usual_position': 'D',
             'country': 'Spain', 'birth_date': '2000-01-01', 'height_cm': 182, 'preferred_foot': None,
             'fetched_at': '2026-10-21T12:00:00+00:00'},
        ],
        'jugador_partido': [
            convocatoria(FINALIZADO, UNO, 'away', False, 90, stats_uno, '2026-10-21T12:00:00+00:00'),
            convocatoria(FINALIZADO, SUPLENTE, 'away', True, None, {'totalShots': 0}, '2026-10-21T12:00:00+00:00'),
            convocatoria(EN_COPA, UNO, 'away', False, 90, {'minutesPlayed': 90}, '2026-10-29T12:00:00+00:00'),
            convocatoria(EN_COPA, SOLO_COPA, 'away', False, 90, {'minutesPlayed': 90}, '2026-10-29T12:00:00+00:00'),
        ],
        'originales': [
            evento(FINALIZADO, GRUPO_2), evento(PENDIENTE, GRUPO_2), evento(EN_COPA, COPA), evento(EN_GRUPO_1, GRUPO_1),
            # Visitante: lo propio es `away`. ALL y 1ST distintos (nunca se suman); una clave repetida con el mismo valor.
            estadisticas(FINALIZADO, [
                ('ALL', [{'groupName': 'Match overview', 'statisticsItems': [
                    item('ballPossession', 40, 60), item('expectedGoals', 0.8, 1.5), item('totalShotsOnGoal', 9, 12),
                    item('cornerKicks', 4, 5)]},
                    {'groupName': 'Shots', 'statisticsItems': [
                        item('expectedGoals', 0.8, 1.5), item('totalShotsOnGoal', 9, 12), item('shotsOnGoal', 3, 2)]}]),
                ('1ST', [{'groupName': 'Match overview', 'statisticsItems': [
                    item('ballPossession', 45, 55), item('expectedGoals', 0.3, 0.7), item('totalShotsOnGoal', 4, 6),
                    item('shotsOnGoal', 1, 1), item('cornerKicks', 2, 2)]}]),
            ]),
            # Local: ceros reales (xG y tiros), posesión booleana (no válida), tiros a puerta contradictorios entre
            # grupos (se descartan) y sin córners.
            estadisticas(EMPATE_CASA, [('ALL', [
                {'groupName': 'Match overview', 'statisticsItems': [
                    item('ballPossession', True, 45), item('expectedGoals', 0.0, 0.9), item('totalShotsOnGoal', 0, 7),
                    item('shotsOnGoal', 1, 2)]},
                {'groupName': 'Shots', 'statisticsItems': [item('shotsOnGoal', 2, 2)]}])]),
            # Sección guardada sin contenido: no son ceros.
            original(DERROTA_CASA, 'statistics', {'statistics': []}),
            original(FINALIZADO, 'incidents', {'incidents': [{'incidentType': 'goal', 'time': 30, 'player': {'id': UNO}}]}),
            alineacion(FINALIZADO, [], [(uno, 10, False, stats_uno), (suplente, 20, True, {'totalShots': 0})],
                       '2026-10-21T12:00:00+00:00'),
            alineacion(EN_COPA, [], [(uno_copa, 10, False, {'minutesPlayed': 90}), (solo_copa, 30, False, {'minutesPlayed': 90})],
                       '2026-10-29T12:00:00+00:00'),
        ],
    }
    DESTINO.mkdir(exist_ok=True)
    for nombre, contenido in datos.items():
        (DESTINO / f'{nombre}.json').write_text(json.dumps(contenido, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    crear()
