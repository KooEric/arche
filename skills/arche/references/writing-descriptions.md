# Writing file and endpoint descriptions

`describe.py` writes `worksheets/<part>.md` to read and `descriptions/<part>.json` to fill in. Each key is a file path or `"METHOD /path"`, and each value is `{"name": "...", "description": "..."}`.

- Describe what the code does today. Read the file first; the worksheet lists only symbols and connections.
- Do not guess from a name. If you have not read a file, leave its entry empty. The map shows "No description" for it.
- `name` is two to four words a reader would use for the file: "Order list screen". `description` is one sentence: who calls it, what it does, what comes out.
- With `"audience": "everyone"`, use everyday words and leave out library names unless the reader needs them.
- Leave credentials, tokens and copied source out of descriptions.
- With more than about 150 files, work one part at a time. Run `describe.py` again to list the entries that are still empty, then run `map.py` to see the fill rate.
