"""Read VS Code JSONC without changing string contents."""
import json
import re


def loads(text):
    token = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')
    clean = token.sub(lambda m: m.group() if m.group().startswith('"') else " ", text)
    token = re.compile(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])')
    clean = token.sub(lambda m: "" if m.group() == "," else m.group(), clean)
    return json.loads(clean)
