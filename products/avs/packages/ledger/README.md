# @avs/ledger

Core Credit Ledger package providing atomic credit wallet operations with Prisma interactive transactions.

## APIs

The `CreditLedgerService` exposes four core methods:

- `topup(userId, amount, refId)`: Atomically adds credits to a user's wallet with idempotency by reference ID.
- `hold(userId, amount, refId)`: Atomically reserves credits from user's available balance, creating a `PENDING` hold record.
- `capture(holdId)`: Atomically finalizes a pending hold into `CAPTURED` status; strictly rejects double capture.
- `release(holdId)`: Atomically cancels a pending hold into `RELEASED` status and restores reserved credits to user balance.

## Transaction Invariants

1. **Interactive Atomic Transactions**: All ledger operations execute inside a single Prisma interactive transaction (`prisma.$transaction`), guaranteeing ACID isolation and consistency.
2. **Non-Negative Balance Invariant**: Available credit balance is validated before creating a hold. If balance < amount, `InsufficientBalanceError` is raised and the transaction aborts.
3. **Idempotent Topup**: Re-submitting a topup with an existing `refId` for the same user and amount safely returns the existing record without duplicating balance additions. Mismatched references raise `DuplicateRefError`.
4. **Strict Hold Resolution**: Holds exist in `PENDING`, `CAPTURED`, or `RELEASED` states. Only `PENDING` holds can be captured or released. Double captures or releases strictly throw `HoldAlreadyResolvedError`.
5. **Credit Restoration**: A release atomically marks the hold as `RELEASED` and increments the user's wallet balance by the held amount.

## Domain Errors

- `InsufficientBalanceError`: Thrown when available balance is insufficient for a hold.
- `HoldAlreadyResolvedError`: Thrown when trying to capture or release an already resolved hold.
- `DuplicateRefError`: Thrown on reference identifier collisions.
