#!/usr/bin/env python3
"""Build an Updater catalog from an actual docker-save image; never publishes."""
import argparse
import hashlib
import json
import pathlib
import re
import shutil
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def verify_image(image, image_id, revision):
    if image.is_symlink() or not image.is_file():
        raise ValueError('A regular docker-save archive is required')
    with tarfile.open(image, 'r:*') as archive:
        info = archive.getmember('manifest.json')
        if not info.isfile() or info.size > 1024 * 1024:
            raise ValueError('Invalid image manifest')
        manifest = json.load(archive.extractfile(info))
        if not isinstance(manifest, list) or len(manifest) != 1:
            raise ValueError('Exactly one Docker image is required')
        config = archive.getmember(manifest[0]['Config'])
        if not config.isfile() or config.size > 16 * 1024 * 1024:
            raise ValueError('Invalid image configuration')
        content = archive.extractfile(config).read()
        actual = 'sha256:' + hashlib.sha256(content).hexdigest()
        if actual != image_id:
            raise ValueError('Docker image ID does not match its configuration digest')
        metadata = json.loads(content)
        if metadata.get('os') != 'linux' or metadata.get('architecture') != 'amd64':
            raise ValueError('The catalog requires a Linux amd64 image')
        labels = (metadata.get('config') or {}).get('Labels') or {}
        if labels.get('org.opencontainers.image.revision') != revision:
            raise ValueError('Image revision label does not match the source revision')


def package(image, image_id, revision, output):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Full image ID and source revision are required')
    if output.exists() or output.is_symlink():
        raise ValueError('Use a new output directory')
    verify_image(image, image_id, revision)
    release = json.loads((ROOT / 'release/updater.json').read_text())
    with image.open('rb') as stream:
        digest = 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()
    version = release['version']
    asset = 'linux-amd64--image.tar'
    settings = [
        dict(key='PORT', label='Local health port', kind='port', default='8774'),
        dict(key='HUB_ENDPOINT', label='Dedicated Hub WSS endpoint', default='', required=True),
        dict(key='HUB_TOKEN', label='Hub connector credential', default='', secret=True, required=True, kind='url_password'),
        dict(key='PERMISSION', label='MCP permission (edit/readOnly)', default='edit'),
        dict(key='SOUNDFONT_ROOT', label='Existing SoundFont directory (read-only)', default='', required=True),
    ]
    runtime = ('CANTABILE_PORT=${PORT}\nCANTABILE_HUB_ENDPOINT=${HUB_ENDPOINT}\n'
               'CANTABILE_HUB_TOKEN=${HUB_TOKEN}\nCANTABILE_PERMISSION=${PERMISSION}\n')
    recipe = dict(application_id=release['application_id'], release=version, platform=release['platform'],
        compatible_from=release['compatible_from'], dependencies={}, settings=settings,
        downloads={'image.tar': dict(url=f'https://github.com/flamoris-jp/flamoris-cantabile/releases/download/v{version}/{asset}', digest=digest)},
        generated={'runtime.env': runtime},
        application=dict(application_id=release['application_id'], release=version, kind='docker',
            directories=[dict(path='${root}/config'), dict(path='${root}/data', uid=10001, gid=10001, mode=488)],
            files=[dict(source=dict(path='${package}/runtime.env', digest='generated', private=True), destination='${root}/config/runtime.env')],
            health_url='http://127.0.0.1:${PORT}/healthz',
            image_archive=dict(path='${package}/image.tar', digest=digest, private=False), image_id=image_id,
            container_name=release['application_id'], environment_file='${root}/config/runtime.env', network='host',
            mounts=[dict(source='${root}/data', target='/data', read_only=False),
                    dict(source='${SOUNDFONT_ROOT}', target='/soundfonts', read_only=True)]))
    output.mkdir(parents=True)
    shutil.copyfile(image, output / asset)
    (output / 'catalog.json').write_text(json.dumps(dict(catalog_version=1, recipes=[recipe], updater_releases=[]), indent=2) + '\n')
    (output / 'candidate.json').write_text(json.dumps(dict(application_id=release['application_id'], release=version,
        platform=release['platform'], source_revision=revision, image_id=image_id, archive_digest=digest), indent=2) + '\n')
    return recipe


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=pathlib.Path, required=True)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    package(args.image, args.image_id, args.revision, args.output)
