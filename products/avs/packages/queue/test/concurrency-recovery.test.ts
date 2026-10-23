import { describe, it, expect, afterAll } from 'vitest';
import { Queue } from 'bullmq';
import {
  createJob,
  createWorker,
  InMemoryJobStore,
  JobTemplate,
  LedgerClient,
  ProviderRouter,
  ProviderSubmission,
} from '../src/index';

class MockCreditLedger implements LedgerClient {
  public holds = new Map<string, { id: string; userId: string; amount: number; status: 'held' | 'captured' | 'released' }>();
  public captureCalls = new Map<string, number>();
  public releaseCalls = new Map<string, number>();

  async holdCredits(p: { userId: string; amount: number; referenceId?: string }) {
    const holdId = `hold_${p.referenceId || Math.random().toString(36).slice(2, 8)}`;
    this.holds.set(holdId, { id: holdId, userId: p.userId, amount: p.amount, status: 'held' });
    return { holdId, success: true };
  }

  async captureHold(id: string) {
    const h = this.holds.get(id);
    if (!h) throw new Error(`Hold not found: ${id}`);
    if (h.status !== 'held') throw new Error(`Hold already resolved: ${id} is ${h.status}`);
    h.status = 'captured';
    this.captureCalls.set(id, (this.captureCalls.get(id) || 0) + 1);
    return { success: true };
  }

  async releaseHold(id: string) {
    const h = this.holds.get(id);
    if (!h) throw new Error(`Hold not found: ${id}`);
    if (h.status !== 'held') throw new Error(`Hold already resolved: ${id} is ${h.status}`);
    h.status = 'released';
    this.releaseCalls.set(id, (this.releaseCalls.get(id) || 0) + 1);
    return { success: true };
  }

  getActiveHolds() {
    return Array.from(this.holds.values()).filter((h) => h.status === 'held');
  }
}

class MockVideoProvider implements ProviderRouter {
  public submissions = new Map<string, ProviderSubmission>();

  async submit(j: { jobId: string; templateId: string; input: Record<string, unknown>; userId: string }): Promise<ProviderSubmission> {
    const outcome = j.input.desiredOutcome || 'done';
    const providerJobId = `pjob_${j.jobId}`;
    let sub: ProviderSubmission;

    if (outcome === 'done') {
      sub = { providerJobId, status: 'done', output: { url: `https://mock.cdn/video_${j.jobId}.mp4` } };
    } else if (outcome === 'failed') {
      sub = { providerJobId, status: 'failed', error: 'Simulated render failure' };
    } else {
      sub = { providerJobId, status: 'processing' };
    }

    this.submissions.set(providerJobId, sub);
    return sub;
  }

  async getStatus(providerJobId: string): Promise<ProviderSubmission> {
    return this.submissions.get(providerJobId) || { providerJobId, status: 'failed', error: 'Job not found' };
  }
}

describe('Queue & Worker Concurrency and Recovery Test Suite', () => {
  const redisConfig = { host: '127.0.0.1', port: 6379, maxRetriesPerRequest: null };
  const template: JobTemplate = { id: 'persian-promo', costCredits: 10, requiredFields: ['prompt'] };

  it('executes 50 concurrent jobs with mixed outcomes and leaves zero dangling holds', async () => {
    const queueName = `concurrency-${Date.now()}`;
    const ledger = new MockCreditLedger();
    const router = new MockVideoProvider();
    const jobStore = new InMemoryJobStore();
    const queue = new Queue(queueName, { connection: redisConfig });

    const outcomes: Array<'done' | 'failed' | 'timeout'> = ['done', 'failed', 'timeout'];
    const jobPromises = Array.from({ length: 50 }, (_, i) =>
      createJob(
        { userId: `user_${i}`, template, input: { prompt: `Prompt ${i}`, desiredOutcome: outcomes[i % 3] } },
        { queue, ledger, jobStore, queueName }
      )
    );

    const created = await Promise.all(jobPromises);
    expect(created).toHaveLength(50);

    const worker = createWorker(queueName, {
      redis: redisConfig,
      ledger,
      providersRouter: router,
      jobStore,
      concurrency: 25,
      pollIntervalMs: 5,
      timeoutMs: 15,
    });

    const start = Date.now();
    let allDone = false;
    while (!allDone && Date.now() - start < 15000) {
      const records = await Promise.all(created.map((j) => jobStore.get(j.jobId)));
      allDone = records.every((r) => r?.status === 'done' || r?.status === 'refunded');
      if (!allDone) await new Promise((r) => setTimeout(r, 80));
    }

    expect(allDone).toBe(true);

    const finalRecords = await Promise.all(created.map((j) => jobStore.get(j.jobId)));
    for (const record of finalRecords) {
      expect(record).toBeDefined();
      expect(['done', 'refunded']).toContain(record!.status);
      const hold = ledger.holds.get(record!.holdId);
      if (record!.status === 'done') {
        expect(hold?.status).toBe('captured');
        expect(record!.output).toBeDefined();
      } else {
        expect(hold?.status).toBe('released');
        expect(record!.error).toBeDefined();
      }
    }

    expect(ledger.getActiveHolds()).toHaveLength(0);

    await worker.close();
    await queue.close();
  });

  it('resumes active or queued jobs cleanly after simulated worker restart without duplicate charges or lost state', async () => {
    const queueName = `recovery-${Date.now()}`;
    const ledger = new MockCreditLedger();
    const router = new MockVideoProvider();
    const jobStore = new InMemoryJobStore();
    const queue = new Queue(queueName, { connection: redisConfig });

    const created = await Promise.all(
      Array.from({ length: 10 }, (_, i) =>
        createJob(
          { userId: `user_rec_${i}`, template, input: { prompt: `Prompt ${i}`, desiredOutcome: i % 2 === 0 ? 'done' : 'failed' } },
          { queue, ledger, jobStore, queueName }
        )
      )
    );

    const worker1 = createWorker(queueName, {
      redis: redisConfig,
      ledger,
      providersRouter: router,
      jobStore,
      concurrency: 1,
      pollIntervalMs: 5,
      timeoutMs: 25,
    });

    await new Promise((r) => setTimeout(r, 40));
    await worker1.close();

    const worker2 = createWorker(queueName, {
      redis: redisConfig,
      ledger,
      providersRouter: router,
      jobStore,
      concurrency: 10,
      pollIntervalMs: 5,
      timeoutMs: 25,
    });

    const start = Date.now();
    let allDone = false;
    while (!allDone && Date.now() - start < 10000) {
      const records = await Promise.all(created.map((j) => jobStore.get(j.jobId)));
      allDone = records.every((r) => r?.status === 'done' || r?.status === 'refunded');
      if (!allDone) await new Promise((r) => setTimeout(r, 80));
    }

    expect(allDone).toBe(true);

    const finalRecords = await Promise.all(created.map((j) => jobStore.get(j.jobId)));
    for (const record of finalRecords) {
      expect(record).toBeDefined();
      expect(['done', 'refunded']).toContain(record!.status);
      const captures = ledger.captureCalls.get(record!.holdId) || 0;
      const releases = ledger.releaseCalls.get(record!.holdId) || 0;

      if (record!.status === 'done') {
        expect(captures).toBe(1);
        expect(releases).toBe(0);
      } else {
        expect(captures).toBe(0);
        expect(releases).toBe(1);
      }
    }

    expect(ledger.getActiveHolds()).toHaveLength(0);

    await worker2.close();
    await queue.close();
  });
});
