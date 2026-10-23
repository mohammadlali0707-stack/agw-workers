export type PollStatus = 'done' | 'failed' | 'timeout' | 'processing';

export interface GenerationParams {
  prompt: string;
  durationSeconds?: number;
  aspectRatio?: string;
  model?: string;
  webhookUrl?: string;
  mockOutcome?: PollStatus;
  failOnSubmit?: boolean;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SubmitResult {
  jobId: string;
  provider: string;
  status?: PollStatus;
  costUsd?: number;
  metadata?: Record<string, unknown>;
}

export interface PollResult {
  status: PollStatus;
  videoUrl?: string;
  error?: string;
  progress?: number;
  metadata?: Record<string, unknown>;
}

export interface VideoProvider {
  readonly name: string;
  estimateCostUsd(params: GenerationParams): Promise<number>;
  submit(params: GenerationParams): Promise<SubmitResult>;
  poll(jobId: string): Promise<PollResult>;
}

export interface TemplateLike {
  primary_provider: string;
  fallback_provider?: string;
  [key: string]: unknown;
}

export interface ExecuteOptions {
  template?: TemplateLike;
  primaryProvider?: string;
  fallbackProvider?: string;
  params: GenerationParams;
  pollIntervalMs?: number;
  maxPollAttempts?: number;
}

export interface ExecuteResult {
  success: boolean;
  provider: string;
  jobId: string;
  pollResult: PollResult;
  fallbackUsed: boolean;
  error?: string;
}
