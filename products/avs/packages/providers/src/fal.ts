import type { GenerationParams, PollResult, SubmitResult, VideoProvider } from './types.js';

export interface FalProviderOptions {
  name?: string;
  apiKey?: string;
  baseUrl?: string;
  fetchFn?: typeof fetch;
}

export class FalVideoProvider implements VideoProvider {
  readonly name: string;
  private apiKey?: string;
  private baseUrl: string;
  private fetchFn: typeof fetch;

  constructor(options: FalProviderOptions = {}) {
    this.name = options.name ?? 'fal';
    this.apiKey = options.apiKey;
    this.baseUrl = options.baseUrl ?? 'https://queue.fal.run';
    this.fetchFn = options.fetchFn ?? globalThis.fetch;
  }

  private getApiKey(): string {
    const key = this.apiKey ?? process.env.FAL_KEY ?? process.env.FAL_API_KEY;
    if (!key) throw new Error('FAL_KEY or FAL_API_KEY is required in environment');
    return key;
  }

  async estimateCostUsd(params: GenerationParams): Promise<number> {
    return Number(((params.durationSeconds ?? 5) * 0.01).toFixed(4));
  }

  async submit(params: GenerationParams): Promise<SubmitResult> {
    const apiKey = this.getApiKey();
    const model = params.model ?? 'fal-ai/hunyuan-video';
    const res = await this.fetchFn(`${this.baseUrl}/${model}`, {
      method: 'POST',
      headers: { Authorization: `Key ${apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: params.prompt, ...params.metadata }),
    });
    if (!res.ok) throw new Error(`Fal submission failed: ${res.status} ${res.statusText}`);
    const data = (await res.json()) as { request_id?: string; job_id?: string };
    const jobId = data.request_id ?? data.job_id ?? 'unknown';
    return { jobId, provider: this.name, status: 'processing', costUsd: await this.estimateCostUsd(params) };
  }

  async poll(jobId: string): Promise<PollResult> {
    const apiKey = this.getApiKey();
    const res = await this.fetchFn(`${this.baseUrl}/requests/${jobId}/status`, {
      headers: { Authorization: `Key ${apiKey}` },
    });
    if (!res.ok) {
      const isTimeout = res.status === 408 || res.status === 504;
      return { status: isTimeout ? 'timeout' : 'failed', error: `Fal poll error: ${res.status}` };
    }
    const data = (await res.json()) as { status?: string; video?: { url?: string }; output?: { video_url?: string }; error?: string };
    const status = (data.status ?? '').toUpperCase();
    if (status === 'COMPLETED' || status === 'DONE') return { status: 'done', videoUrl: data.video?.url ?? data.output?.video_url };
    if (status === 'FAILED' || status === 'ERROR') return { status: 'failed', error: data.error ?? 'Fal generation failed' };
    if (status === 'TIMEOUT') return { status: 'timeout', error: 'Fal generation timed out' };
    return { status: 'processing' };
  }
}
