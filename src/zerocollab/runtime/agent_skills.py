"""Role cores and on-demand skills (Docs/agent-profiles.md r3, task b8bc48b6).

An approved profile record carries `core`, `core_version`, `approved_by`, `approved_at` and `skills`:
[{name, sha256}], pinned when Zero approves it in Team builder. Skills live in agent-skills/<name>/ in the
room's source tree. Only pinned, unchanged skills reach an agent: through a skills-only Claude plugin, a
Codex extra skills root, and room_skill for every provider. Nothing here grants tools or changes settings.
"""
import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from zerocollab.paths import ROOT
LIBRARY = ROOT / 'agent-skills'
MAX_SKILL_CHARS = 16000        # one SKILL.md body, as room_skill returns it
MAX_SKILL_FILES = 12           # SKILL.md plus approved extra files
MAX_DESCRIPTION_CHARS = 240    # the always-in-context "use when" line
MAX_CORE_WORDS = 400
PLUGIN_NAME = 'zerocollab-skills'
MANIFEST = '.claude-plugin/plugin.json'
MANIFEST_KEYS = {'name', 'version', 'description'}  # inline hooks, mcpServers, commands or agents are refused
NAME = re.compile(r'[a-z][a-z0-9-]{0,63}')
FRONTMATTER = re.compile(r'\A---\n(.*?)\n---\n?(.*)\Z', re.S)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    version: str
    sha256: str
    files: tuple  # (posix path inside the skill folder, sha256) for every file
    body: str


def digest(data):
    """Line-ending neutral, so a checkout's CRLF conversion doesn't break an approval pin."""
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def front_matter(text):
    """Flat `key: value` frontmatter; `[a, b]` values become lists. Anything nested is refused."""
    match = FRONTMATTER.match(text.replace('\r\n', '\n'))
    if not match:
        raise ValueError('missing --- frontmatter')
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(':')
        if not sep or not re.fullmatch(r'[a-z_]+', key.strip()):
            raise ValueError(f'unreadable frontmatter line: {line[:60]}')
        value = value.strip()
        if value.startswith('[') and value.endswith(']'):
            value = [item.strip().strip('"\'') for item in value[1:-1].split(',') if item.strip()]
        else:
            value = value.strip('"\'')
        fields[key.strip()] = value
    return fields, match.group(2)


def linked(path):
    return path.is_symlink() or path.is_junction()


def read_skill(name, library=LIBRARY):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('not a skill name')
    folder = library / name
    if linked(folder) or not (folder / 'SKILL.md').is_file():
        raise ValueError('not in the skill library')
    files = []
    for path in sorted(folder.rglob('*')):
        if linked(path):
            raise ValueError('skill files may not be links')
        if path.is_file():
            files.append((path.relative_to(folder).as_posix(), digest(path.read_bytes())))
    if len(files) > MAX_SKILL_FILES:
        raise ValueError(f'more than {MAX_SKILL_FILES} files')
    fields, body = front_matter((folder / 'SKILL.md').read_text('utf-8'))
    description = fields.get('description', '')
    if fields.get('name') != name:
        raise ValueError('frontmatter name does not match its folder')
    if not isinstance(description, str) or not description.lower().startswith('use when') \
            or len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f'description must start "Use when" and fit {MAX_DESCRIPTION_CHARS} characters')
    if len(body) > MAX_SKILL_CHARS:
        raise ValueError(f'body over {MAX_SKILL_CHARS} characters')
    combined = digest('\n'.join(f'{path} {sha}' for path, sha in files).encode())
    return Skill(name, description, str(fields.get('version', '')), combined, tuple(files), body.strip())


def active(profile):
    """A core counts only once the server recorded Zero's approval on the profile record (security 3a00bef2
    A2). approved_by and approved_at in a drafted core.md are never imported, so a file can't approve itself."""
    return bool(isinstance(profile.get('core'), str) and profile['core'].strip()
                and profile.get('approved_by') and profile.get('approved_at'))


def approved(profile, library=LIBRARY):
    """The profile's pinned skills that still match their approval, and why any others were withheld."""
    skills, withheld = [], []
    for pin in profile.get('skills') or []:
        name, pinned = (pin.get('name', ''), pin.get('sha256', '')) if isinstance(pin, dict) else (str(pin), '')
        try:
            skill = read_skill(name, library)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            withheld.append(f'{name}: {exc}')
            continue
        if not pinned or skill.sha256 != pinned:
            withheld.append(f'{name}: changed since approval')
            continue
        skills.append(skill)
    return skills, withheld


