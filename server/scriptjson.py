"""JSON that is safe to place inside an HTML <script> element.

json.dumps does not escape "</script>", so any string a caller controls -- a strategy name,
a rule label that becomes an exit reason -- could close the script element and open a new
one on our origin (stored XSS, security review 13 Nov 2026). Escaping <, > and & as \\u
sequences keeps the JSON value identical while making it impossible to end the element or
start a comment; U+2028/2029 are escaped because older JS engines treat them as newlines.
Every JSON-in-<script> in the server goes through here.
"""
import json

_ESC = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", " ": "\\u2028", " ": "\\u2029"}
_TABLE = str.maketrans(_ESC)


def script_json(obj, **kwargs):
    return json.dumps(obj, **kwargs).translate(_TABLE)
