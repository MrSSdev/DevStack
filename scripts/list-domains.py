#!/usr/bin/env python3
import json
from pathlib import Path
import re

print('devstack.test')
path = Path('/var/lib/devstack/projects.json')
if path.exists():
    state = json.loads(path.read_text(encoding='utf-8'))
    for project in state.values():
        domain = project.get('domain', '')
        if isinstance(domain, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,39}\.test', domain) and domain != 'devstack.test':
            print(domain)
