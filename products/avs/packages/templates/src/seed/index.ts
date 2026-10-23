export interface TemplateInput { key: string; label_fa: string; required?: boolean; }
export interface VideoTemplate {
  slug: string; title_fa: string; category: string; cover_url: string; sample_video_url: string;
  inputs: TemplateInput[]; video_prompt: string; primary_provider: string; fallback_provider: string;
  providers: { primary: string; fallback: string }; price_credits: number; active: boolean; status: string;
}
export interface GenRequest {
  provider: string; fallback_provider: string; prompt: string; price_credits: number;
  metadata: { template_slug: string; inputs: Record<string, string> };
}
export const TemplateSchema = {
  parse(t: any): VideoTemplate {
    const keys = ['slug','title_fa','category','cover_url','sample_video_url','inputs','video_prompt','primary_provider','fallback_provider','price_credits','active'];
    for (const k of keys) if (t?.[k] === undefined || t?.[k] === null) throw new Error(`الگوی قالب فاقد فیلد الزامی ${k} است`);
    if (!Array.isArray(t.inputs) || t.inputs.length === 0) throw new Error('ورودی‌های قالب نامعتبر است');
    for (const i of t.inputs) if (!i.key || !i.label_fa) throw new Error('ورودی فاقد label_fa است');
    return t as VideoTemplate;
  }
};
export const parseTemplate = TemplateSchema.parse;
export function validateInputs(t: VideoTemplate, inputs: Record<string, any>) {
  const errors: string[] = [];
  for (const def of t.inputs) {
    const val = inputs?.[def.key];
    if (val === undefined || val === null || (typeof val === 'string' && !val.trim())) {
      errors.push(`فیلد "${def.label_fa}" الزامی است و مقداردهی نشده است`);
    }
  }
  if (errors.length) { const e = new Error(errors.join(' | ')); (e as any).errors = errors; throw e; }
  return { valid: true, errors: [] };
}
export function validateInputsSafe(t: VideoTemplate, inputs: Record<string, any>) {
  try { return validateInputs(t, inputs); } catch (e: any) { return { valid: false, errors: e.errors || [e.message] }; }
}
export function renderGenRequest(t: VideoTemplate, inputs: Record<string, string>): GenRequest {
  validateInputs(t, inputs);
  return {
    provider: t.primary_provider, fallback_provider: t.fallback_provider,
    prompt: t.video_prompt.replace(/\{\{(\w+)\}\}/g, (_, k) => inputs[k] ?? ''),
    price_credits: t.price_credits, metadata: { template_slug: t.slug, inputs }
  };
}
export const cinematicPortraitTemplate: VideoTemplate = {
  slug: 'cinematic-portrait', title_fa: 'پرتره سینمایی با نورپردازی دراماتیک', category: 'portrait',
  cover_url: 'https://mock.avs.local/covers/cinematic-portrait.webp', sample_video_url: 'https://mock.avs.local/samples/cinematic-portrait.mp4',
  inputs: [{ key: 'subject', label_fa: 'سوژه و چهره' }, { key: 'lighting', label_fa: 'سبک نورپردازی' }],
  video_prompt: 'Cinematic portrait of {{subject}}, dramatic {{lighting}}, highly detailed 8k film still, 35mm lens, fal.ai video studio',
  primary_provider: 'fal-ai/hunyuan-video', fallback_provider: 'fal-ai/kling-video',
  providers: { primary: 'fal-ai/hunyuan-video', fallback: 'fal-ai/kling-video' },
  price_credits: 15, active: true, status: 'active'
};
export const commercialProductTemplate: VideoTemplate = {
  slug: 'commercial-product-video', title_fa: 'تیزر تبلیغاتی محصول مدرن', category: 'commercial',
  cover_url: 'https://mock.avs.local/covers/commercial-product.webp', sample_video_url: 'https://mock.avs.local/samples/commercial-product.mp4',
  inputs: [{ key: 'product_name', label_fa: 'نام و نوع محصول' }, { key: 'environment', label_fa: 'محیط و صحنه نمایش' }],
  video_prompt: 'Commercial product video of {{product_name}}, 360 rotation in {{environment}}, clean studio lighting, 4k commercial',
  primary_provider: 'fal-ai/kling-video', fallback_provider: 'fal-ai/minimax/video-01',
  providers: { primary: 'fal-ai/kling-video', fallback: 'fal-ai/minimax/video-01' },
  price_credits: 20, active: true, status: 'active'
};
export const epicHistoricalLandscapeTemplate: VideoTemplate = {
  slug: 'epic-historical-landscape', title_fa: 'مناظر حماسی و تاریخی', category: 'landscape',
  cover_url: 'https://mock.avs.local/covers/epic-landscape.webp', sample_video_url: 'https://mock.avs.local/samples/epic-landscape.mp4',
  inputs: [{ key: 'monument', label_fa: 'بنا یا اثر تاریخی' }, { key: 'weather', label_fa: 'شرایط جوی و آب‌وهوا' }],
  video_prompt: 'Epic historical aerial view of {{monument}}, {{weather}}, grand atmosphere, hyper-detailed ancient landscape 8k video',
  primary_provider: 'fal-ai/hunyuan-video', fallback_provider: 'fal-ai/kling-video',
  providers: { primary: 'fal-ai/hunyuan-video', fallback: 'fal-ai/kling-video' },
  price_credits: 25, active: true, status: 'active'
};
export const seedTemplates = [cinematicPortraitTemplate, commercialProductTemplate, epicHistoricalLandscapeTemplate];
export default seedTemplates;
