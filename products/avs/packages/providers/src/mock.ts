import type { GenerationParams, PollResult, PollStatus, SubmitResult, VideoProvider } from './types.js';

export interface MockProviderOptions {
  name?: string;
  defaultOutcome?: PollStatus;
  costUsd?: number;
  failOnSubmit?: boolean;
}

export class MockVideoProvider implements VideoProvider {
  readonly name: string;
  public defaultOutcome: PollStatus;
  public costUsd: number;
  public failOnSubmit: boolean;
  private jobCounter = 0;
  private jobs = new Map<string, { outcome: PollStatus; params: GenerationParams }>();

  constructor(options: MockProviderOptions = {}) {
    this.name = options.name ?? 'mock';
    this.defaultOutcome = options.defaultOutcome ?? 'done';
    this.costUsd = options.costUsd ?? 0.02;
    this.failOnSubmit = options.failOnSubmit ?? false;
  }

  async estimateCostUsd(params: GenerationParams): Promise<number> {
    return params.durationSeconds && params.durationSeconds > 0
      ? Number((params.durationSeconds * 0.005).toFixed(4))
      : this.costUsd;
  }

  async submit(params: GenerationParams): Promise<SubmitResult> {
    if (this.failOnSubmit || params.failOnSubmit) throw new Error(`[${this.name}] Submit failed`);
    const jobId = `${this.name}-job-${++this.jobCounter}`;
    this.jobs.set(jobId, { outcome: params.mockOutcome ?? this.defaultOutcome, params });
    return { jobId, provider: this.name, status: 'processing', costUsd: await this.estimateCostUsd(params) };
  }

  async poll(jobId: string): Promise<PollResult> {
    const outcome = this.jobs.get(jobId)?.outcome ?? this.defaultOutcome;
    if (outcome === 'done') return { status: 'done', videoUrl: `https://mock.avs.local/videos/${jobId}.mp4` };
    if (outcome === 'failed') return { status: 'failed', error: `[${this.name}] Generation failed` };
    if (outcome === 'timeout') return { status: 'timeout', error: `[${this.name}] Generation timed out` };
    return { status: 'processing', progress: 50 };
  }

  setOutcome(outcome: PollStatus): void {
    this.defaultOutcome = outcome;
  }
}
