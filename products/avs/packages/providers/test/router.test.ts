import { describe, expect, it } from 'vitest';
import { FalVideoProvider, MockVideoProvider, ProviderRouter } from '../src/index.js';

describe('AVS-M03 Video Providers & Router', () => {
  it('executes normal job successfully with MockVideoProvider', async () => {
    const router = new ProviderRouter().register(new MockVideoProvider({ name: 'mock-primary' }));
    const result = await router.executeJob({
      primaryProvider: 'mock-primary',
      params: { prompt: 'scenic video' },
    });
    expect(result.success).toBe(true);
    expect(result.provider).toBe('mock-primary');
    expect(result.fallbackUsed).toBe(false);
    expect(result.pollResult.status).toBe('done');
    expect(result.pollResult.videoUrl).toContain('mock-primary-job-');
  });

  it('handles deterministic mock outcomes (done, failed, timeout, processing)', async () => {
    const mock = new MockVideoProvider();
    for (const st of ['done', 'failed', 'timeout', 'processing'] as const) {
      mock.setOutcome(st);
      const sub = await mock.submit({ prompt: 'test' });
      const poll = await mock.poll(sub.jobId);
      expect(poll.status).toBe(st);
    }
    expect(await mock.estimateCostUsd({ prompt: 't', durationSeconds: 10 })).toBe(0.05);
  });

  it('triggers one-time automatic fallback on primary failure and timeout', async () => {
    const router = new ProviderRouter()
      .register('fail-p', new MockVideoProvider({ defaultOutcome: 'failed' }))
      .register('timeout-p', new MockVideoProvider({ defaultOutcome: 'timeout' }))
      .register('err-p', new MockVideoProvider({ failOnSubmit: true }))
      .register('backup', new MockVideoProvider({ defaultOutcome: 'done' }));

    for (const primary of ['fail-p', 'timeout-p', 'err-p']) {
      const res = await router.executeJob({
        template: { primary_provider: primary, fallback_provider: 'backup' },
        params: { prompt: 'video' },
      });
      expect(res.success).toBe(true);
      expect(res.fallbackUsed).toBe(true);
      expect(res.provider).toBe('backup');
      expect(res.pollResult.status).toBe('done');
    }
  });

  it('handles fallback failure and no-fallback cases gracefully', async () => {
    const router = new ProviderRouter()
      .register('p1', new MockVideoProvider({ defaultOutcome: 'failed' }))
      .register('p2', new MockVideoProvider({ defaultOutcome: 'failed' }));

    const r1 = await router.executeJob({ primaryProvider: 'p1', params: { prompt: 'test' } });
    expect(r1.success).toBe(false);
    expect(r1.fallbackUsed).toBe(false);

    const r2 = await router.executeJob({
      template: { primary_provider: 'p1', fallback_provider: 'p2' },
      params: { prompt: 'test' },
    });
    expect(r2.success).toBe(false);
    expect(r2.fallbackUsed).toBe(true);
  });

  it('fal adapter strictly reads credentials from process.env with zero network calls', async () => {
    delete process.env.FAL_KEY;
    delete process.env.FAL_API_KEY;
    const fal = new FalVideoProvider();
    await expect(fal.submit({ prompt: 'test' })).rejects.toThrow(/FAL_KEY or FAL_API_KEY is required/);

    process.env.FAL_KEY = 'test-key';
    const mockFetch: typeof fetch = async () =>
      new Response(JSON.stringify({ request_id: 'fal-123' }), { status: 200 });
    const client = new FalVideoProvider({ fetchFn: mockFetch });
    const sub = await client.submit({ prompt: 'test' });
    expect(sub.jobId).toBe('fal-123');
    delete process.env.FAL_KEY;
  });
});
