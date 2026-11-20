# Translation fallback

A missing translation currently displays its internal key even when English has a translation. Fix `translate` so it first uses a translation in the requested locale, then the English translation, then the key itself. Existing translations, including empty strings, must be preserved. Unknown locales and unknown keys should work without errors. Change only the return expression in translator.py.
