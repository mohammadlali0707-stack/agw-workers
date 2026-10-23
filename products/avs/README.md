# AVS Workspace

AVS monorepo foundation workspace configured with pnpm workspaces, containerized local services, and GitHub Actions CI.

## Architecture

The workspace follows a monorepo structure separating applications from shared libraries:

```text
products/avs/
├── apps/               # Deployable microservices and frontends
├── packages/           # Shared libraries, utilities, and models
├── package.json        # Root workspace configuration & recursive scripts
├── pnpm-workspace.yaml # Workspace package globs (packages/*, apps/*)
└── README.md           # Workspace documentation
```

### Infrastructure Services
Local dependencies run via the root `docker-compose.yml`:
- **PostgreSQL 16** (`avs-postgres`): Relational data store on port `5432` with automated `pg_isready` health check and persistent volume `postgres_data`.
- **Redis 7** (`avs-redis`): In-memory caching and message queue on port `6379` with `redis-cli ping` health check and persistent volume `redis_data`.

## Setup & Prerequisites

### Prerequisites
- **Node.js**: >= 20.0.0
- **pnpm**: >= 9.0.0
- **Docker & Docker Compose**: v2+

### Installation

1. Install workspace dependencies:
   ```bash
   cd products/avs
   pnpm install
   ```

2. Start local service infrastructure:
   ```bash
   # From the repository root
   docker compose up -d
   ```

3. Verify service health:
   ```bash
   docker compose ps
   ```

4. Stop local services:
   ```bash
   docker compose down
   ```

## Workspace Scripts

Run commands across all packages using pnpm workspace filtering:

- `pnpm run build`: Recursively builds all packages and apps.
- `pnpm run lint`: Runs linter checks across all packages.
- `pnpm run test`: Runs test suites against active PostgreSQL and Redis services.
- `pnpm run clean`: Cleans build artifacts and caches.

## CI/CD Pipeline

The `.github/workflows/products-avs-ci.yml` pipeline triggers on push and pull requests affecting `products/avs/**`:
- Provisions containerized PostgreSQL 16 and Redis 7 service instances with health checks.
- Sets up Node.js 20 and pnpm 9.
- Executes `pnpm run lint` and `pnpm run test` with service connection strings.
