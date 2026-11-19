import importlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.types import Config

try:
    import ujson as json
except ImportError:
    import json

PLUGIN_NAME = "app.plugins.alpha"


def load_static():
    return importlib.import_module("app.plugins.alpha")


def load_named(name):
    return importlib.import_module(name)


def load_builtin(name):
    return __import__(name)


def lazy():
    from app import plugins

    return plugins, sys, json
