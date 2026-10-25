"""Lectura de la API desde la base SQLite (fase 3, docs/persistencia-sqlite.md 5.1).

La base se abre solo en lectura (`mode=ro`: nunca se crea ni se modifica), se comprueba que su revisión de esquema es
la de este código y que tiene una carga inicial confirmada, y se reconstruyen los mismos datos que lee el modo JSON
(persistencia.reconstruccion). A partir de ahí el camino es el de siempre: api.componer (fichas, ámbito,
sustituciones guardadas en la base) y, al consultar, las reglas actuales de normalización y agregados. Todo se lee al
arrancar; la conexión se cierra después (sin recarga en caliente).

Con el diario WAL, una conexión de solo lectura puede dejar junto a la base sus archivos `-shm` y `-wal` (vacío): es
el funcionamiento normal de SQLite y no cambia los datos.
"""
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy.exc import SQLAlchemyError

import api
from persistencia import reconstruccion, sqlite
from persistencia.carga import configuracion_alembic
from persistencia.decisiones import sustituciones_guardadas

# Lo que la API necesita de la base: los mismos archivos que lee el modo JSON (resumen aparte).
DATOS = ('equipos', 'jugadores', 'partidos', 'jugador_partido', 'exportaciones', 'discrepancias', 'fichas_originales')


class BaseNoUtilizable(Exception):
    """La API no arranca: la base falta, no es de este esquema o no se puede leer."""


def revision_esperada():
    return ScriptDirectory.from_config(configuracion_alembic(Path('.'))).get_current_head()


def leer_base(ruta):
    """(datos almacenados con los originales indexados, sustituciones activas) de una base válida."""
    ruta = Path(ruta).resolve()
    if not ruta.is_file():
        raise BaseNoUtilizable(f'No existe la base {ruta} (el modo SQL nunca crea una)')
    esperada = revision_esperada()
    motor = sqlite.crear_motor(ruta, solo_lectura=True)
    try:
        with motor.connect() as c:
            tablas = {t for (t,) in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if 'alembic_version' not in tablas:
                raise BaseNoUtilizable(f'{ruta} no es una base de la persistencia (sin revisión de esquema)')
            revision = c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar()
            if revision != esperada:
                raise BaseNoUtilizable(f'{ruta} tiene la revisión de esquema {revision}; este código necesita {esperada}')
            try:
                reconstruida = reconstruccion.salida_desde_base(c)
            except ValueError as e:
                raise BaseNoUtilizable(f'{ruta}: {e}') from e
            sustituciones = sustituciones_guardadas(c)
    except SQLAlchemyError as e:
        raise BaseNoUtilizable(f'No se puede leer {ruta}: {getattr(e, "orig", e)}') from e
    finally:
        motor.dispose()
    datos = {n: reconstruida[n] for n in DATOS}
    datos['originales'] = api.indexar(reconstruida['originales'])
    return datos, sustituciones


def cargar_desde_base(ruta, ambito):
    """Los datos que sirve la API, desde la base: el equivalente de api.cargar para el modo SQL."""
    datos, sustituciones = leer_base(ruta)
    try:
        return api.componer(datos, api.leer_ambito(ambito), sustituciones)
    except ValueError as e:
        raise BaseNoUtilizable(str(e)) from e
