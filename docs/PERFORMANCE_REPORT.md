# Atlas Cache Refactor — Performance Report

**Test date:** 2026-07-21  
**Scope:** Backend response caching, query indexes, frontend server-state caching, and mutation invalidation.

## Measured dashboard-cache result

The benchmark exercised the authenticated `GET /dashboard/summary` endpoint through the FastAPI ASGI stack using the repository’s isolated in-memory SQLite fixture. A cold sample cleared the cache before each request; warm samples used the identical, already-primed cache entry. The payload was asserted equal on every warm read.

| Metric | Cold / uncached-equivalent read | Warm cached read | Change |
| --- | ---: | ---: | ---: |
| Median endpoint latency | 15.982 ms | 1.976 ms | **87.6% lower** |
| Sample count | 20 cold requests | 100 warm requests | — |
| Correctness check | Database-backed payload | Identical cached payload | Passed |

> These timings are an **isolated in-process benchmark**, not a production latency commitment. They exclude browser rendering, network transit, Redis network latency, and production PostgreSQL load. Their purpose is to verify the change in execution path and quantify the database-aggregation work avoided after a cache hit.

## Expected application impact

The server now caches costly, repeatable read models with short, resource-specific TTLs and precise post-commit invalidation. The most material production benefit should be lower PostgreSQL read pressure and reduced p50/p95 response time for dashboard, task, notification, referral, payment, plan, configuration, and administrative aggregate reads. The new composite indexes further reduce the work required on cache misses for user-scoped histories and aggregates.

| Area | Change | Expected effect |
| --- | --- | --- |
| Dashboard and wallet summaries | 30-second backend and frontend cache, plus post-commit invalidation | Fewer repeated aggregate queries during navigation and refreshes |
| Tasks and task completion | 5-minute task cache, optimistic completion UI, rollback, dependent-key invalidation | Faster return visits without stale completed-task state |
| Notifications | One shared 20-second cache and focused polling owner | Eliminates duplicate component polling and repeated unread-count requests |
| Referrals and payment history | User-scoped cache keys, pagination bounds, targeted invalidation | Less repeated hierarchy/history work and bounded query cost |
| Plans and configuration | Shared cache keys with longer TTLs | Fewer duplicate catalog/configuration reads across users |
| Mutation correctness | Invalidation only after successful database commits | Cached views reflect committed writes without making Redis an availability dependency |

## Validation completed

| Check | Result |
| --- | --- |
| Frontend TypeScript production build | Passed |
| Backend regression and cache tests | **9 passed** |
| Dashboard cold-versus-warm benchmark | Passed; **87.6% median latency reduction** |
| Diff whitespace check | Passed |
| Redis outage behavior | Covered by resilient local TTL fallback tests |

## Production measurement recommendation

After deployment, collect cache hit ratio, Redis error/fallback rate, PostgreSQL query count per route, and p50/p95 latency for the cached endpoints. Compare these metrics against the deployment immediately preceding this change with an equivalent traffic window. This will quantify real-network and real-database improvements while keeping the test benchmark as a repeatable regression guard.
