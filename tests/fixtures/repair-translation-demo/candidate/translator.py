"""Translations for the pickup interface."""

CATALOGS = {
    'en': {'ready': 'Ready for pickup', 'undo': 'Undo pickup', 'empty': ''},
    'ko': {'ready': '수령 준비'},
    'fr': {'ready': 'Prêt'},
}


def translate(locale, key):
    translated = CATALOGS.get(locale, {})
    english = CATALOGS['en']
    return translated.get(key, english.get(key, key))
