# @avs/queue

Core queue management and background worker implementation for AVS using BullMQ and Redis.

## Overview

- **createJob**: Validates job input against template specifications, places an atomic credit hold via `packages/ledger`, and enqueues the job into BullMQ.
- **Background Worker**: Processes queued jobs, submits to `packages/providers` router, and polls provider status every 10 seconds up to a 15-minute timeout.
- **Hold Resolution**:
  - **On 'done'**: Persists job output and captures the credit hold via ledger.
  - **On failure or timeout**: Releases the credit hold via ledger and marks job status as `'refunded'`.

## Exported Interfaces

- `createJob(params, options)`: Validates input, places hold, enqueues job into BullMQ.
- `createWorker(queueName, config)`: Worker instance with provider dispatch, polling, and hold management.
- `processJob(jobData, deps)`: Core execution logic handling polling loop and hold capture/refund.
- `validateJobInput(template, input)`: Template input validation.
- Interfaces: `JobTemplate`, `JobData`, `JobRecord`, `JobResult`, `LedgerClient`, `ProviderRouter`, `JobStore`.
