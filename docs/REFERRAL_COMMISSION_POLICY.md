# AdPulseAI Referral Commission Policy

## Baseline rates

- Tier A (direct): **10% maximum**
- Tier B (second level): **4% maximum**
- Tier C (third level): **1% maximum**

## Membership policy

Plans now support:

- `membership_level`: explicit hierarchy; existing names such as `LV1`–`LV5` are inferred when unset.
- `membership_commission_rate`: optional decimal fraction, for example `0.08` means 8%.

Until an administrator explicitly configures a membership rate, the established tier baseline remains active. This avoids silently changing existing payouts.

The effective rate is:

```text
effective_rate = min(tier_baseline, applicable_membership_rate)
```

The applicable membership rate is selected as follows:

- Downline is higher than upline: use the upline's rate.
- Same membership level: use the downline's rate.
- Upline is higher than downline: use the downline's rate.

The tier baseline is always the ceiling.

## Eligible transaction amount

- Paid initial purchase: full verified plan amount.
- Upgrade: `max(new plan price - previous eligible plan value, 0)`.
- Renewals after the first paid purchase: no commission unless a future business rule explicitly enables them.
- Free plans, failed payments, refunds, and zero-value transactions: no commission.
- PesaFlux plan payments use the verified payment record and are processed through the same engine.

All amounts are rounded to cents using `ROUND_HALF_UP` before wallet crediting.

## Idempotency and audit

Each commission has an immutable `commission_awards` record keyed by source type, source ID, upline, and tier. A unique database index plus a savepoint protects against duplicate and concurrent callbacks. The record stores the eligible amount, effective rate, tier, recipient, and credited amount.

## Examples using the seeded catalog

With no membership-specific rates configured:

- LV1 ($20) initial purchase:
  - Tier A: $2.00
  - Tier B: $0.80
  - Tier C: $0.20
- LV1 ($20) to LV3 ($100) upgrade:
  - Eligible incremental amount: $80
  - Tier A: $8.00
  - Tier B: $3.20
  - Tier C: $0.80

These rates remain configurable from the admin plan editor and are capped by the tier baselines.
