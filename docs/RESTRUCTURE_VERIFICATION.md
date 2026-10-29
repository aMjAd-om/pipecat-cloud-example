# Architecture migration verification

Verified locally on Windows on 2026-10-29.

| Check | Result |
|---|---|
| Original offline suite before migration | 89 tests run, 88 passed, 1 skipped |
| Migrated suite including layout regressions | 92 tests run, 91 passed, 1 skipped |
| Clean frontend install and production build | Passed |
| Deterministic survey-chat browser regression | Passed |
| Voice API health and built HTML/JS/CSS through FastAPI | Passed |
| Survey-chat HTML/JS/CSS and settings through FastAPI | Passed |
| Legacy questionnaire names and loading outside the repository | Passed |
| Absolute custom questionnaire paths | Passed |
| Package startup | Passed delegation check |
| Evaluation and provider verification CLI help | Passed |
| Frozen editable installation and lockfile validation | Passed offline |
| Both Docker Compose configurations | Passed configuration validation |
| Docker COPY source paths | All present |
| Local Markdown links and Git whitespace check | Passed |

The skipped test checks Linux signal handling and is skipped on Windows, as it
was before the migration. The offline suite exercises real Pipecat pipeline,
session, transport, and lifecycle behavior with simulated provider responses.

A Docker image build and container startup were not verified because the local
Docker Desktop engine returned HTTP 500. Live Azure/Cartesia voice calls and
physical Asterisk calls were not rerun. The deterministic browser check makes no
external provider requests.

See [architecture and commands](ARCHITECTURE.md) to reproduce the offline suite,
frontend build, and browser check.
