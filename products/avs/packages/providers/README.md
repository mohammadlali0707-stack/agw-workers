# @avs/providers

Video provider router and adapters for the AVS video pipeline (AVS-M03).

## Architecture & Interfaces

- `VideoProvider`: Base interface defining `estimateCostUsd`, `submit`, and `poll`.
- `MockVideoProvider`: Deterministic mock simulating `done`, `failed`, and `timeout`.
- `FalVideoProvider`: Adapter for fal.ai queue API reading credentials strictly from environment (`FAL_KEY` / `FAL_API_KEY`).
- `ProviderRouter`: Registry and job executor with automatic one-time fallback upon primary failure.

## Testing

```bash
pnpm --filter @avs/providers test
```
