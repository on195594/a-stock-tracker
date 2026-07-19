# Claude production review resolution

## Resolution

Both actionable findings were fixed.

- P2: added the tracked `qualitative_v2_production_authorizations.json` ledger. The consumed authorization `qualitative-v2-prod-canary-20260719-01` is permanently marked retired at 45/45 attempts, with no active replacement authorization. The collector validates this ledger before company resolution, artifact creation, database opening, or network access.
- P3: widened the v2 adapter fallback boundary to ordinary `Exception` subclasses. The existing legacy getter remains outside the boundary, and process-control `BaseException` subclasses are not swallowed.

## Negative production smoke

Reusing the retired authorization returned exit code 2 with `production authorization is retired and cannot be reused`. The requested run root was not created, and no CNINFO call was issued.

## Remaining state

The five-stock production read canary remains enabled and fallback-only. There are no adopted v2 rows; adding another collection authorization requires an explicit tracked ledger update tied to a new user approval.
