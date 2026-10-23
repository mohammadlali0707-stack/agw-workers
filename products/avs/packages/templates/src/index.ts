import { z } from 'zod';

export const TemplateStatusSchema = z.enum(['active', 'draft', 'archived']);
export type TemplateStatus = z.infer<typeof TemplateStatusSchema>;

export const InputFieldSchema = z.object({
  name: z.string().min(1),
  type: z.enum(['string', 'number', 'boolean']).or(z.string()),
  label_fa: z.string().min(1),
  required: z.boolean().default(false),
  default: z.unknown().optional(),
});
export type InputField = z.infer<typeof InputFieldSchema>;

export const TemplateSchema = z.object({
  slug: z.string().min(1),
  title_fa: z.string().min(1),
  category: z.string().min(1),
  cover_url: z.string(),
  sample_video_url: z.string().optional(),
  inputs: z.array(InputFieldSchema),
  prompt: z.string(),
  primary_provider: z.string().min(1),
  fallback_provider: z.string().optional(),
  price_credits: z.number().nonnegative(),
  status: TemplateStatusSchema,
});
export type Template = z.infer<typeof TemplateSchema>;
export const templateSchema = TemplateSchema;

export const templateJsonSchema = {
  type: 'object',
  required: ['slug', 'title_fa', 'category', 'cover_url', 'inputs', 'prompt', 'primary_provider', 'price_credits', 'status'],
  properties: {
    slug: { type: 'string' }, title_fa: { type: 'string' }, category: { type: 'string' }, cover_url: { type: 'string' },
    sample_video_url: { type: 'string' }, inputs: { type: 'array' }, prompt: { type: 'string' },
    primary_provider: { type: 'string' }, fallback_provider: { type: 'string' }, price_credits: { type: 'number' },
    status: { enum: ['active', 'draft', 'archived'] },
  },
};

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  [index: number]: string;
  length: number;
  [Symbol.iterator](): Iterator<string>;
}

export function validate(template: Template, inputs: Record<string, unknown> = {}): ValidationResult {
  const errors: string[] = [];
  for (const field of template.inputs || []) {
    const val = inputs[field.name];
    const label = field.label_fa || field.name;
    const isMissing = val === undefined || val === null || val === '';
    if (isMissing) {
      if (field.required && field.default === undefined) {
        errors.push(`فیلد «${label}» الزامی است و مقداردهی نشده است`);
      }
    } else if (
      (field.type === 'number' && (typeof val !== 'number' || Number.isNaN(val))) ||
      (field.type === 'boolean' && typeof val !== 'boolean') ||
      (field.type === 'string' && typeof val !== 'string')
    ) {
      errors.push(`مقدار فیلد «${label}» نامعتبر است`);
    }
  }
  const res = {
    valid: errors.length === 0,
    errors,
    get length() { return errors.length; },
    [Symbol.iterator]() { return errors[Symbol.iterator](); },
  } as ValidationResult;
  errors.forEach((err, idx) => { (res as Record<number, string>)[idx] = err; });
  return res;
}

export interface GenRequest {
  prompt: string;
  target_provider: string;
  fallback_provider?: string;
  provider: string;
  metadata: Record<string, unknown>;
}

export function render(template: Template, inputs: Record<string, unknown> = {}): GenRequest {
  const merged: Record<string, unknown> = {};
  for (const f of template.inputs || []) {
    if (f.default !== undefined) merged[f.name] = f.default;
  }
  Object.assign(merged, inputs);
  const prompt = template.prompt.replace(/\{\{\s*([a-zA-Z0-9_-]+)\s*\}\}/g, (_, k) => {
    const v = merged[k];
    return v !== undefined && v !== null ? String(v) : '';
  });
  return {
    prompt,
    target_provider: template.primary_provider,
    fallback_provider: template.fallback_provider,
    provider: template.primary_provider,
    metadata: {
      template_slug: template.slug,
      category: template.category,
      price_credits: template.price_credits,
      inputs: merged,
    },
  };
}
