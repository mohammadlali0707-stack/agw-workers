import type { ExecuteOptions, ExecuteResult, GenerationParams, PollResult, SubmitResult, VideoProvider } from './types.js';

export class ProviderRouter {
  private providers = new Map<string, VideoProvider>();

  register(nameOrProvider: string | VideoProvider, maybe?: VideoProvider): this {
    if (typeof nameOrProvider === 'string') {
      if (maybe) this.providers.set(nameOrProvider, maybe);
    } else {
      this.providers.set(nameOrProvider.name, nameOrProvider);
    }
    return this;
  }

  get(name: string): VideoProvider | undefined {
    return this.providers.get(name) ?? (name.startsWith('fal') ? (this.providers.get('fal') ?? this.providers.get('fal.ai')) : undefined);
  }

  has(name: string): boolean {
    return this.get(name) !== undefined;
  }

  async estimateCostUsd(name: string, params: GenerationParams): Promise<number> {
    const p = this.get(name);
    if (!p) throw new Error(`Provider "${name}" not registered`);
    return p.estimateCostUsd(params);
  }

  async submit(name: string, params: GenerationParams): Promise<SubmitResult> {
    const p = this.get(name);
    if (!p) throw new Error(`Provider "${name}" not registered`);
    return p.submit(params);
  }

  async poll(name: string, jobId: string): Promise<PollResult> {
    const p = this.get(name);
    if (!p) throw new Error(`Provider "${name}" not registered`);
    return p.poll(jobId);
  }

  private async runProvider(provider: VideoProvider, params: GenerationParams, max: number, interval: number): Promise<{ success: boolean; jobId: string; pollResult: PollResult; error?: string }> {
    const sub = await provider.submit(params);
    for (let i = 0; i < max; i++) {
      const poll = await provider.poll(sub.jobId);
      if (poll.status === 'done') return { success: true, jobId: sub.jobId, pollResult: poll };
      if (poll.status === 'failed' || poll.status === 'timeout') return { success: false, jobId: sub.jobId, pollResult: poll, error: poll.error };
      if (interval > 0 && i < max - 1) await new Promise(r => setTimeout(r, interval));
    }
    const timeoutRes: PollResult = { status: 'timeout', error: 'Exceeded max poll attempts' };
    return { success: false, jobId: sub.jobId, pollResult: timeoutRes, error: timeoutRes.error };
  }

  async executeJob(options: ExecuteOptions): Promise<ExecuteResult> {
    const pName = options.primaryProvider ?? options.template?.primary_provider;
    const fbName = options.fallbackProvider ?? options.template?.fallback_provider;
    if (!pName) throw new Error('Primary provider is required');
    const primary = this.get(pName);
    if (!primary) throw new Error(`Primary provider "${pName}" not found`);

    const max = options.maxPollAttempts ?? 10;
    const interval = options.pollIntervalMs ?? 0;
    let res: { success: boolean; jobId: string; pollResult: PollResult; error?: string } | undefined;
    let pErr: Error | undefined;

    try {
      res = await this.runProvider(primary, options.params, max, interval);
    } catch (err: any) {
      pErr = err;
    }

    if (res?.success) {
      return { success: true, provider: pName, jobId: res.jobId, pollResult: res.pollResult, fallbackUsed: false };
    }

    if (!fbName) {
      return {
        success: false, provider: pName, jobId: res?.jobId ?? '',
        pollResult: res?.pollResult ?? { status: 'failed', error: pErr?.message },
        fallbackUsed: false, error: pErr?.message ?? res?.error ?? 'Primary provider failed',
      };
    }

    const fallback = this.get(fbName);
    if (!fallback) throw new Error(`Fallback provider "${fbName}" not found`);

    try {
      const fbRes = await this.runProvider(fallback, options.params, max, interval);
      return { success: fbRes.success, provider: fbName, jobId: fbRes.jobId, pollResult: fbRes.pollResult, fallbackUsed: true, error: fbRes.error };
    } catch (err: any) {
      return { success: false, provider: fbName, jobId: '', pollResult: { status: 'failed', error: err.message }, fallbackUsed: true, error: err.message };
    }
  }
}
