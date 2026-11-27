"""CI-only adaptation of bubblewrap 0.13.1-1 Debian packaging to compat 13.

No source or build/test flags are changed. Fail closed on unexpected stanzas.
"""
from pathlib import Path
import re
import sys


def add_substvars(stanza, field, variables):
    pattern = re.compile(r'^' + re.escape(field) + r':([^\n]*(?:\n[ \t]+[^\n]*)*)', re.M)
    found = list(pattern.finditer(stanza))
    if len(found) > 1:
        raise ValueError('Duplicate control field: ' + field)
    if not found:
        return stanza.replace('Package: bubblewrap\n', 'Package: bubblewrap\n' +
                              field + ': ' + ', '.join(variables) + '\n', 1)
    match = found[0]
    old = match.group(1).strip()
    missing = [v for v in variables if v not in old]
    if not missing:
        return stanza
    value = ', '.join(missing) + (', ' + old if old else '')
    return stanza[:match.start()] + field + ': ' + value + stanza[match.end():]


def adapt_control(original):
    sections = re.split(r'\n[ \t]*\n', original.rstrip())
    if (len(sections) != 2 or not sections[0].startswith('Source: bubblewrap\n')
            or not sections[1].startswith('Package: bubblewrap\n')):
        raise ValueError('Unexpected source or binary package stanza layout')
    if original.count('debhelper-compat (= 14)') != 1 or 'debhelper-compat (= 14)' not in sections[0]:
        raise ValueError('Unexpected source packaging; review the compat change')
    sections[0] = sections[0].replace('debhelper-compat (= 14)', 'debhelper-compat (= 13)')
    sections[1] = add_substvars(sections[1], 'Depends',
                                ['${shlibs:Depends}', '${misc:Depends}'])
    sections[1] = add_substvars(sections[1], 'Pre-Depends', ['${misc:Pre-Depends}'])
    return '\n\n'.join(sections) + '\n'


def main():
    if len(sys.argv) != 1:
        raise SystemExit('Run only in the extracted source tree')
    path = Path('debian/control')
    path.write_text(adapt_control(path.read_text(encoding='utf-8')), encoding='utf-8')


if __name__ == '__main__':
    main()
