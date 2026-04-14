# PolySignal Gap Analysis and Priorities

Date: 2026-04-14

This document captures what is missing, what is already good, what is relevant to product value, and what should be done next.

## Overall Relevance Assessment

The project is relevant and directionally strong for a prediction-market intelligence assistant because it already has:

- an end-to-end pipeline (fetch -> parse -> debate -> score)
- a usable API and live dashboard
- persistence for run history
- passing tests for core logic contracts

Current relevance score (practical MVP readiness): 7/10.

Reason:

- Architecture is clear and demo-ready.
- Production hardening and evaluation depth are still incomplete.

## What Is Good Already

- Clear modular boundaries: scraping, pipeline, scoring, API, storage.
- Fallback strategy for model invocation (Gemini to Ollama).
- Useful SSE streaming for real-time UX.
- SQLite persistence makes local iteration easy.
- Unit tests pass and cover core node behaviors.

## What Is Missing (High Importance)

## 1) Observability and quality metrics

Why it matters:

- You cannot reliably know if predictions are improving without historical evaluation.

Missing now:

- no forecast calibration tracking (Brier score, log loss, calibration curve)
- no run-level quality KPIs dashboard
- no structured error taxonomy and alerting

Recommended actions:

1. Add an evaluation table in SQLite for resolved outcomes.
2. Compute rolling Brier/log loss by topic class.
3. Expose evaluation metrics on a new API endpoint.

## 2) Data quality controls for feeds

Why it matters:

- Forecast quality is bottlenecked by noisy or stale signals.

Missing now:

- no deduplication across feeds by URL/title similarity
- no source health checks and feed reliability scoring
- limited recency/data freshness guarantees

Recommended actions:

1. Add dedupe stage before parse node.
2. Record feed health (success rate, latency, empty payload rate).
3. Weight source trust with freshness and historical reliability.

## 3) Security and deployment readiness

Why it matters:

- API is currently open and unauthenticated, suitable for local use but risky if public.

Missing now:

- no auth/rate limiting on API
- permissive CORS configuration
- no secret-management strategy beyond local env file

Recommended actions:

1. Add API key or JWT auth for write endpoints.
2. Restrict CORS to known origins.
3. Add environment-based config profiles (dev/staging/prod).

## 4) Deterministic integration tests

Why it matters:

- Unit tests are good, but there is limited confidence in end-to-end runtime behavior.

Missing now:

- no full integration test for API run lifecycle + SSE flow
- no contract tests for payload schema stability

Recommended actions:

1. Add integration tests using FastAPI test client and mocked pipeline callbacks.
2. Add schema assertions for response payloads.

## 5) Decision transparency for traders

Why it matters:

- Traders need explainability and audit trail before acting on signals.

Missing now:

- no explicit confidence decomposition by evidence strength
- no uncertainty classification beyond single confidence number

Recommended actions:

1. Add score decomposition fields (signal quality, consensus, recency).
2. Add uncertainty labels (low/medium/high confidence reliability).

## Medium Priority Gaps

- No background worker queue for scaling concurrent runs.
- SQLite may become a bottleneck for multi-user deployment.
- No explicit versioned model registry for reproducibility.
- Limited frontend controls for filtering by source/sentiment/time.

## Low Priority Gaps

- No badges/release notes/changelog automation.
- No benchmark script for latency profiling by node.
- Optional Twitter scraper is not integrated into main pipeline path.

## Suggested Roadmap

## Phase 1 (1-2 weeks)

1. Add dedupe + feed health tracking.
2. Add API auth + tighter CORS.
3. Add integration test for run and SSE contract.

## Phase 2 (2-4 weeks)

1. Add evaluation pipeline with Brier/log loss tracking.
2. Add result explainability decomposition.
3. Add Postgres option for non-local deployments.

## Phase 3 (later)

1. Add queue-backed execution for higher throughput.
2. Add model versioning and experiment tracking.
3. Add enhanced dashboard analytics.

## Containerization Status

Containerization is now available via Dockerfile in repository root.

What this enables:

- reproducible local/runtime environment
- easier deployment to container platforms
- cleaner onboarding for contributors

Remaining container improvements (optional):

- multi-stage build to reduce final image size
- non-root runtime user hardening
- docker-compose profile with optional Ollama sidecar
