#!/usr/bin/env python3
"""Retired ZIP entrypoint; use release_pack.py for managed release packages."""


def build():
    raise SystemExit(
        'The incomplete standalone ZIP builder has been retired.\n'
        'Build a committed managed release with:\n'
        '  python release_pack.py build --repo . --commit HEAD --spool ./release-staging\n'
        'This creates a release package, not a standalone installation ZIP.\n'
        'See README.md for packaging and deployment boundaries.'
    )


if __name__ == '__main__':
    build()
