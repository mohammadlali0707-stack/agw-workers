import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { prisma, JobStatus, LedgerType } from '../src/index.js';
import { seed } from '../src/seed.js';

describe('AVS PostgreSQL Database Suite', () => {
  beforeAll(async () => { await prisma.$connect(); });
  afterAll(async () => { await prisma.$disconnect(); });

  it('connects to PostgreSQL database', async () => {
    const res = await prisma.$queryRaw<Array<{ ok: number }>>`SELECT 1 as ok`;
    expect(res[0]?.ok).toBe(1);
  });

  it('creates user and wallet with relations', async () => {
    const user = await prisma.user.create({
      data: {
        email: `alice-${Date.now()}@avs.test`, name: 'Alice',
        wallets: { create: { address: `0x${Date.now().toString(16)}`, balance: 250.0 } },
      },
      include: { wallets: true },
    });
    expect(user.id).toBeDefined();
    expect(user.wallets).toHaveLength(1);
    expect(Number(user.wallets[0].balance)).toBe(250);
  });

  it('creates ledger entries and performs operations', async () => {
    const user = await prisma.user.create({
      data: { email: `bob-${Date.now()}@avs.test`, wallets: { create: { address: `0xbob${Date.now().toString(16)}`, balance: 0 } } },
      include: { wallets: true },
    });
    const entry = await prisma.ledgerEntry.create({
      data: { walletId: user.wallets[0].id, amount: 50.0, type: LedgerType.CREDIT, reference: 'tx-1' },
      include: { wallet: true },
    });
    expect(entry.id).toBeDefined();
    expect(entry.type).toBe(LedgerType.CREDIT);
    expect(entry.wallet.id).toBe(user.wallets[0].id);
  });

  it('creates template and job with status lifecycle', async () => {
    const user = await prisma.user.create({ data: { email: `carol-${Date.now()}@avs.test` } });
    const template = await prisma.template.create({
      data: { userId: user.id, name: 'ValidationTask', config: { timeout: 30 } },
    });
    const job = await prisma.job.create({
      data: { userId: user.id, templateId: template.id, payload: { step: 1 } },
    });
    expect(job.status).toBe(JobStatus.PENDING);
    const updated = await prisma.job.update({
      where: { id: job.id },
      data: { status: JobStatus.COMPLETED, result: { success: true } },
    });
    expect(updated.status).toBe(JobStatus.COMPLETED);
    expect(updated.result).toEqual({ success: true });
  });

  it('runs seed script and populates data', async () => {
    const { user, job } = await seed();
    expect(user.id).toBeDefined();
    expect(job.userId).toBe(user.id);
    expect(await prisma.user.count()).toBeGreaterThan(0);
  });
});
