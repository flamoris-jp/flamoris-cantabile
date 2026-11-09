#!/usr/bin/env python3
"""Launch the actual shared MCP host; settings and projects live outside /app."""
import os
import pathlib
import stat
import sys


def write_credential(credential, token):
    fd = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'w') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Credential target must be a regular, unlinked file')
        os.fchmod(stream.fileno(), 0o600)
        os.ftruncate(stream.fileno(), 0)
        stream.write(token)


def main():
    root = pathlib.Path('/data')
    config = root / 'config'
    if root.is_symlink() or config.is_symlink():
        raise ValueError('Data and configuration directories must not be symlinks')
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = os.environ.pop('CANTABILE_HUB_TOKEN', '')
    endpoint = os.environ.get('CANTABILE_HUB_ENDPOINT', '')
    permission = os.environ.get('CANTABILE_PERMISSION', 'edit')
    port = int(os.environ.get('CANTABILE_PORT', '8774'))
    if permission not in ('edit', 'readOnly') or not 1024 <= port <= 65535:
        raise ValueError('Invalid permission or health port')
    if not endpoint.startswith('wss://') or not token or any(c in token for c in '\r\n\x00'):
        raise ValueError('A dedicated WSS Hub endpoint and one-line credential are required')
    credential = config / 'hub-token'
    # O_NOFOLLOW prevents a retained configuration link from redirecting a secret.
    write_credential(credential, token)
    os.execv('/app/cantabile-host', ['/app/cantabile-host', 'serve', '--workspace', str(root),
        '--permission', permission, '--hub-endpoint', endpoint, '--credential-file', str(credential),
        '--health-port', str(port)])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError):
        # Do not echo raw configuration, paths, URLs or credentials on startup failure.
        print('Cantabile startup configuration is invalid or unavailable.', file=sys.stderr)
        sys.exit(1)
