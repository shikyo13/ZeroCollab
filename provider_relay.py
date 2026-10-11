"""Room-side relay for Claude Code and Codex on the user's Anthropic and OpenAI API keys
(Docs/providers.md, slice S5b; security d807412d).

An agent never holds the key. Its CLI gets this relay's address and a relay token minted for that run
(native_adapters.relay_setup). The relay checks the request, adds the key from the credential store
and forwards it to the connection's one fixed vendor origin, streaming the reply back unchanged
while reading its token usage for the cost view. Gates, in order:
  1. no Origin header (the CLIs never send one; a page or served upload does);
  2. a loopback client and the exact Host 127.0.0.1:<port> or localhost:<port>;
  3. the raw path has no percent-encoding, backslash, '..' or '//';
  4. the token belongs to an active run whose agent is on exactly the connection in the path, re-read
     on every request (identity for usage comes only from that run);
  5. the connection is on and its stored origin matches (provider_keys.headers);
  6. the method and path are on the vendor's allowlist; only allowlisted query keys pass.
Headers and bodies are never logged. Only allowlisted request and response headers cross."""
import hmac
import json
import re

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

import provider_keys

ROUTES = {
    'claude': {('POST', 'v1/messages'), ('POST', 'v1/messages/count_tokens'), ('GET', 'v1/models')},
    'codex': {('POST', 'v1/responses'), ('GET', 'v1/models')},
}
METERED = {'v1/messages', 'v1/responses'}
QUERY = {'claude': {'beta'}, 'codex': set()}
REQUEST_HEADERS = ('content-type', 'accept', 'anthropic-version', 'anthropic-beta', 'openai-beta', 'user-agent')
RESPONSE_HEADERS = ('content-type', 'request-id', 'x-request-id', 'retry-after')
RESPONSE_PREFIXES = ('anthropic-ratelimit-', 'x-ratelimit-')
MAX_BODY = 32 * 1024 * 1024  # native_adapters.CODEX_MESSAGE_LIMIT
MAX_IN_FLIGHT = 6  # per run: bounds a runaway tool loop before S5c spend limits exist
ERROR_BODY = 64 * 1024
CONNECTION = re.compile(r'[a-z][a-z0-9-]{0,39}')
RUNTIME = None
# Vendor origins are read live from provider_keys.VENDORS, the one place they are defined.


def refuse(status, message, kind='permission_error'):
    # Shaped like both vendors' errors, so each CLI shows the message.
    return JSONResponse({'type': 'error', 'error': {'type': kind, 'message': message}}, status)


def token_of(request, provider):
    if provider == 'claude':
        return request.headers.get('x-api-key')
    value = request.headers.get('authorization', '')
    return value[7:].strip() if value.lower().startswith('bearer ') else None


def run_for(token):
    """The active run holding this relay token, compared in constant time."""
    if not token:
        return None
    for run in list(RUNTIME.running.values()):
        expected = getattr(run, 'relay_token', None)
        if expected and hmac.compare_digest(token.encode(), expected.encode()):
            return run
    return None


def problem_for(status, body, where):
    """A short line for a vendor refusal, so a turn that fails on the account never reads as an agent fault."""
    try:
        detail = json.loads(body).get('error', {})
        text = str(detail.get('message', '') if isinstance(detail, dict) else detail).lower()
    except (ValueError, AttributeError):
        text = ''
    if status in (401, 403):
        return f'Key not accepted by {where}. Check or replace it in Team > Providers.'
    if status == 402 or any(word in text for word in ('credit', 'billing', 'quota', 'balance', 'payment')):
        return f'{where} refused the request: the account has no credit or a billing problem. Check it with the provider.'
    return None


