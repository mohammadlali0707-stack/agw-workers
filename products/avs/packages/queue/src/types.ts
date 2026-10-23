export type JobStatus = 'queued' | 'processing' | 'done' | 'failed' | 'refunded';
export type ProviderStatus = 'pending' | 'processing' | 'done' | 'failed';

export interface JobTemplate {
  id: string;
  name?: string;
  costCredits: number;
  requiredFields?: string[];
  schema?: Record<string, string>;
  validate?: (input: Record<string, unknown>) => boolean | { valid: boolean; error?: string };
}

export interface JobData {
  jobId: string;
  userId: string;
  templateId: string;
  holdId: string;
  input: Record<string, unknown>;
  status: JobStatus;
  createdAt: number;
}

export interface JobRecord extends JobData {
  output?: unknown;
  error?: string;
  updatedAt: number;
}

export interface JobResult {
  jobId: string;
  status: 'done' | 'refunded';
  output?: unknown;
  error?: string;
  updatedAt: number;
}

export interface LedgerClient {
  holdCredits(params: { userId: string; amount: number; referenceId?: string }): Promise<{ holdId: string; success?: boolean }>;
  captureHold(holdId: string): Promise<{ success?: boolean }>;
  releaseHold(holdId: string, reason?: string): Promise<{ success?: boolean }>;
}

export interface ProviderSubmission {
  providerJobId: string;
  status: ProviderStatus;
  output?: unknown;
  error?: string;
}

export interface ProviderRouter {
  submit(job: { jobId: string; templateId: string; input: Record<string, unknown>; userId: string }): Promise<ProviderSubmission>;
  getStatus(providerJobId: string): Promise<ProviderSubmission>;
}

export interface JobStore {
  save(record: JobRecord): Promise<void>;
  update(jobId: string, update: Partial<JobRecord>): Promise<void>;
  get(jobId: string): Promise<JobRecord | undefined>;
}

export interface CreateJobInput {
  userId: string;
  template: JobTemplate | string;
  input: Record<string, unknown>;
  jobId?: string;
}

export interface CreateJobOptions {
  queue?: { add: (name: string, data: any, opts?: any) => Promise<any> };
  queueName?: string;
  redis?: unknown;
  ledger?: LedgerClient;
  jobStore?: JobStore;
}

export interface WorkerConfig {
  queueName?: string;
  redis?: unknown;
  pollIntervalMs?: number;
  timeoutMs?: number;
  providersRouter?: ProviderRouter;
  ledger?: LedgerClient;
  jobStore?: JobStore;
}
