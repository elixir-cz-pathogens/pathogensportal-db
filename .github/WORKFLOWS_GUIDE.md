# GitHub Actions — guide

This repository has a few automations (GitHub Actions) that link commits, branches and pull
requests to issues, run the tests, and refresh the dev server. They need no setup to work.

## What happens automatically

| Workflow | When it runs | What it does |
|---|---|---|
| Issue Prefixer | an issue is opened | renames the title to `PPDB-<number>: …` |
| Branch Issue Linker | a `feature/…`, `bugfix/…` or `docs/…` branch is pushed | comments on the issue that work has started |
| PR Open Notification | a pull request is opened | comments on the issue with a link to the PR |
| PR Merged Notification | a pull request is merged | comments on the issue that it is done |
| CI: Tests | **every push and pull request** | runs `pytest` and shows ✅ / ❌ |
| Data pipeline trigger | a push to `dev`, or manual dispatch | asks the dev server to run the pipeline — see [docs/deployment.md](../docs/deployment.md) |

## What you need to do

1. **Tests:** put `test_*.py` files into `tests/`. They run on every push.

2. **For the issue links to work** (optional, but recommended):
   - Name branches `feature/PPDB-12_something` (the number is the issue number). The pattern is
     exact: prefix, hyphen, number, underscore.
   - Put `PPDB-12` into the pull request title (e.g. `PPDB-12: add scraper`).
   - If you do not, nothing breaks — the comment on the issue is just not posted.

## Optional settings

- **Change the `PPDB` prefix:** Settings → Secrets and variables → **Actions** → Variables →
  add `PROJECT_PREFIX` with another value. Without it the default `PPDB` is used.
- **If the comments on issues are not posted:** Settings → Actions → General → *Workflow
  permissions* → switch to **Read and write permissions** → Save.
- **Tests as a required gate** before merging into `main`: Settings → Branches → add a rule for
  `main` → *Require status checks* → select `tests`.

## If something looks wrong

The automations are written so that they **never block** a push (except for failing tests, if you
make them required). When an action "does nothing", the issue number in the name usually did not
match — that is fine.
