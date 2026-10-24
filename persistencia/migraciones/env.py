"""Entorno de Alembic. La base se indica siempre (`-x base=RUTA` o, desde código, `config.attributes['base']`); nunca
hay una por defecto. La conexión es la de persistencia.sqlite: claves foráneas comprobadas y BEGIN IMMEDIATE.
Modo batch: SQLite no admite la mayoría de ALTER TABLE y Alembic recrea la tabla (específico de SQLite)."""
from logging.config import fileConfig

from alembic import context

from persistencia.modelos import Base
from persistencia.sqlite import crear_motor

config = context.config
if config.config_file_name is not None and config.attributes.get('configurar_registro', True):
    fileConfig(config.config_file_name)


def ruta_de_la_base():
    ruta = config.attributes.get('base') or context.get_x_argument(as_dictionary=True).get('base')
    if not ruta:
        raise SystemExit('Indica la base: alembic -x base=RUTA ... (no hay base por defecto)')
    return ruta


def ejecutar():
    if context.is_offline_mode():
        raise SystemExit('El modo offline (SQL sin conexión) no se usa en este proyecto')
    motor = crear_motor(ruta_de_la_base())
    try:
        with motor.connect() as conexion:
            context.configure(connection=conexion, target_metadata=Base.metadata, render_as_batch=True,
                              compare_type=True, compare_server_default=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        motor.dispose()


ejecutar()
