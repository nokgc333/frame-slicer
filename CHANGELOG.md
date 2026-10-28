# Changelog

All notable changes to this project are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.0] - 2026-10-28

### Added

- `split`: cut a frame range into slices by count (`--slices`) or by size (`--chunk-size`), with stepped ranges (`1-100x2`), several ranges (`1-10,50-60`) and heavy frames first (`--heavy`).
- `submit`: send one QueueWatch job per slice, carrying its frame range and scene; write a manifest (`--manifest`); hold a stage back until the same frames of an earlier stage succeeded (`--depends-on`).
- `retry` and `status`: send what was never accepted and requeue dead or cancelled jobs under the same id; show the queue's current state of a manifest.
- `commands`: print Blender, husk or hbatch render command lines per slice (never run).
- Safe transport: timeouts, a response size limit, retries only for failures that leave no job behind (capped exponential backoff with jitter), a stable `Idempotency-Key`, a Bearer token from `QUEUEWATCH_API_TOKEN` (https or localhost only), `QUEUEWATCH_API_URL`, no redirects, no way to switch off TLS verification.
- Verified against a real QueueWatch worker running Blender 4.5.3 (`scripts/verify_real_render.py`).
