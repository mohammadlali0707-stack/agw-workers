import { test } from 'vitest';
import assert from 'node:assert/strict';
import {
  createJob, processJob, validateJobInput,
  InMemoryLedger, InMemoryProviderRouter, InMemoryJobStore, JobTemplate,
} from './index';

test('validateJobInput checks fields and schema', () => {
  const tpl: JobTemplate = { id: 't1', costCredits: 5, requiredFields: ['prompt'], schema: { prompt: 'string' } };
  assert.equal(validateJobInput(tpl, { prompt: 'hi' }).valid, true);
  assert.equal(validateJobInput(tpl, {}).valid, false);
});

test('createJob validates, holds credits, and enqueues', async () => {
  const ledger = new InMemoryLedger();
  const jobStore = new InMemoryJobStore();
  const queueJobs: any[] = [];
  const mockQueue = { add: async (_n: string, d: any) => { queueJobs.push(d); return d; } };
  const tpl: JobTemplate = { id: 't1', costCredits: 10, requiredFields: ['text'] };

  const job = await createJob({ userId: 'u1', template: tpl, input: { text: 'hello' } }, { ledger, jobStore, queue: mockQueue });
  assert.equal(job.status, 'queued');
  assert.equal(queueJobs.length, 1);
  assert.equal(ledger.holds.get(job.holdId)?.status, 'held');
});

test('processJob handles completion, failure, and timeout', async () => {
  const ledger = new InMemoryLedger();
  const router = new InMemoryProviderRouter();
  const store = new InMemoryJobStore();
  const { holdId } = await ledger.holdCredits({ userId: 'u1', amount: 10 });
  const jobData = { jobId: 'j1', userId: 'u1', templateId: 't1', holdId, input: {}, status: 'queued' as const, createdAt: Date.now() };

  const doneRes = await processJob(jobData, { providersRouter: router, ledger, jobStore: store, pollIntervalMs: 1 });
  assert.equal(doneRes.status, 'done');
  assert.equal(ledger.holds.get(holdId)?.status, 'captured');

  const { holdId: h2 } = await ledger.holdCredits({ userId: 'u1', amount: 10 });
  const failRouter = { submit: async () => ({ providerJobId: 'p2', status: 'failed' as const, error: 'fail' }), getStatus: async () => ({ providerJobId: 'p2', status: 'failed' as const }) };
  const failRes = await processJob({ ...jobData, jobId: 'j2', holdId: h2 }, { providersRouter: failRouter, ledger, jobStore: store, pollIntervalMs: 1 });
  assert.equal(failRes.status, 'refunded');
  assert.equal(ledger.holds.get(h2)?.status, 'released');

  const { holdId: h3 } = await ledger.holdCredits({ userId: 'u1', amount: 10 });
  const slowRouter = { submit: async () => ({ providerJobId: 'p3', status: 'processing' as const }), getStatus: async () => ({ providerJobId: 'p3', status: 'processing' as const }) };
  const toRes = await processJob({ ...jobData, jobId: 'j3', holdId: h3 }, { providersRouter: slowRouter, ledger, jobStore: store, pollIntervalMs: 1, timeoutMs: 5 });
  assert.equal(toRes.status, 'refunded');
  assert.equal(ledger.holds.get(h3)?.status, 'released');
});
