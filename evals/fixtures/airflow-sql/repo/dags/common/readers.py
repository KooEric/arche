import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def read_sql(name):
    with open(os.path.join(ROOT, name)) as handle:
        return handle.read()
