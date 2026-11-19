import sqlite3

from shop.config import SETTINGS


def connect(path=None):
    return sqlite3.connect(path or SETTINGS["db_path"])
