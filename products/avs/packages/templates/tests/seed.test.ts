import { describe, it, expect } from 'vitest';
import { seedTemplates, parseTemplate, validateInputs, validateInputsSafe, renderGenRequest } from '../src/seed/index.js';

const sampleInputs = {
  'cinematic-portrait': { subject: 'سهراب سپهری در کافه نوستالژیک', lighting: 'نورپردازی رامبراند دراماتیک' },
  'commercial-product-video': { product_name: 'عطر لوکس کریستالی', environment: 'سنگ مرمر مشکی و مه ملایم' },
  'epic-historical-landscape': { monument: 'تخت جمشید با ستون‌های باستانی', weather: 'غروب طلایی با ابرهای حماسی' }
};

describe('Persian Video Studio Seed Templates Acceptance', () => {
  it('parses each template strictly against the schema', () => {
    expect(seedTemplates).toHaveLength(3);
    for (const t of seedTemplates) {
      const parsed = parseTemplate(t);
      expect(parsed.slug && parsed.title_fa && parsed.category).toBeTruthy();
      expect(parsed.cover_url).toMatch(/^https?:\/\//);
      expect(parsed.sample_video_url).toMatch(/^https?:\/\//);
      expect(parsed.inputs.length).toBeGreaterThan(0);
      parsed.inputs.forEach(i => expect(i.label_fa).toBeTruthy());
      expect(parsed.video_prompt).toContain('{{');
      expect(parsed.primary_provider).toMatch(/^fal-ai\//);
      expect(parsed.price_credits).toBeGreaterThan(0);
      expect(parsed.active).toBe(true);
    }
  });

  it('validates cleanly against valid Persian inputs', () => {
    for (const t of seedTemplates) {
      const res = validateInputs(t, sampleInputs[t.slug as keyof typeof sampleInputs]);
      expect(res.valid).toBe(true);
      expect(res.errors).toHaveLength(0);
    }
  });

  it('produces explicit Persian errors on malformed inputs', () => {
    for (const t of seedTemplates) {
      expect(() => validateInputs(t, {})).toThrow(/الزامی است/);
      const safe = validateInputsSafe(t, { dummy: 'نامعتبر' });
      expect(safe.valid).toBe(false);
      expect(safe.errors[0]).toMatch(/الزامی است/);
    }
  });

  it('renders into deterministic GenRequest objects', () => {
    for (const t of seedTemplates) {
      const inputs = sampleInputs[t.slug as keyof typeof sampleInputs];
      const req1 = renderGenRequest(t, inputs);
      const req2 = renderGenRequest(t, inputs);
      expect(req1).toEqual(req2);
      expect(req1.provider).toBe(t.primary_provider);
      expect(req1.fallback_provider).toBe(t.fallback_provider);
      expect(req1.price_credits).toBe(t.price_credits);
      expect(req1.metadata.template_slug).toBe(t.slug);
      expect(req1.prompt).not.toContain('{{');
      for (const val of Object.values(inputs)) expect(req1.prompt).toContain(val);
    }
  });
});