class Meter:
    """Reads token usage from a streamed (SSE) or plain JSON reply without changing it."""

    def __init__(self, provider, streamed):
        self.provider, self.streamed = provider, streamed
        self.buffer, self.model, self.counts, self.seen = b'', '', {}, False

    def feed(self, chunk):
        if not self.streamed:
            if len(self.buffer) < MAX_BODY:
                self.buffer += chunk
            return
        self.buffer += chunk
        *lines, self.buffer = self.buffer.split(b'\n')
        for line in lines:
            line = line.strip()
            if line.startswith(b'data:'):
                try:
                    self.event(json.loads(line[5:]))
                except ValueError:
                    pass

    def finish(self):
        if not self.streamed and self.buffer:
            try:
                data = json.loads(self.buffer)
                if self.provider == 'claude':
                    self.event({'type': 'message_start', 'message': data})
                else:
                    self.event({'type': 'response.completed', 'response': data})
            except ValueError:
                pass
        return self.result()

    def event(self, data):
        if not isinstance(data, dict):
            return
        kind = data.get('type')
        if self.provider == 'claude':
            if kind == 'message_start' and isinstance(data.get('message'), dict):
                message = data['message']
                self.model = message.get('model') or self.model
                self.usage(message.get('usage'))
            elif kind == 'message_delta':
                self.usage(data.get('usage'))
        elif kind == 'response.completed' and isinstance(data.get('response'), dict):
            response = data['response']
            self.model = response.get('model') or self.model
            self.usage(response.get('usage'))
            if isinstance(response.get('service_tier'), str):
                self.counts['service_tier'] = response['service_tier']  # OpenAI reports it on the response

    def usage(self, usage):
        if not isinstance(usage, dict):
            return
        self.seen = True
        # Later events repeat or extend earlier counts (Anthropic sends output in message_delta).
        for key, value in usage.items():
            self.counts[key] = value

    def tier(self):
        """The service tier the vendor reports for this response, '' when none (security f066876a). Anthropic
        reports usage.service_tier, and fast mode as usage.speed; OpenAI reports response.service_tier."""
        u = self.counts
        speed = u.get('speed')
        if isinstance(speed, str) and speed.lower() not in ('', 'standard', 'normal', 'default'):
            return speed.lower()
        tier = u.get('service_tier')
        return tier.lower() if isinstance(tier, str) else ''

    def result(self):
        """{input, cached, output[, cache_write][, tier]} with input excluding cache reads and writes, or None."""
        if not self.seen:
            return None
        u, count = self.counts, provider_keys.count
        try:
            if self.provider == 'claude':
                counts = {'input': count(u.get('input_tokens')), 'cached': count(u.get('cache_read_input_tokens')),
                          'output': count(u.get('output_tokens'))}
                write = count(u.get('cache_creation_input_tokens'))
                if write:
                    counts['cache_write'] = write
            else:
                details = u.get('input_tokens_details') if isinstance(u.get('input_tokens_details'), dict) else {}
                prompt, cached = count(u.get('input_tokens')), count(details.get('cached_tokens'))
                counts = {'input': max(0, prompt - cached), 'cached': cached, 'output': count(u.get('output_tokens'))}
        except (ValueError, OverflowError):
            return None
        tier = self.tier()
        return {**counts, 'tier': tier[:40]} if tier else counts


