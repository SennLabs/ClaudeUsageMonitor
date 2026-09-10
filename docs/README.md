# Claude Usage Monitor — Documentation

Central documentation for the Claude Usage Monitor: a self-hosted service that
collects Claude Code token/cost telemetry from any number of dev containers and
renders it on a web dashboard.

The top-level [`../README.md`](../README.md) is the short "get it running" pitch.
These pages are the detail behind it.

## Start here

| Page | What it covers |
| --- | --- |
| [Getting started](getting-started.md) | Run the stack with Docker, or run both services locally without it |
| [Architecture](architecture.md) | Components, data flow, and the design decisions behind them |
| [Client setup](client-setup.md) | Configuring a Claude Code dev container to report usage here |

## Reference

| Page | What it covers |
| --- | --- |
| [Configuration](configuration.md) | Every environment variable, port, and volume |
| [API reference](api-reference.md) | All ingest and read endpoints, with request/response shapes |
| [Data model](data-model.md) | SQLite schema, OTLP attribute mapping, aggregation queries |
| [Dashboard guide](dashboard.md) | The three UI routes and what each control does |

## Operations

| Page | What it covers |
| --- | --- |
| [Deployment](deployment.md) | Images, health checks, persistence, upgrades |
| [Backup and restore](backup-and-restore.md) | Scheduled SQLite backups to a path or a NAS, and how to restore |
| [Security](security.md) | The auth model, its limits, and hardening notes |
| [Troubleshooting](troubleshooting.md) | Symptoms, causes, fixes |

## Contributing

| Page | What it covers |
| --- | --- |
| [Development](development.md) | Repo layout, local workflow, tests, and how to add an endpoint or a chart |

## Conventions used in these docs

- `9585` / `9595` are the **host** ports published by `docker-compose.yml`.
  Inside the compose network the services still listen on `8000` (ingest) and
  `80` (dashboard). Both numbers appear throughout — the host port is what a
  dev container or a browser connects to.
- Shell examples assume you are at the repository root unless a `cd` says otherwise.
- Anything marked *optional* is off by default and stays off until you set the
  environment variable that enables it.
