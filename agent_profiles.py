"""Project-local expert identities, independent of provider subscriptions."""
import copy
import hashlib
import json
import re
from pathlib import Path

import agent_skills
from project_room import write_json
from provider_controls import ProviderControls
from provider_store import ProviderStore

# Role cores (Docs/agent-profiles.md, task b8bc48b6): drafted as agent-profiles/<agent>/core.md, reviewed, then
# imported into the profile record only by the user's approve action. The record is what delivery reads.
CORE_LIBRARY = Path(__file__).resolve().parent / 'agent-profiles'
CORE_FILE = 'core.md'
CORE_SECTIONS = ('Expertise', 'Responsibilities', 'Boundaries', 'Quality bars')
CORE_HEADING = re.compile(r'^##\s+(.+?)\s*$', re.M)
# Set only by approve_core, from the user's own action (security 3a00bef2 A2): never from core.md, a profile
# edit or an agent's tool call, and cleared together by revoke_core.
CORE_FIELDS = ('core', 'core_version', 'skills', 'approved_by', 'approved_at')
APPROVER = 'user'
# A release grant is bound to one permanent profile (security B2, Astra cd672b82): changing what identifies it,
# archiving or replacing it lapses the grant for good, even if changed back. Model and effort edits do not.
GRANT_IDENTITY_FIELDS = ('name', 'handle', 'template')


def draft_digest(draft):
    """What the user reviewed: the core text, its version and each skill's pinned hash."""
    return hashlib.sha256(json.dumps(draft, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


class AgentProfiles:
    def __init__(self, runtime):
        self.runtime = runtime
        # config.toml templates with Team > Providers additions and edits layered over them.
        self.store = ProviderStore(runtime)
        self.templates = self.store.templates()
        # Model lists for connections no agent uses yet, so Add agent can offer them.
        self.template_catalogs = {}
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
            unused = self.template_catalogs.get(template)
            fallback = ({**unused, 'selected': {'model': cfg.get('model', ''), 'effort': ''}} if unused else
                        {'models': [], 'selected': {'model': '', 'effort': ''}, 'error': 'Refresh connections to load models'})
            catalog = next((self.runtime.controls.describe(name) for name, profile in self.items.items()
                            if profile['template'] == template and name in self.runtime.controls.catalogs), fallback)
            providers.append({'id': template, 'provider': cfg.get('provider', template),
                              'label': cfg.get('label', template), 'color': cfg.get('color', '#8b5cf6'),
                              'controls': catalog, 'offered': self.store.offered(template)})
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
        if not old and not self.store.offered(template):
            raise ValueError('API provider turned off. Turn it on in Team > Providers to add agents on it')
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
            if source is not None:
                self.runtime.controls.validate(source, model, effort)
            elif self.template_catalogs.get(template, {}).get('models'):
                # The first agent on a connection: check against the connection's own model list.
                ProviderControls.check(self.template_catalogs[template], model, effort)
            else:
                raise ValueError('Refresh provider choices before choosing a model')
        return {'id': identifier, 'handle': handle, 'previous_handles': previous_handles,
                'template': template, 'name': label, 'color': color.lower(),
                'background': background.strip(), 'model': model, 'effort': effort,
                'archived': old.get('archived', False) if old else False}

    def core_draft(self, name, library=CORE_LIBRARY, skills_library=agent_skills.LIBRARY):
        """agent-profiles/<name>/core.md parsed for the user's review: the body, its version and each listed
        skill's current pin, with the digest approve_core must be given back. approved_by and approved_at in
        the file are ignored; only approve_core sets them."""
        if name not in self.items:
            raise ValueError('Agent profile was not found')
        try:
            text = (library / name / CORE_FILE).read_text('utf-8')
        except FileNotFoundError:
            raise ValueError(f'No drafted core at agent-profiles/{name}/{CORE_FILE}') from None
        fields, body = agent_skills.front_matter(text)
        if fields.get('agent') != name:
            raise ValueError(f"{CORE_FILE} names agent {fields.get('agent')!r}, not {name!r}")
        version = fields.get('version', '')
        if not (isinstance(version, str) and version.isdigit() and int(version) >= 1):
            raise ValueError('version must be a whole number from 1')
        body = body.strip()
        words = len(body.split())
        if words > agent_skills.MAX_CORE_WORDS:
            raise ValueError(f'The core is {words} words; keep it to {agent_skills.MAX_CORE_WORDS}')
        if CORE_HEADING.findall(body) != list(CORE_SECTIONS):
            raise ValueError('The core needs exactly these ## sections, in order: ' + ', '.join(CORE_SECTIONS))
        names = fields.get('skills') or []
        if not isinstance(names, list):
            raise ValueError('skills must be a [list] of skill names')
        skills = []
        for skill_name in dict.fromkeys(names):
            try:
                skill = agent_skills.read_skill(skill_name, skills_library)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f'Skill {skill_name}: {exc}') from None
            skills.append(skill)
        draft = {'core': body, 'core_version': int(version),
                 'skills': [{'name': s.name, 'sha256': s.sha256} for s in skills]}
        return {**draft, 'words': words, 'digest': draft_digest(draft),
                'skill_details': [{'name': s.name, 'version': s.version, 'description': s.description}
                                  for s in skills]}

    def core_state(self, name):
        """The approved core on record, if any, and whether delivery uses it."""
        if name not in self.items:
            raise ValueError('Agent profile was not found')
        profile = self.items[name]
        return {**{field: profile.get(field) for field in CORE_FIELDS}, 'active': agent_skills.active(profile)}

    def approve_core(self, name, digest, approved_at, **libraries):
        """The user's approval: re-reads the draft and refuses unless it is exactly what was reviewed, then
        records it with approved_by and approved_at. The agent's next run uses it (a deliberate session reset)."""
        draft = self.core_draft(name, **libraries)
        if not isinstance(digest, str) or digest != draft['digest']:
            raise ValueError('The drafted core or one of its skills changed since you reviewed it; review it again')
        self.items[name].update({field: draft[field] for field in ('core', 'core_version', 'skills')},
                                approved_by=APPROVER, approved_at=approved_at)
        self.items[name].pop('core_dismissed', None)
        self.save()
        return self.core_state(name)

    def revoke_core(self, name, **libraries):
        """Back to the free-text background from the agent's next run. The current draft is recorded as answered
        (core_dismissed), so its bell item stays closed until a different draft is published (b0eaf89e)."""
        if name not in self.items:
            raise ValueError('Agent profile was not found')
        for field in CORE_FIELDS:
            self.items[name].pop(field, None)
        try:
            self.items[name]['core_dismissed'] = self.core_draft(name, **libraries)['digest']
        except (OSError, ValueError):
            self.items[name].pop('core_dismissed', None)  # no approvable draft, so no item to keep closed
        self.save()
        return self.core_state(name)

    @staticmethod
    def grant_identity_changed(old, new):
        """Whether an edit changes what the profile's release grant is bound to (its handle defaults to its id)."""
        return any(old.get(field, old['id'] if field == 'handle' else None)
                   != new.get(field, new['id'] if field == 'handle' else None) for field in GRANT_IDENTITY_FIELDS)

    def lapse_release_grant(self, name):
        """Before the edit is applied, so a failure refuses the edit rather than leaving a grant on a changed
        profile. Grant storage and its user-only enable and revoke are the release manager's."""
        grant = getattr(getattr(self.runtime, 'deployment', None), 'release_grant', None)
        if grant is not None:
            grant.invalidate_profile(self.runtime.identity, name)

    def preferences(self, name, values):
        if name in self.items:
            self.items[name].update(values)
            self.save()
