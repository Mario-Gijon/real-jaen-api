"""Todo lo específico de SQLite en la persistencia (docs/persistencia-sqlite.md, 5.2). Los modelos y los comandos usan
estas funciones en lugar de escribir PRAGMA, transacciones o SQL propio del motor.

SQLAlchemy no hace portable lo que hay aquí: cambiar de motor exigiría reescribir este módulo, revisar las migraciones
(modo batch) y validar la equivalencia de los datos.
"""
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text

# Espera ante un bloqueo de otro proceso antes de fallar (milisegundos).
ESPERA_BLOQUEO_MS = 5000


class ClavesForaneasDesactivadas(RuntimeError):
    """La conexión no comprueba claves foráneas: no se usa."""


def url(ruta, solo_lectura=False):
    """URL de SQLAlchemy para un archivo. En solo lectura se abre con URI `mode=ro`: el archivo debe existir."""
    ruta = Path(ruta).resolve()
    if solo_lectura:
        return f'sqlite:///{ruta.as_uri()}?mode=ro&uri=true'
    return f'sqlite:///{ruta.as_posix()}'


def preparar_conexion(conexion_dbapi, solo_lectura):
    """Cada conexión nueva: el driver no abre transacciones por su cuenta (las abre `al_empezar`), claves foráneas
    activadas y comprobadas, espera ante bloqueos y, para escribir, diario WAL (un lector no bloquea al escritor)."""
    conexion_dbapi.isolation_level = None
    cursor = conexion_dbapi.cursor()
    try:
        cursor.execute('PRAGMA foreign_keys = ON')
        cursor.execute(f'PRAGMA busy_timeout = {ESPERA_BLOQUEO_MS}')
        if not solo_lectura:
            cursor.execute('PRAGMA journal_mode = WAL')
        if cursor.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise ClavesForaneasDesactivadas('SQLite no ha activado las claves foráneas en esta conexión')
    finally:
        cursor.close()


def crear_motor(ruta, solo_lectura=False):
    """Motor de SQLAlchemy para una base SQLite. Escritura: cada transacción es BEGIN IMMEDIATE (un único escritor,
    que toma el bloqueo al empezar y no a mitad). Lectura: BEGIN normal sobre una conexión `mode=ro`."""
    motor = create_engine(url(ruta, solo_lectura))

    @event.listens_for(motor, 'connect')
    def al_conectar(conexion_dbapi, _registro):
        preparar_conexion(conexion_dbapi, solo_lectura)

    @event.listens_for(motor, 'begin')
    def al_empezar(conexion):
        conexion.exec_driver_sql('BEGIN' if solo_lectura else 'BEGIN IMMEDIATE')

    return motor


def claves_foraneas_activas(conexion):
    """1 si la conexión comprueba claves foráneas (para pruebas y verificaciones de arranque)."""
    return conexion.exec_driver_sql('PRAGMA foreign_keys').scalar()


def comprobar_integridad(conexion):
    """Filas que violan claves foráneas (PRAGMA foreign_key_check); lista vacía si ninguna."""
    return conexion.exec_driver_sql('PRAGMA foreign_key_check').fetchall()


@contextmanager
def transaccion_de_escritura(motor):
    """Una transacción de escritura completa: confirma al terminar sin error y deshace todo si falla."""
    with motor.begin() as conexion:
        yield conexion


# ─── Expresiones SQL propias del dialecto, para las restricciones de los modelos ───────────────────────────────

def json_valido(columna, nulo=True):
    """CHECK de un literal JSON guardado como texto (extensión JSON1). `nulo`: NULL de SQL (ausente) admitido."""
    return f'{columna} IS NULL OR json_valid({columna})' if nulo else f'json_valid({columna})'


def indice_parcial(condicion):
    """Argumentos de un índice parcial (único sólo para las filas que cumplen la condición)."""
    return {'sqlite_where': text(condicion)}


def hexadecimal(columna, longitud):
    """CHECK de una huella hexadecimal en minúsculas de longitud fija."""
    return f"length({columna}) = {longitud} AND {columna} NOT GLOB '*[^0-9a-f]*'"


def texto_no_vacio(columna):
    return f"length(trim({columna})) > 0"
