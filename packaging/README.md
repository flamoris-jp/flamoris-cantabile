# Docker / Updater distribution

The application owns its Docker image, immutable archive, release metadata and
Updater recipe. The release workflow publishes a preview only after successful main-branch CI,
then builds and smoke-tests the exact verified revision again. It uploads the
image archive, digested catalog, image/source receipt, Hub descriptor and license
notices through a draft release. Existing releases are never overwritten.
Publication does not register a catalog, install on a deployment host, or prove
a real Docker/Updater rollout. Updater platform metadata uses `linux/amd64`;
asset filenames use `linux-amd64--image.tar`.

## Published preview

[v0.1.0 preview](https://github.com/flamoris-jp/flamoris-cantabile/releases/tag/v0.1.0) is published in the now-public repository. It contains `catalog.json`, `candidate.json`, `linux-amd64--image.tar`, `cantabile-hub.json`, `LICENSE` and `THIRD_PARTY.md`. Use the versioned release assets for installation; a local build is a separate candidate.

A fresh Linux Updater installation, start and actual Hub/MIDI/WAV round trip have been accepted in one deployment. This is not compatible-update or restart/recovery acceptance. See [evidence](../docs/ACCEPTANCE.md).

## Build and prepare

```bash
docker build -f packaging/Dockerfile --build-arg SOURCE_REVISION="$(git rev-parse HEAD)" -t flamoris-cantabile:0.1.0 .
docker image inspect flamoris-cantabile:0.1.0 --format '{{.Id}}'
docker save -o image.tar flamoris-cantabile:0.1.0
git rev-parse HEAD
```

Pass the exact image ID and source commit printed above to:

```bash
python3 packaging/package_updater.py --image image.tar \
  --image-id sha256:ACTUAL_IMAGE_ID --revision FULL_SOURCE_COMMIT \
  --output candidate-0.1.0
```

Build from a clean checkout of the selected commit. The packager verifies the
Docker-save configuration digest against the image ID, checks Linux/amd64 and
the image's source-revision label, hashes the archive, and emits `catalog.json`,
`candidate.json`, and the archive. The label is metadata, not a build attestation.
The output directory must be new. It never uploads/publishes these files.
Release asset URLs in the generated catalog become usable only after the
corresponding reviewed release is actually published with matching assets.
The repository and v0.1.0 preview are public. Private mirrors or deployments with
additional access controls must verify their own release-download permissions.

## Host startup contract

The image runs the real .NET host with the shared native editing engine. It uses
FLAMORIS MCP Core's Hub WSS connection provider and permission/revision guards.
The local stdio mode uses the official MCP SDK over the same Core boundary;
it does not introduce a second MCP protocol implementation or editing session.

Updater setup supplies `PORT`, `HUB_ENDPOINT`, `HUB_TOKEN`, `PERMISSION`, and
`SOUNDFONT_ROOT`. Port 8774 is only a proposed configurable default; select an
available local port during setup. The generated environment file is private.
The entrypoint removes the token from its environment, writes a mode-0600
credential file under retained application data, then launches the host with a
credential-file path. Tokens never appear in command arguments or the catalog.

`/healthz` is loopback-only and indicates process/editor readiness. Hub discovery,
permission, actual MIDI edits, audio rendering, full-byte retrieval, and client
listening must be accepted separately.

The reviewed static template is `config/mcps/_cantabile.example.json` (JSON is
valid YAML). It is generated from the actual host's 18-tool catalog, including
schemas and annotations. Match its upstream ID and product ID to the dedicated
WSS endpoint and provide the separate connector-token environment variable.
Underscore-prefixed templates are ignored by Hub until an operator copies one to
a deployment-local non-underscore YAML filename. No live Hub catalog is changed
by this repository or the generator.

## Retained data and free instruments

- `/data`: projects, owned exports, configuration and credential state; writable
  by application UID/GID 10001 and preserved across compatible updates.
- `/soundfonts`: a separately prepared read-only SoundFont directory, containing
  `default-GM.sf2` for the configured first backend. Supply a bank whose usage and
  redistribution conditions have been checked; no SoundFont is bundled here.
- `/app`: application binaries, replaced by managed updates.

Offline rendering does not require display, GPU, sound-card or Docker-socket
mounts. The first render backend is FluidSynth. VST 3 loading and presets remain
unimplemented and are reported as unavailable, rather than silently substituted.

There is no compatible predecessor release yet. `compatible_from` is empty;
update compatibility and migrations must be verified before a subsequent release
adds predecessors. Updater remains the process/update authority; no parallel
systemd application service is installed by this repository.

## Verification status

The package-generator tests use Docker-save-shaped fixtures and verify digest
identity, persistence mounts, private settings and refusal to overwrite output.
They do not replace a real image build/install/update acceptance test. GitHub CI and the release workflow passed actual Docker build, packaged host discovery and offline
WAV synthesis. A fresh Updater installation/start and real 18-tool Hub editing/audio
round trip have also been accepted; compatible updates and controlled restart remain pending.
