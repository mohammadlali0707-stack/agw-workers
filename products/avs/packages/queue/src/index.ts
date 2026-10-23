import { Queue, Worker, Job } from 'bullmq';
import {
  CreateJobInput, CreateJobOptions, JobData, JobRecord, JobResult,
  JobTemplate, LedgerClient, ProviderRouter, ProviderSubmission, JobStore, WorkerConfig,
} from './types';

export * from './types';
export const DEFAULT_POLL_INTERVAL_MS = 10_000;
export const DEFAULT_TIMEOUT_MS = 15 * 60 * 1000;

const templates = new Map<string, JobTemplate>();
export const registerTemplate = (t: JobTemplate) => templates.set(t.id, t);
export const getTemplate = (id: string) => templates.get(id);

export function validateJobInput(t: JobTemplate, input: Record<string, unknown>): { valid: boolean; error?: string } {
  if (!input || typeof input !== 'object') return { valid: false, error: 'Input must be an object' };
  for (const f of t.requiredFields || []) {
    if (input[f] === undefined || input[f] === null || input[f] === '') return { valid: false, error: `Missing ${f}` };
  }
  for (const [k, typ] of Object.entries(t.schema || {})) {
    if (input[k] !== undefined && typeof input[k] !== typ) return { valid: false, error: `Invalid type for ${k}` };
  }
  if (t.validate) {
    const res = t.validate(input);
    return typeof res === 'boolean' ? (res ? { valid: true } : { valid: false, error: 'Validation failed' }) : res;
  }
  return { valid: true };
}

export class InMemoryLedger implements LedgerClient {
  public holds = new Map<string, { userId: string; amount: number; status: 'held' | 'captured' | 'released' }>();
  async holdCredits(p: { userId: string; amount: number; referenceId?: string }) {
    const holdId = `hold_${p.referenceId || Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    this.holds.set(holdId, { userId: p.userId, amount: p.amount, status: 'held' });
    return { holdId, success: true };
  }
  async captureHold(id: string) { const h = this.holds.get(id); if (h) h.status = 'captured'; return { success: true }; }
  async releaseHold(id: string) { const h = this.holds.get(id); if (h) h.status = 'released'; return { success: true }; }
}

export class InMemoryProviderRouter implements ProviderRouter {
  public submissions = new Map<string, ProviderSubmission>();
  async submit(j: { jobId: string; templateId: string; input: Record<string, unknown>; userId: string }) {
    const sub: ProviderSubmission = { providerJobId: `pjob_${j.jobId}`, status: 'done', output: { processed: true, ...j.input } };
    this.submissions.set(sub.providerJobId, sub);
    return sub;
  }
  async getStatus(id: string) { return this.submissions.get(id) || { providerJobId: id, status: 'failed', error: 'Not found' }; }
}

export class InMemoryJobStore implements JobStore {
  public records = new Map<string, JobRecord>();
  async save(r: JobRecord) { this.records.set(r.jobId, { ...r }); }
  async update(id: string, u: Partial<JobRecord>) {
    const cur = this.records.get(id);
    if (cur) this.records.set(id, { ...cur, ...u, updatedAt: Date.now() });
  }
  async get(id: string) { return this.records.get(id); }
}

const defaultLedger = new InMemoryLedger();
const defaultRouter = new InMemoryProviderRouter();
const defaultStore = new InMemoryJobStore();

export const getLedgerClient = (): LedgerClient => {
  try { const m = require('packages/ledger'); return m.ledger || m.default || m; } catch { return defaultLedger; }
};
export const getProvidersRouter = (): ProviderRouter => {
  try { const m = require('packages/providers'); return m.providersRouter || m.router || m.default || m; } catch { return defaultRouter; }
};
export const getDefaultJobStore = (): JobStore => defaultStore;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function createJob(params: CreateJobInput, options: CreateJobOptions = {}): Promise<JobRecord> {
  const template = typeof params.template === 'string' ? getTemplate(params.template) : params.template;
  if (!template) throw new Error(`Template not found: ${String(params.template)}`);

  const val = validateJobInput(template, params.input);
  if (!val.valid) throw new Error(`Validation failed: ${val.error}`);

  const jobId = params.jobId || `job_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const ledger = options.ledger || getLedgerClient();
  const hold = await ledger.holdCredits({ userId: params.userId, amount: template.costCredits, referenceId: jobId });
  if (!hold?.holdId) throw new Error(`Credit hold failed for user ${params.userId}`);

  const jobData: JobData = {
    jobId, userId: params.userId, templateId: template.id, holdId: hold.holdId,
    input: params.input, status: 'queued', createdAt: Date.now(),
  };

  const queue = options.queue || new Queue(options.queueName || 'avs-jobs', {
    connection: (options.redis as any) || { host: '127.0.0.1', port: 6379, maxRetriesPerRequest: null },
  });
  await queue.add('process-job', jobData, { jobId });

  const record: JobRecord = { ...jobData, updatedAt: Date.now() };
  await (options.jobStore || getDefaultJobStore()).save(record);
  return record;
}

export async function processJob(
  jobData: JobData,
  deps: { providersRouter: ProviderRouter; ledger: LedgerClient; jobStore?: JobStore; pollIntervalMs?: number; timeoutMs?: number }
): Promise<JobResult> {
  const { jobId, templateId, input, userId, holdId } = jobData;
  const pollIntervalMs = deps.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const timeoutMs = deps.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const { providersRouter, ledger, jobStore } = deps;

  await jobStore?.update(jobId, { status: 'processing', updatedAt: Date.now() });
  try {
    const submission = await providersRouter.submit({ jobId, templateId, input, userId });
    const start = Date.now();
    let current = submission;

    while (current.status !== 'done' && current.status !== 'failed') {
      if (Date.now() - start >= timeoutMs) throw new Error(`Timeout after ${timeoutMs}ms`);
      await sleep(pollIntervalMs);
      current = await providersRouter.getStatus(submission.providerJobId);
    }

    if (current.status === 'done') {
      await ledger.captureHold(holdId);
      const res: JobResult = { jobId, status: 'done', output: current.output, updatedAt: Date.now() };
      await jobStore?.update(jobId, { status: 'done', output: current.output, updatedAt: Date.now() });
      return res;
    }
    throw new Error(current.error || 'Provider execution failed');
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    await ledger.releaseHold(holdId, msg);
    const res: JobResult = { jobId, status: 'refunded', error: msg, updatedAt: Date.now() };
    await jobStore?.update(jobId, { status: 'refunded', error: msg, updatedAt: Date.now() });
    return res;
  }
}

export function createWorker(queueName = 'avs-jobs', config: WorkerConfig = {}): Worker {
  const ledger = config.ledger || getLedgerClient();
  const providersRouter = config.providersRouter || getProvidersRouter();
  const jobStore = config.jobStore || getDefaultJobStore();
  const connection = (config.redis as any) || { host: '127.0.0.1', port: 6379, maxRetriesPerRequest: null };

  return new Worker(
    queueName,
    async (job: Job) => processJob(job.data as JobData, {
      providersRouter, ledger, jobStore, pollIntervalMs: config.pollIntervalMs, timeoutMs: config.timeoutMs,
    }),
    { connection, concurrency: config.concurrency || 20 }
  );
}
