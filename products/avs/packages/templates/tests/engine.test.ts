import { describe, it, expect } from 'vitest';
import { TemplateSchema, validate, render, Template } from '../src/index';

const sampleTemplate: Template = {
  slug: 'promo-video', title_fa: 'ویدیوی تبلیغاتی', category: 'marketing',
  cover_url: 'https://example.com/cover.jpg', sample_video_url: 'https://example.com/sample.mp4',
  inputs: [
    { name: 'product', type: 'string', label_fa: 'محصول', required: true },
    { name: 'duration', type: 'number', label_fa: 'مدت زمان', required: true },
    { name: 'hd', type: 'boolean', label_fa: 'کیفیت بالا', required: false, default: true },
  ],
  prompt: 'Create a video for {{product}} lasting {{duration}} seconds. HD: {{hd}}.',
  primary_provider: 'runway', fallback_provider: 'pika', price_credits: 10, status: 'active',
};

describe('Template Engine', () => {
  it('validates schema correctly', () => {
    expect(TemplateSchema.safeParse(sampleTemplate).success).toBe(true);
    expect(TemplateSchema.safeParse({ ...sampleTemplate, status: 'invalid' }).success).toBe(false);
    expect(TemplateSchema.safeParse({ ...sampleTemplate, slug: '' }).success).toBe(false);
  });

  it('validates inputs and returns Persian error messages', () => {
    const res = validate(sampleTemplate, {});
    expect(res.valid).toBe(false);
    expect(res.errors).toContain('فیلد «محصول» الزامی است و مقداردهی نشده است');
    expect(res.errors).toContain('فیلد «مدت زمان» الزامی است و مقداردهی نشده است');

    const malformed = validate(sampleTemplate, { product: 'Phone', duration: 'not-a-number' });
    expect(malformed.valid).toBe(false);
    expect(malformed.errors).toContain('مقدار فیلد «مدت زمان» نامعتبر است');

    const valid = validate(sampleTemplate, { product: 'Phone', duration: 15 });
    expect(valid.valid).toBe(true);
    expect(valid.errors).toHaveLength(0);
  });

  it('renders prompt deterministically with GenRequest', () => {
    const req1 = render(sampleTemplate, { product: 'Coffee', duration: 30 });
    const req2 = render(sampleTemplate, { product: 'Coffee', duration: 30 });
    expect(req1).toEqual(req2);
    expect(req1.prompt).toBe('Create a video for Coffee lasting 30 seconds. HD: true.');
    expect(req1.target_provider).toBe('runway');
    expect(req1.fallback_provider).toBe('pika');
    expect(req1.provider).toBe('runway');
    expect(req1.metadata.template_slug).toBe('promo-video');
  });
});
