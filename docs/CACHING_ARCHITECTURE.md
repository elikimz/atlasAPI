# Atlas Caching Architecture

## Purpose

This design introduces a **two-tier server-state architecture** for Atlas. TanStack Query owns browser-side API state and applies stale-while-revalidate behavior, while Redis owns reusable backend response data. PostgreSQL remains the source of truth. All cache operations are fail-open: an unavailable Redis instance must never prevent an authenticated request from reaching PostgreSQL.

> Cache entries are performance artifacts, not sources of truth. Every write commits to PostgreSQL first and invalidates only the affected representations after a successful commit.

## Ownership and data flow

| Layer | Responsibility | Consistency behavior |
| --- | --- | --- |
| PostgreSQL | Canonical transactional data, balances, task completion, payments, and audit history | Strongly consistent within each committed transaction |
| Redis | Shared, short-lived read models for public data and costly user/dashboard aggregates | Explicit deletion after writes; TTL protects against missed invalidations |
| TanStack Query | Per-browser server-state cache, request deduplication, background refetching, optimistic UI | Shows cached data while silently fetching fresher data; mutations update or invalidate related keys |
| Local session storage | Existing bearer-token persistence only; no TanStack Query persistence for sensitive API payloads | Cleared on logout or unrecoverable authentication failure |

The frontend uses one query client for the complete application. Query results are retained only in browser memory, so account data is not serialized into durable query-cache storage. The existing token flow remains compatible; a future migration to HttpOnly, Secure, SameSite cookies is the recommended hardening path for production bearer-token security.

## Cache policy

| Resource group | Frontend stale time | Backend TTL | Cache key shape | Why |
| --- | ---: | ---: | --- | --- |
| Plans / package catalog | 30 minutes | 30 minutes | `atlas:plans:active` | Shared, infrequently modified public catalog |
| Application configuration | 1 hour | 1 hour | `atlas:app-config` | Small shared configuration document |
| Authenticated user / profile | 10 minutes | Browser only | `['user','me']`, `['profile']` | Avoids repeated `/auth/me` requests during navigation |
| Available and playable tasks | 5 minutes | 5 minutes | `atlas:user:{id}:tasks:*` | User- and plan-scoped read model |
| Dashboard / wallet summary | 30 seconds | 30 seconds | `atlas:user:{id}:dashboard` | Costly aggregate that changes after financial/task writes |
| Referral summary / codes / active list | 5 minutes | 5 minutes | `atlas:user:{id}:referrals:*` | User-scoped aggregate and hierarchy data |
| Payment history and overview | 1 minute | 1 minute | `atlas:user:{id}:payments:*` | Changes after deposits, withdrawals, and approval workflows |
| Withdrawal accounts | 10 minutes | Browser only | `['withdrawalAccounts']` | Low-change user settings |
| Notifications | 20 seconds | 20 seconds | `atlas:user:{id}:notifications:*` | Short-lived freshness with a single polling owner |
| Admin statistics | 30 seconds | 30 seconds | `atlas:admin:stats` | Aggregate dashboard data shared by administrators |

The frontend uses `refetchOnWindowFocus: true` for most queries and a focused 20-second interval only for notifications. This replaces duplicated component-level intervals with one request stream shared across all notification consumers.

## Invalidation contract

| Successful mutation | Invalidate or update |
| --- | --- |
| Task completed | Current user tasks, dashboard, authenticated user, referral data; every rewarded referrer’s dashboard, authenticated user, and referrals |
| Plan purchased or upgraded | Plans, current user tasks, dashboard, authenticated user, referral data; affected referrer representations |
| Deposit created or payment approved/rejected | Affected user payment history, payment overview, dashboard, authenticated user; admin payments and statistics |
| Withdrawal requested | Current user payment history, payment overview, dashboard, authenticated user |
| Profile or withdrawal-password updated | Current user profile and authenticated user |
| Withdrawal account created | Current user withdrawal accounts |
| Notification marked read, deleted, cleared, or sent | Affected user notifications; global sends invalidate the global notification list by TTL and admin readers immediately |
| App configuration changed | Shared app-configuration cache and all config queries |
| Admin task, plan, user, payment, certification, or referral edits | Corresponding admin list/statistics keys plus any affected user-facing cache group |

Backend invalidation runs **only after `db.commit()` succeeds**. Frontend mutations use optimistic state only when the interaction is safely reversible; failed mutations restore the saved snapshot and refetch the affected key.

## Database optimization plan

The schema adds composite indexes that match the highest-frequency filters and sort orders:

| Table | Composite index | Primary consumers |
| --- | --- | --- |
| `user_video_tasks` | `(user_id, status, completed_at)` and `(user_id, video_task_id)` | Dashboard counts/history and task completion checks |
| `earnings_logs` | `(user_id, created_at)` | Dashboard period sums |
| `notifications` | `(user_id, created_at)` | User notification list ordered by recency |
| `payments` | `(user_id, created_at)` | History and payment overview |
| `withdrawal_accounts` | `(user_id, is_primary)` | Withdrawal form/account lookup |
| `user_plan_history` | `(user_id, status, expires_at)` | Referral activity and plan validity |
| `referral_codes` | `(user_id)` | Referral summary and codes |
| `referral_relationships` | `(referrer_id)` | Referral hierarchy traversal |

List APIs will accept bounded `page` and `limit` parameters where relevant. History and administrative tables must order deterministically, select only the columns required for their response, and never load unbounded record sets by default.

## Operational safeguards

Redis connection details are supplied through `REDIS_URL` or `APPSETTING_REDIS_URL`. `CACHE_ENABLED=false` disables backend caching explicitly. When Redis is not configured or is temporarily unavailable, the cache service records the condition and falls back to a small process-local TTL cache. This preserves availability for local development and automated tests while retaining Redis as the production shared-cache implementation.

The cache service must emit structured hit, miss, set, delete, and fallback logs without recording authentication tokens, passwords, payment proofs, or personally sensitive values. Deployment health checks should include Redis reachability, cache error rate, PostgreSQL query count, p50/p95 API latency, and the frontend’s duplicate request count.

## Verification criteria

The implementation is complete when the following conditions hold:

1. Navigation between cached routes does not issue duplicated requests for the same fresh query key.
2. A task completion, plan change, payment, or notification mutation immediately updates or invalidates all dependent frontend state.
3. Redis failures still return successful PostgreSQL-backed API responses.
4. Repeated dashboard, task, plans, configuration, and notification reads generate cache hits after the first request.
5. The application has automated coverage for cache key construction, TTL behavior, invalidation, pagination bounds, and the task-completion invalidation path.
6. Frontend type checking, linting, production build, backend tests, and API smoke checks pass before release.

## References

This document describes the Atlas implementation contract. It intentionally contains no external runtime assumptions beyond the repositories’ existing FastAPI, SQLAlchemy, React, and Axios stack.
