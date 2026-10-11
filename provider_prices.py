"""Built-in API prices for Anthropic and OpenAI key connections (Docs/providers.md, S5b).

USD per million tokens, standard (global) pricing, copied from the vendors' own price lists on the date
below. Prices the user enters for a connection override these. The estimate does not model batch
discounts, Anthropic fast mode or US-only inference (1.1x), or OpenAI priority/regional uplifts.
Anthropic cache writes use the 1-hour rate (the 5-minute rate is lower), so they err high. A model whose
cached-input price isn't listed is charged at its input price. Size tiers are applied per request:
`long` applies when a request's prompt (input + cache reads + cache writes) is above `above` tokens."""
import re

AS_OF = '2026-10-10'

ANTHROPIC = 'https://platform.claude.com/docs/en/about-claude/pricing'
OPENAI = 'https://developers.openai.com/api/docs/pricing'


def _c(i, cached, out, write, long=None):
    row = {'input': i, 'cached': cached, 'output': out, 'cache_write': write}
    if long:
        row['long'] = long
    return row


def _o(i, cached, out, long=None):
    row = {'input': i, 'cached': cached if cached is not None else i, 'output': out}
    if long:
        row['long'] = {'above': 272_000, 'input': long[0], 'cached': long[1] if long[1] is not None else long[0], 'output': long[2]}
    return row


# Claude API IDs from platform.claude.com/docs/en/about-claude/models/overview; IDs from the 4.6
# generation on are dateless, earlier ones are matched after their -YYYYMMDD snapshot suffix.
PRICES = {
    'claude': {'vendor': 'Anthropic', 'source': ANTHROPIC, 'models': {
        'claude-fable-5-1': _c(10, 0.25, 50, 20),
        'claude-fable-5': _c(10, 1, 50, 20),
        'claude-opus-5-5': _c(4, 0.20, 20, 8),
        'claude-opus-5': _c(5, 0.50, 25, 10),
        'claude-opus-4-8': _c(5, 0.50, 25, 10),
        'claude-opus-4-7': _c(5, 0.50, 25, 10),
        'claude-opus-4-6': _c(5, 0.50, 25, 10),
        'claude-opus-4-5': _c(5, 0.50, 25, 10),
        'claude-sonnet-5-5': _c(2, 0.10, 10, 4),
        'claude-sonnet-5': _c(2, 0.20, 10, 4),
        'claude-sonnet-4-6': _c(3, 0.30, 15, 6),
        'claude-sonnet-4-5': _c(3, 0.30, 15, 6),
        'claude-haiku-5-5': _c(0.10, 0.01, 0.50, 0.20,
                               long={'above': 100_000, 'input': 0.50, 'cached': 0.05, 'output': 2.50, 'cache_write': 1.0}),
        'claude-haiku-4-5': _c(1, 0.10, 5, 2),
    }},
    # Standard tier text tokens; long-context rates apply above 272K input tokens.
    'codex': {'vendor': 'OpenAI', 'source': OPENAI, 'models': {
        'gpt-6-astra': _o(10, 1.00, 50, (20, 2.00, 75)),
        'gpt-6.1-sol': _o(2, 0.10, 10, (4, 0.20, 15)),
        'gpt-6-sol': _o(2, 0.20, 10, (4, 0.40, 15)),
        'gpt-6-luna': _o(0.10, 0.01, 0.50, (0.20, 0.02, 0.75)),
        'gpt-5.6-sol': _o(4, 0.40, 20, (8, 0.80, 30)),
        'gpt-5.6-terra': _o(2, 0.20, 12, (4, 0.40, 18)),
        'gpt-5.6-luna': _o(0.20, 0.02, 1.20, (0.40, 0.04, 1.80)),
        'gpt-5.5': _o(5, 0.50, 30, (10, 1.00, 45)),
        'gpt-5.5-pro': _o(30, None, 180, (60, None, 270)),
        'gpt-5.4': _o(2.50, 0.25, 15, (5, 0.50, 22.50)),
        'gpt-5.4-pro': _o(30, None, 180, (60, None, 270)),
        'gpt-5.4-mini': _o(0.75, 0.075, 4.50),
        'gpt-5.4-nano': _o(0.20, 0.02, 1.25),
        'gpt-5.2': _o(1.75, 0.175, 14),
        'gpt-5.1': _o(1.25, 0.125, 10),
        'gpt-5': _o(1.25, 0.125, 10),
        'gpt-5-mini': _o(0.25, 0.025, 2),
        'gpt-5-nano': _o(0.05, 0.005, 0.40),
        'gpt-4.1': _o(2, 0.50, 8),
        'gpt-4.1-mini': _o(0.40, 0.10, 1.60),
        'gpt-4.1-nano': _o(0.10, 0.025, 0.40),
        'o3': _o(2, 0.50, 8),
        'o4-mini': _o(1.10, 0.275, 4.40),
    }},
}

SNAPSHOT = re.compile(r'-(\d{8}|\d{4}-\d{2}-\d{2})$')


def builtin(provider):
    """The built-in price table for a key connection's CLI ('claude' or 'codex'), or {}."""
    return PRICES.get(provider, {}).get('models', {})


def lookup(prices, model):
    """A model's price entry: the exact ID, or the ID without a dated snapshot suffix."""
    if not isinstance(model, str) or not model:
        return None
    return (prices or {}).get(model) or (prices or {}).get(SNAPSHOT.sub('', model))


def describe(provider):
    """Where the built-in prices come from, for the panel; None for connections without them."""
    table = PRICES.get(provider)
    if not table:
        return None
    return {'vendor': table['vendor'], 'source': table['source'], 'as_of': AS_OF,
            'models': {m: {k: p[k] for k in ('input', 'cached', 'output')} for m, p in table['models'].items()}}
