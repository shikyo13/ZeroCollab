"""Regenerate the generated tables in Docs/ from the source tree.

  python Docs/refresh_tables.py

Rewrites only the text between `<!-- generated:NAME -->` and `<!-- /generated -->`
markers in file-index.md and dependency-map.md; everything else is hand-written.
Python facts come from `ast` (module docstrings, imports at any depth), frontend
facts from each file's leading /* ... */ header, script order from static/index.html.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / 'Docs'
MARKER = re.compile(r'(<!-- generated:([\w-]+) -->\n).*?(<!-- /generated -->)', re.S)


def clean(text):
    """One table cell: no pipes or em dashes, whole sentences up to about 240 characters."""
    text = ' '.join(text.replace(chr(0x2014), '-').replace(chr(0x2013), '-').replace('|', '/').split())
    if len(text) > 240 and '. ' in text[:240]:
        text = text[:text[:240].rindex('. ') + 1]
    return text or '-'


def python_facts():
    modules = {p.stem: p for p in sorted(ROOT.glob('*.py'))}
    stdlib = set(sys.stdlib_module_names)
    facts = {}
    for name, path in modules.items():
        source = path.read_text('utf-8')
        tree = ast.parse(source)
        internal, external = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split('.')[0]]
            else:
                continue
            for dep in names:
                if dep in modules:
                    internal.add(dep)
                elif dep not in stdlib:
                    external.add(dep)
        doc = (ast.get_docstring(tree) or '').strip().split('\n\n')[0]
        facts[name] = {'lines': len(source.splitlines()), 'doc': doc,
                       'imports': sorted(internal - {name}), 'external': sorted(external)}
    return facts


def frontend_facts():
    facts = {}
    src = ROOT / 'frontend' / 'src'
    for path in sorted(src.rglob('*'), key=lambda p: p.as_posix().lower()):
        if path.suffix in ('.ts', '.tsx', '.css'):
            text = path.read_text('utf-8')
            head = re.match(r'\s*/\*+(.*?)\*/', text, re.S)
            facts[path.relative_to(src).as_posix()] = {
                'lines': len(text.splitlines()), 'doc': head.group(1).replace('*', ' ') if head else ''}
    return facts


def table(header, rows):
    return '\n'.join([header, '|' + '-|' * header.count('|', 1)] + rows) + '\n'


def sections():
    py, fe = python_facts(), frontend_facts()
    importers, third_party = {}, {}
    for name, m in py.items():
        for dep in m['imports']:
            importers.setdefault(dep, []).append(name)
        for pkg in m['external']:
            third_party.setdefault(pkg, []).append(name)
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', (ROOT / 'static' / 'index.html').read_text('utf-8'))
    tests = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / 'tests').rglob('*.py'))
    return {
        'python-files': table('| Module | Lines | Purpose (module docstring) |',
                              [f"| `{n}.py` | {m['lines']} | {clean(m['doc'])} |" for n, m in py.items()]),
        'frontend-files': table('| File | Lines | Purpose (file header) |',
                                [f"| `{n}` | {m['lines']} | {clean(m['doc'])} |" for n, m in fe.items()]),
        'python-imports': table('| Module | Imports (internal) | Third-party |',
                                [f"| `{n}` | {', '.join(m['imports']) or '-'} | {', '.join(m['external']) or '-'} |"
                                 for n, m in py.items()]),
        'python-importers': table('| Module | Imported by |',
                                  [f"| `{n}` | {', '.join(sorted(importers[n]))} |" for n in py if n in importers]),
        'third-party': table('| Package | Used by |',
                             [f"| `{p}` | {', '.join(sorted(u))} |" for p, u in sorted(third_party.items())]),
        'scripts': '\n'.join(f"{i}. `{s.split('?')[0]}`" for i, s in enumerate(scripts, 1)) + '\n',
        'tests': '\n'.join(f'- `{t}`' for t in tests) + '\n',
    }


def main():
    generated = sections()
    for doc in ('file-index.md', 'dependency-map.md'):
        path = DOCS / doc
        text = path.read_text('utf-8')
        unknown = [m.group(2) for m in MARKER.finditer(text) if m.group(2) not in generated]
        if unknown:
            raise SystemExit(f'{doc}: unknown generated section(s) {unknown}')
        path.write_text(MARKER.sub(lambda m: m.group(1) + generated[m.group(2)] + m.group(3), text), 'utf-8', newline='\n')
        print(f'updated {doc}')


if __name__ == '__main__':
    main()
