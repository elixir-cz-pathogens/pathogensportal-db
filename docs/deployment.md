# Deployment — how the pipeline runs and reaches the portal

This repository is never deployed on its own. The portal repository (`pathogensportal`) attaches it
as a git submodule, builds a Docker image from it and runs that image on the server. This document
describes the hand-over from this side; the server configuration lives in the private
infrastructure repository.

## The Docker image

```bash
docker build -t pathogensportal-db .
docker run --rm \
  -v "$PWD/data:/data" \
  -v "$PWD/out/charts:/output/charts" \
  pathogensportal-db
```

| | |
|---|---|
| Base | `python:3.12-slim` |
| Dependencies | `requirements.txt` — the only place they are listed |
| Environment | `DATA_DIR=/data`, `OUTPUT_DIR=/output/charts` |
| Command | `run_all.py && generate_json.py && detect_anomalies.py && compute_mem.py` |

Things that follow from this:

- The steps are joined with `&&`. `run_all.py` exits with code 1 when any scraper failed, and in
  that case the generators do **not** run in that container invocation.
- `/data` should be a persistent volume. It holds the snapshot archive, the manifest and the
  caches (SZÚ regional PDFs, RespiCast rounds, historical SZÚ seasons). With an empty volume every
  run downloads everything again and the archive starts from nothing.
- `load_to_db.py` is not part of the command.
- The image contains no `.git` directory, so `code_version` in the anomaly output is `unknown`
  there.

## How the portal runs it

In the portal's `deploy/docker-compose.yml` the image is the service `datascrapper`, started on
demand or from cron — it does not run continuously:

```bash
docker compose -f deploy/docker-compose.yml --profile tools run --rm datascrapper
```

The service mounts a named volume onto `/data` and the portal's
`frontend/static/data/charts/` onto `/output/charts`, so the generated JSON lands directly in the
static site's data directory.

The portal's database container mounts `db/init.sql` from this repository as its initialisation
script.

## Refreshing the dev server

`.github/workflows/trigger-datapipeline.yml` runs on every push to `dev` here, and on manual
dispatch.

```
push to dev ──► GitHub Action ──► SSH to the dev server ──► the pipeline runs there
                                  (forced command)          and its log streams back
```

- The workflow does **not** run the pipeline. The scrapers, the image and the data processing all
  run on the server.
- The SSH key is bound to a forced command on the server: it can start one systemd unit and
  nothing else.
- The server's host key is pinned from a repository variable; `ssh-keyscan` is deliberately not
  used.
- The run is synchronous and its log comes back on stdout, so the GitHub job turns red when the
  pipeline fails.

Configuration in the repository settings:

| Kind | Name | Value |
|---|---|---|
| secret | `PIPELINE_TRIGGER_SSH_KEY` | private half of the trigger key |
| variable | `PIPELINE_HOST` | host name of the dev server |
| variable | `PIPELINE_SSH_HOST_KEY` | the server's `ssh-ed25519` line |
| variable | `PIPELINE_TRIGGER_ENABLED` | `true` to enable the job |

There is deliberately only one environment. Do not add a production variant of this workflow:
production must never take unreviewed data straight from a branch.

## Releases and the submodule pin

Branches: work happens on `feature/…`, `bugfix/…` and `docs/…` branches, is merged into `dev`, and
`dev` is merged into `main` for a release. Releases are annotated tags `vMAJOR.MINOR.PATCH`
(`v0.1.0` … `v0.4.0` so far).

The portal pins the submodule to a **release tag**, not to a branch. Two consequences:

1. **A release here changes nothing on the portal by itself.** New code reaches production only
   when a pull request in the portal repository moves the pin to the new tag. If that step is
   forgotten, production keeps building the old image.
2. **The interface must stay stable between releases.** See the contract in the
   [README](../README.md#contract-with-the-portal). A breaking change needs a release and a note
   in [CHANGELOG.md](../CHANGELOG.md).

Checklist for a release:

1. `dev` is green (tests) and the dev-server run succeeded.
2. Move the "Unreleased" section of `CHANGELOG.md` under the new version; mark breaking changes.
3. Merge `dev` into `main`.
4. Create the annotated tag on `main` and push it.
5. In the portal repository, open a pull request that moves the submodule pin to the tag.

The portal has a CI check (`check-submodule-pin`) that fails a pull request into its `main` when
the pin moves to something that is not a clean release tag, or moves backwards.
