"""Project-local expert identities, independent of provider subscriptions."""
import copy
import json
import re

from project_room import write_json


class AgentProfiles:
    def __init__(self, runtime):
        self.runtime = runtime
        self.templates = {name: copy.deepcopy(cfg) for name, cfg in runtime.cfg['agents'].items() if cfg.get('managed')}
        self.items = {}
        self.path = None

    def load(self, identity):
        self.path = self.runtime.data / 'projects' / identity / 'agents.json'
        if self.path.exists():
            self.items = json.loads(self.path.read_text('utf-8'))
        else:
            self.items = {}
            for name, cfg in self.templates.items():
                selected = self.runtime.controls.saved.get(name, {})
                self.items[name] = {'id': name, 'template': name, 'name': cfg.get('label', name),
                    'color': cfg.get('color', '#8b5cf6'), 'background': '', 'archived': False,
                    'model': selected.get('model', cfg.get('model', '')),
                    'effort': selected.get('effort', cfg.get('effort', ''))}
            self.save()

    def save(self):
        write_json(self.path, self.items)

    def resolve(self, value):
        """Public handles are aliases; records, processes and worktrees keep their IDs."""
        if not isinstance(value, str):
            return value
        key = value.removeprefix('@').lower()
        if key in self.items:
            return key
        for identifier, profile in self.items.items():
            if key in [profile.get('handle', identifier), *profile.get('previous_handles', [])]:
                return identifier
        return value

    def recipient_arguments(self, arguments):
        result = dict(arguments)
        for key in ('owner', 'reviewer', 'assigned_to'):
            if result.get(key):
                result[key] = self.resolve(result[key])
        return result

    def config(self, profile):
        cfg = copy.deepcopy(self.templates[profile['template']])
        cfg.update(label=profile['name'], color=profile['color'], model=profile['model'],
                   effort=profile['effort'], background=profile['background'], profile=True,
                   handle=profile.get('handle', profile['id']),
                   previous_handles=list(profile.get('previous_handles', [])))
        return cfg

    def describe(self):
        providers = []
        for template, cfg in self.templates.items():
            catalog = next((self.runtime.controls.describe(name) for name, profile in self.items.items()
                            if profile['template'] == template and name in self.runtime.controls.catalogs),
                           {'models': [], 'selected': {'model': '', 'effort': ''}, 'error': 'Refresh connections to load models'})
            providers.append({'id': template, 'provider': cfg.get('provider', template),
                              'label': cfg.get('label', template), 'color': cfg.get('color', '#8b5cf6'),
                              'controls': catalog})
        return {'project': str(self.runtime.project or ''), 'profiles': [
                    {**p, 'handle': p.get('handle', p['id']), 'previous_handles': list(p.get('previous_handles', []))}
                    for p in self.items.values()], 'providers': providers,
                'paused': self.runtime.paused}

    def prepare(self, body, identifier=''):
        old = self.items.get(identifier) if identifier else None
        if identifier and old is None:
            raise ValueError('Agent profile was not found')
        template = body.get('template', old['template'] if old else '')
        if template not in self.templates:
            raise ValueError('Choose a configured provider connection')
        if old and template != old['template']:
            raise ValueError('Duplicate the agent to choose a different provider and keep its history intact')
        label = body.get('name', old['name'] if old else '').strip()
        if not label or len(label) > 60:
            raise ValueError('Give the agent a name between 1 and 60 characters')
        if not old:
            stem = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')[:32] or template + '-expert'
            if not stem[0].isalpha():
                stem = 'agent-' + stem
            identifier = body.get('id') or stem
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', identifier) or identifier in ('all', 'both', 'user', 'system', 'everyone'):
                raise ValueError('Mention handle must start with a letter and contain only lowercase letters, digits or hyphens')
            if self.resolve(identifier) != identifier or identifier in self.items or self.runtime.host.registry.get_instance(identifier):
                raise ValueError('That mention handle is already in use; choose another')
        handle = body.get('handle', old.get('handle', identifier) if old else identifier)
        if not isinstance(handle, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', handle) or handle in ('all', 'both', 'user', 'system', 'everyone'):
            raise ValueError('Mention handle must start with a letter and contain only lowercase letters, digits or hyphens')
        for other_id, other in self.items.items():
            if other_id != identifier and handle in [other_id, other.get('handle', other_id), *other.get('previous_handles', [])]:
                raise ValueError('That mention handle belongs to another agent, including its previous handles')
        if handle != identifier and self.runtime.host.registry.get_instance(handle):
            raise ValueError('That mention handle is already in use; choose another')
        previous_handles = list(old.get('previous_handles', [])) if old else []
        if old:
            previous = old.get('handle', identifier)
            if previous != handle and previous not in previous_handles:
                previous_handles.append(previous)
        previous_handles = [value for value in previous_handles if value != handle]
        color = body.get('color', old['color'] if old else self.templates[template].get('color', '#8b5cf6'))
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('Choose a six-digit hex color')
        background = body.get('background', old['background'] if old else '')
        if not isinstance(background, str) or len(background) > 16000:
            raise ValueError('Background must be text up to 16,000 characters')
        model = body.get('model', old['model'] if old else '')
        effort = body.get('effort', old['effort'] if old else '')
        # Cosmetic/personality edits remain possible while a provider is offline.
        if not old or (model, effort) != (old['model'], old['effort']):
            source = next((n for n, p in self.items.items() if p['template'] == template
                           and self.runtime.controls.catalogs.get(n, {}).get('models')), None)
            if source is None:
                raise ValueError('Refresh provider choices before choosing a model')
            self.runtime.controls.validate(source, model, effort)
        return {'id': identifier, 'handle': handle, 'previous_handles': previous_handles,
                'template': template, 'name': label, 'color': color.lower(),
                'background': background.strip(), 'model': model, 'effort': effort,
                'archived': old.get('archived', False) if old else False}

    def preferences(self, name, values):
        if name in self.items:
            self.items[name].update(values)
            self.save()
