# AdPulseAI Referral Commission Policy

## Separate concepts

- **Membership level** selects the applicable membership commission rate and price basis.
- **Referral depth** selects the Tier A/B/C maximum ceiling.

## Referral-depth ceilings

- Tier A (direct): **10% maximum**
- Tier B (second level): **4% maximum**
- Tier C (third level): **1% maximum**

These ceilings remain separate from membership-level rates.

## Membership policy

Plans support:

- `membership_level`: explicit hierarchy; names such as `LV1`–`LV5` are inferred only when unset.
- `membership_commission_rate`: an explicitly configured decimal fraction, for example `0.10` means 10%.

The applicable membership plan is selected per upline/downline pair:

- Downline is higher than upline: use the **upline membership plan**.
- Same membership level: use the **downline membership plan**.
- Upline is higher than downline: use the **downline membership plan**.

The effective rate is:

```text
effective_rate = min(applicable_membership_rate, referral_depth_ceiling)
```

If the applicable plan has no configured membership rate, the engine **fails closed** and creates no commission. It never substitutes a referral-depth rate for a missing membership rate.

## Commission price basis

For each upline, the commission base is the price of the applicable membership plan—not the provider amount, not the full upgrade price for every upline, and not the price difference.

Examples:

- LV1 upline referring an LV2 purchase: 10% of LV1 $20 = **$2.00**.
- Same-level LV1 referral: 10% of LV1 $20 = **$2.00**.
- LV2 upline referring an LV1 purchase: 10% of LV1 $20 = **$2.00**.

## Qualifying transactions

- Successful confirmed initial purchases and upgrades can generate commissions.
- Renewals after the first paid purchase remain excluded unless explicitly enabled.
- Free plans, failed payments, refunds, and zero-value transactions generate no commission.
- PesaFlux plan payments use the verified successful payment record and the same commission engine.
- Amounts are rounded to cents using `ROUND_HALF_UP` before wallet crediting.

## Current configured rates

- LV1 ($20): **10%**, explicitly configured.
- LV2 ($50): **not configured**.
- LV3 ($100): **not configured**.
- LV4 ($150): **not configured**.
- LV5 ($200): existing catalog entry left untouched; rate **not configured**.

Payouts involving an applicable level without a configured rate are skipped rather than guessed.

## Idempotency and audit

Each commission has an immutable `commission_awards` record keyed by source type, source ID, upline, and referral tier. A unique database index prevents duplicate awards. The record stores the applicable price basis, effective rate, tier, recipient, and credited amount. Wallet and earnings-log updates happen in the same surrounding transaction.
