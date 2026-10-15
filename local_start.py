"""Start a built-in local model server from Team > Providers (task edd41ff3).

Ollama and LM Studio start with their own installed app or CLI. llama.cpp and MLX LM need a model, so the
user saves the full start command once (the llama-server path, the model .gguf and any flags) and Start runs it.
A server is started hidden, with its output in data/local-servers/<server>.log, and Start waits until it answers
on its port, so the following refresh loads its models and unblocks its agents."""
import asyncio
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

START_WAIT_S = 120     # a 27B model can take a minute to load on a single GPU
START_POLL_S = 1.0
COMMAND_LIMIT = 2000   # characters in a saved start command
LOG_TAIL_BYTES = 1200  # how much of the log a failed start quotes
SAVED = {'llamacpp', 'mlx'}  # servers that need a model, so their start command is the user's
STARTING = {}          # server key -> asyncio.Lock, so two clicks start one server


def ollama_command():
    """The installed Ollama app (it runs the server and its tray icon), else `ollama serve`."""
    if sys.platform == 'win32':
        app = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Ollama/ollama app.exe'
        if app.is_file():
            return [str(app)]
    elif sys.platform == 'darwin' and Path('/Applications/Ollama.app').exists():
        return ['open', '-a', 'Ollama']
    found = shutil.which('ollama')
    return [found, 'serve'] if found else None


def lms_command():
    found = shutil.which('lms') or next((str(p) for p in (Path.home() / '.lmstudio/bin/lms.exe', Path.home() / '.lmstudio/bin/lms')
                                         if p.is_file()), None)
    return [found, 'server', 'start'] if found else None


def parse_command(text):
    """The saved command as an argument list. The first word must be an existing program, so a typo is refused
    when it is saved, not when Start is pressed."""
    if not isinstance(text, str) or len(text) > COMMAND_LIMIT:
        raise ValueError(f'Use a command of at most {COMMAND_LIMIT} characters')
    try:
        words = shlex.split(text, posix=sys.platform != 'win32')
    except ValueError as exc:
        raise ValueError(f'The command could not be read: {exc}') from None
    if sys.platform == 'win32':  # posix=False keeps the quotes; Windows programs get the plain argument
        words = [w[1:-1] if len(w) > 1 and w[0] == w[-1] == '"' else w for w in words]
    if not words:
        raise ValueError('Enter the command that starts the server')
    program = Path(words[0])
    if not (program.is_file() or shutil.which(words[0])):
        raise ValueError(f'{words[0]} was not found. Use the full path to the server program')
    for flag, value in zip(words, words[1:]):
        if flag in ('-m', '--model') and value.lower().endswith('.gguf') and not Path(value).is_file():
            raise ValueError(f'The model {value} was not found. Use the full path to the .gguf file')
    return words


def spec(key, saved):
    """What Start would run for this server: {available, command, saved, detail}, plus argv for start()."""
    if key in SAVED:
        if saved:
            try:
                return {'available': True, 'command': saved, 'saved': True, 'detail': '', 'argv': parse_command(saved)}
            except ValueError as exc:
                return {'available': False, 'command': saved, 'saved': True, 'detail': f'The saved start command no longer works: {exc}'}
        return {'available': False, 'command': '', 'saved': False,
                'detail': 'Save a start command: the full path to llama-server, then -m and the model .gguf, '
                          'and --port 8080 --jinja. Start then runs it.'}
    argv = ollama_command() if key == 'ollama' else lms_command() if key == 'lmstudio' else None
    if argv:
        return {'available': True, 'command': subprocess.list2cmdline(argv), 'saved': False, 'detail': '', 'argv': argv}
    return {'available': False, 'command': '', 'saved': False, 'detail': 'Install it first; ZeroCollab did not find it in the usual places.'}


def public(start):
    return {k: v for k, v in start.items() if k != 'argv'}


def launch(argv, log_path):
    """Run the server hidden and detached, with its output in log_path (started fresh each time)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, 'wb') as log:
        log.write(f'Started {time.strftime("%Y-%m-%d %H:%M:%S")} from ZeroCollab Team > Providers:\n'
                  f'{subprocess.list2cmdline(argv)}\n\n'.encode('utf-8', 'replace'))
        log.flush()
        options = {'stdin': subprocess.DEVNULL, 'stdout': log, 'stderr': subprocess.STDOUT, 'cwd': str(Path(argv[0]).parent)
                   if Path(argv[0]).is_absolute() else None}
        if sys.platform == 'win32':
            hidden = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                # Outlive the room: a desktop job object would otherwise end the server when the room closes.
                return subprocess.Popen(argv, creationflags=hidden | 0x01000000, **options)  # CREATE_BREAKAWAY_FROM_JOB
            except OSError:
                return subprocess.Popen(argv, creationflags=hidden, **options)  # the job does not allow breakaway
        return subprocess.Popen(argv, start_new_session=True, **options)


def log_tail(log_path):
    try:
        with open(log_path, 'rb') as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - LOG_TAIL_BYTES))
            return log.read().decode('utf-8', 'replace').strip()
    except OSError:
        return ''


async def answers(base_url):
    async with httpx.AsyncClient(timeout=httpx.Timeout(2, connect=1), trust_env=False) as client:
        try:
            response = await client.get(base_url.rstrip('/') + '/models')
            return response.status_code == 200
        except httpx.HTTPError:
            return False


async def start(runtime, template):
    """Start this built-in's server unless it already answers, wait until it does, then refresh.
    Returns (status, body) for the route."""
    import provider_setup
    store = runtime.profiles.store
    info = store.local_servers().get(template)
    if info is None:
        return 404, {'error': 'That local provider is not available on this computer'}
    if runtime.deployment and runtime.deployment.rehearsal:
        return 409, {'error': 'Starting servers is disabled in a rehearsal preview'}
    key = info['server']
    lock = STARTING.setdefault(key, asyncio.Lock())
    async with lock:
        if not await answers(info['base_url']):
            start = spec(key, store.entries.get(template, {}).get('local_start', ''))
            if not start['available']:
                return 400, {'error': start['detail'], 'field': 'start'}
            log_path = runtime.data / 'local-servers' / f'{key}.log'
            try:
                process = await asyncio.to_thread(launch, start['argv'], log_path)
            except OSError as exc:
                return 400, {'error': f'{info["label"]} could not be started: {exc}', 'field': 'start'}
            deadline = time.monotonic() + START_WAIT_S
            while not await answers(info['base_url']):
                code = process.poll()
                # An app that hands off (Ollama's tray app, `lms server start`) exits 0; only a failure stops the wait.
                if code not in (None, 0):
                    tail = log_tail(log_path)
                    return 400, {'error': f'{info["label"]} stopped while starting (exit code {code}). Its log is '
                                          f'{log_path}' + (f':\n{tail}' if tail else '.'), 'field': 'start'}
                if time.monotonic() > deadline:
                    return 504, {'error': f'{info["label"]} did not answer within {START_WAIT_S} s. It may still be loading '
                                          f'its model; use Refresh connections in a minute. Its log is {log_path}.'}
                await asyncio.sleep(START_POLL_S)
    result = await provider_setup.checked_status(runtime)  # loads its models and unblocks its agents
    row = next((r for r in result.get('local_servers', []) if r.get('template') == template
                or template in (r.get('templates') or [])), None)
    return 200, {'ok': True, 'row': row}