def instructions(profile, skills):
    """Role core plus the skill index. Versions are in the text, so instruction_hash, and with it session
    resume, changes only when an approved core or skill version changes."""
    text = (f"\nYour role core (version {profile.get('core_version', '')}, approved by "
            f"{profile.get('approved_by') or 'the user'} on {profile.get('approved_at', '')}):\n"
            + profile['core'].strip())
    if skills:
        text += ('\n\nYour skills. Before work that matches a skill\'s "use when", load its full procedure '
                 'with your native skill tool or room_skill(action="load", name=...) and follow it:\n'
                 + '\n'.join(f'- {s.name} (v{s.version}): {s.description}' for s in skills))
    return text


def versions(profile, skills, withheld=()):
    """Recorded in each turn's runtime_config event, so health can tie behaviour to exact versions."""
    return {'core_version': profile.get('core_version', ''),
            'core_sha256': digest(profile.get('core', '').encode('utf-8'))[:16],
            'skills': [{'name': s.name, 'version': s.version, 'sha256': s.sha256[:16]} for s in skills],
            'withheld': list(withheld)}


def plugin_folder(data, agent, profile, skills):
    key = digest(json.dumps([[s.name, s.sha256] for s in skills]).encode())[:12]
    return Path(data) / 'agent-skills' / agent / f"{profile.get('core_version') or 0}-{key}"


def check_plugin(folder, skills):
    """'' when the folder holds exactly the manifest and these skills' files at their pinned hashes."""
    if not folder.is_dir() or linked(folder):
        return 'not built'
    pinned = {f'skills/{s.name}/{path}': sha for s in skills for path, sha in s.files}
    found = set()
    for path in folder.rglob('*'):
        relative = path.relative_to(folder).as_posix()
        if linked(path):
            return f'link at {relative}'
        if not path.is_file():
            continue
        found.add(relative)
        if relative == MANIFEST:
            continue
        if relative not in pinned:
            return f'unexpected file {relative}'
        if digest(path.read_bytes()) != pinned[relative]:
            return f'{relative} differs from its approval'
    missing = ({MANIFEST} | set(pinned)) - found
    if missing:
        return f'missing {sorted(missing)[0]}'
    try:
        manifest = json.loads((folder / MANIFEST).read_text('utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return 'manifest unreadable'
    if not isinstance(manifest, dict) or set(manifest) - MANIFEST_KEYS:
        return 'manifest declares more than name, version and description'
    return ''


def build_plugin(folder, skills, library=LIBRARY):
    """Write the skills-only plugin unless an identical one is there. Returns '' or why it can't be used.
    Builds beside the target and renames, so a concurrent run never reads a half-written folder."""
    if not check_plugin(folder, skills):
        return ''
    temp = folder.with_name(f'{folder.name}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        (temp / '.claude-plugin').mkdir(parents=True)
        (temp / MANIFEST).write_text(json.dumps({'name': PLUGIN_NAME, 'version': folder.name,
            'description': 'Approved room skills for this agent (generated; do not edit)'}), 'utf-8')
        for skill in skills:
            for path, _ in skill.files:
                target = temp / 'skills' / skill.name / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((library / skill.name / path).read_bytes())
        if folder.exists():
            shutil.rmtree(folder)
        temp.rename(folder)
    except OSError as exc:
        if check_plugin(folder, skills):  # another run may have finished the same folder first
            return f'build failed: {exc}'
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    # Checked again after the copy: a library file edited between read and copy fails its pin here.
    return check_plugin(folder, skills)


def load(profile_skills, name, file='', library=LIBRARY):
    """room_skill load: a body by name from the agent's approved list only, never by path. `file` picks one
    of that skill's listed extra files, re-checked against its pin and bounded like a body."""
    allowed = {s.name: s for s in profile_skills}
    if name not in allowed:
        listed = ', '.join(sorted(allowed)) or 'none'
        raise ValueError(f'{name!r} is not one of your skills. Your skills: {listed}')
    skill = allowed[name]
    extras = {path: sha for path, sha in skill.files if path != 'SKILL.md'}
    if not file:
        return {'name': skill.name, 'version': skill.version, 'description': skill.description,
                'body': skill.body, 'files': sorted(extras)}
    if file not in extras:
        raise ValueError(f'{file!r} is not a file of {name}. Its files: {", ".join(sorted(extras)) or "none"}')
    data = (library / name / file).read_bytes()
    if digest(data) != extras[file]:
        raise ValueError(f'{name}/{file} changed since approval')
    text = data.decode('utf-8', errors='replace')
    return {'name': skill.name, 'file': file, 'text': text[:MAX_SKILL_CHARS], 'truncated': len(text) > MAX_SKILL_CHARS}


def listing(profile_skills):
    """room_skill list: names and "use when" lines only."""
    return [{'name': s.name, 'version': s.version, 'description': s.description} for s in profile_skills]
