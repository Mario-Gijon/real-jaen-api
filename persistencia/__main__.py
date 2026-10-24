"""Comandos de la persistencia (requieren requirements-sql.txt):

  python -m persistencia cargar --importacion CARPETA --base RUTA.sqlite
      Carga inicial de una importación verificada en una base nueva, fuera de los repositorios.
  python -m persistencia sustituciones --base RUTA.sqlite --archivo sustituciones.json --ambito ambito.json
      Incorpora las sustituciones locales, validadas con las reglas de la API sobre el ámbito indicado.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from persistencia.carga import CargaRechazada, cargar  # noqa: E402
from persistencia.decisiones import DecisionRechazada, cargar_sustituciones  # noqa: E402


def main():
    ap = argparse.ArgumentParser(prog='python -m persistencia', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='orden', required=True)
    a = sub.add_parser('cargar', help='carga inicial en una base nueva')
    a.add_argument('--importacion', type=Path, required=True)
    a.add_argument('--base', type=Path, required=True)
    s = sub.add_parser('sustituciones', help='incorpora las sustituciones locales')
    s.add_argument('--base', type=Path, required=True)
    s.add_argument('--archivo', type=Path, required=True)
    s.add_argument('--ambito', type=Path, required=True)
    args = ap.parse_args()
    comando = ' '.join(sys.argv)
    try:
        if args.orden == 'cargar':
            informe = cargar(args.importacion, args.base, comando)
        else:
            informe = cargar_sustituciones(args.base, args.archivo, args.ambito, comando)
    except (CargaRechazada, DecisionRechazada) as e:
        ap.exit(1, f'Rechazado: {e}\n')
    print(json.dumps(informe, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