async def relay(request: Request, connection: str, path: str):
    if request.headers.get('origin') is not None:
        return refuse(403, 'The relay refuses browser requests.')
    port = RUNTIME.cfg['server']['port']
    client = request.client.host if request.client else ''
    if client not in ('127.0.0.1', '::1') or request.headers.get('host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
        return refuse(403, 'The relay accepts requests from this computer only.')
    raw = request.scope.get('raw_path', b'').decode('ascii', 'replace')
    rest = raw[len('/relay/'):] if raw.startswith('/relay/') else ''
    if not rest or any(bad in rest for bad in ('%', '\\', '..', '//')) or not CONNECTION.fullmatch(connection):
        return refuse(404, 'Not a relay path.', 'not_found_error')
    run = None
    for provider in ROUTES:
        run = run_for(token_of(request, provider))
        if run:
            break
    if run is None:
        return refuse(401, 'This relay token is not valid for a running turn.', 'authentication_error')
    # Identity comes only from the token's run; the agent's connection is read again for each request.
    cfg = RUNTIME.cfg['agents'].get(run.name, {})
    provider = cfg.get('provider')
    supplied = token_of(request, provider) if provider in ROUTES else None  # in the vendor's own header
    if (cfg.get('key_target') != connection or not supplied
            or not hmac.compare_digest(supplied.encode(), (getattr(run, 'relay_token', None) or '').encode())):
        return refuse(403, 'This relay token is for another connection.')
    if (request.method, path) not in ROUTES[provider]:
        return refuse(404, f'{request.method} /{path} is not relayed.', 'not_found_error')
    base = provider_keys.VENDORS[provider]['origin']
    vendor = {**cfg, 'base_url': base}
    try:
        auth = provider_keys.headers(vendor)
    except provider_keys.KeyProblem as exc:
        run.relay_problem = str(exc)
        return refuse(403, str(exc))
    # The slot is taken before the first await, so requests arriving together can't all pass the cap.
    if getattr(run, 'relay_in_flight', 0) >= MAX_IN_FLIGHT:
        return refuse(429, 'Too many relay requests at once for this turn.', 'rate_limit_error')
    run.relay_in_flight = getattr(run, 'relay_in_flight', 0) + 1
    upstream = reply = meter = None
    error = bytearray()
    released = False

    async def release():
        """Once per request, however it ends (finished, refused, or the CLI gone before or while streaming)."""
        nonlocal released
        if released:
            return
        released = True
        run.relay_in_flight -= 1
        if reply is not None:
            await reply.aclose()
        if upstream is not None:
            await upstream.aclose()
        if meter:
            counts = meter.finish()  # a plain JSON reply is parsed here, model included
            provider_keys.append_usage(connection, run.name, run.id, meter.model or str(run.settings.get('model') or ''), counts)
        elif reply is not None and reply.status_code >= 400:
            run.relay_problem = problem_for(reply.status_code, bytes(error), where) or getattr(run, 'relay_problem', '')

    where = provider_keys.host(base)
    try:
        body = b''
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                await release()
                return refuse(413, 'The request is too large for the relay.', 'request_too_large')
        query = [(k, v) for k, v in request.query_params.multi_items() if k in QUERY[provider]]
        sent = {k: request.headers[k] for k in REQUEST_HEADERS if k in request.headers}
        # identity: the meter reads the body as it passes, and the CLI gets it exactly as labelled.
        sent.update(auth, **{'accept-encoding': 'identity'})
        upstream = httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(connect=15, read=600, write=60, pool=15))
        try:
            reply = await upstream.send(upstream.build_request(request.method, base + '/' + path,
                                                               params=query, headers=sent, content=body), stream=True)
        except httpx.HTTPError as exc:
            await release()
            run.relay_problem = f"Couldn't reach {where} ({type(exc).__name__})."
            return refuse(502, run.relay_problem, 'api_error')
    except BaseException:
        await release()  # the CLI disconnected or the turn was cancelled before a reply existed
        raise
    kept = {k: v for k, v in reply.headers.items()
            if k.lower() in RESPONSE_HEADERS or k.lower().startswith(RESPONSE_PREFIXES)}
    if reply.status_code == 200 and path in METERED:
        meter = Meter(provider, 'text/event-stream' in reply.headers.get('content-type', ''))

    async def stream():
        async for chunk in reply.aiter_bytes():  # decoded, so content-encoding is never passed on
            if meter:
                meter.feed(chunk)
            elif reply.status_code >= 400 and len(error) < ERROR_BODY:
                error.extend(chunk)
            yield chunk

    return Relayed(stream(), release, status_code=reply.status_code, headers=kept)


class Relayed(StreamingResponse):
    """A streamed reply whose cleanup runs whenever sending ends, including a CLI that disconnects
    before the first chunk, when the body generator never starts and its own cleanup would not run."""

    def __init__(self, content, release, **kwargs):
        super().__init__(content, **kwargs)
        self.release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.release()


def install(host, runtime):
    global RUNTIME
    RUNTIME = runtime
    host.app.add_api_route('/relay/{connection}/{path:path}', relay, methods=['GET', 'POST'], include_in_schema=False)
