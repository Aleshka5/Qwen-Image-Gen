export const VIEW_MS = 350;

export const DEFAULT_CONFIG = {
  max_shapes: 8,
  max_copies: 4,
  max_images: 10,
  max_steps: 60,
  default_steps: 40,
  default_cfg: 1,
  model: '',
  aspects: [
    { key: '1:1', width: 2048, height: 2048 },
    { key: '4:3', width: 2400, height: 1792 },
    { key: '3:4', width: 1792, height: 2400 },
    { key: '3:2', width: 2528, height: 1696 },
    { key: '2:3', width: 1696, height: 2528 },
    { key: '16:9', width: 2752, height: 1536 },
    { key: '9:16', width: 1536, height: 2752 },
  ],
  level1_colors: ['#b85c38', '#2f5d50', '#3d4c7a', '#8a6a2f', '#6b3a4a', '#3f5f73'],
  photo_color: '#3d4c7a',
  text_color: '#2f5d50',
};

export const FALLBACK_CATALOG = [
  { kind: 'qwen', label: 'Local Qwen', fields: [], models: [] },
  {
    kind: 'wan',
    label: 'Wan',
    fields: ['api_key', 'region', 'workspace_id'],
    models: [],
  },
  {
    kind: 'higgsfield',
    label: 'Higgsfield',
    fields: ['key_id', 'key_secret'],
    models: [],
  },
];

export const REGIONS = [
  ['singapore', 'Singapore'],
  ['virginia', 'Virginia'],
  ['beijing', 'Beijing'],
  ['frankfurt', 'Frankfurt'],
];

export const CYRILLIC = /[\u0400-\u04FF]/;

export function motionMs() {
  if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    return 0;
  }
  return VIEW_MS;
}

export function withConfig(config) {
  const next = { ...DEFAULT_CONFIG, ...(config || {}) };
  if (!Array.isArray(next.aspects) || next.aspects.length === 0) next.aspects = DEFAULT_CONFIG.aspects;
  if (!Array.isArray(next.level1_colors) || next.level1_colors.length === 0) {
    next.level1_colors = DEFAULT_CONFIG.level1_colors;
  }
  if (next.default_cfg == null && config?.default_true_cfg_scale != null) {
    next.default_cfg = config.default_true_cfg_scale;
  }
  return next;
}

export function isLocalKind(entry) {
  const kind = String(entry?.kind || '').toLowerCase();
  const label = String(entry?.label || '').toLowerCase();
  return kind.includes('qwen') || label.includes('qwen') || kind === 'local' || label.startsWith('local ');
}

export function taskStatusText(task) {
  const kind = String(task?.provider_kind || '').toLowerCase();
  const namedExternal = kind && !kind.includes('qwen') && !kind.includes('local');
  const progress = task?.progress;
  if (!namedExternal && progress && Number(progress.total) > 0) {
    const name = progress.name ? ` ${progress.name}` : '';
    return `${progress.stage}/${progress.total}${name}`;
  }
  return task?.status || 'queued';
}

export function withAlpha(color, alpha) {
  const hex = String(color || '').trim();
  const match = /^#([\da-f]{6})$/i.exec(hex);
  if (!match) return hex || `rgba(61, 76, 122, ${alpha})`;
  const n = match[1];
  const r = parseInt(n.slice(0, 2), 16);
  const g = parseInt(n.slice(2, 4), 16);
  const b = parseInt(n.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function shapeColor(shape, config, index, nested) {
  if (shape?.color) return shape.color;
  const cfg = withConfig(config);
  if (!nested) return cfg.level1_colors[index % cfg.level1_colors.length];
  if (shape?.type === 'text') return cfg.text_color;
  return cfg.photo_color;
}

export function modelId(model) {
  if (!model) return '';
  return typeof model === 'string' ? model : model.id || '';
}

export function modelLabel(model) {
  if (!model) return '';
  if (typeof model === 'string') return model;
  return model.label || model.id || '';
}

export function enabledModelIds(provider) {
  return (provider?.models || []).map(modelId).filter(Boolean);
}

export function modelsForMode(catalog, provider, mode) {
  if (!provider) return [];
  const entry = (catalog || []).find((item) => item.kind === provider.kind);
  let source = entry?.models || [];
  if (!source.length) {
    source = (provider.models || []).filter((item) => item && typeof item === 'object');
  }
  const enabled = enabledModelIds(provider);
  let list = source.filter((item) => {
    const modes = item.modes || [];
    return modes.length === 0 || modes.includes(mode);
  });
  if (enabled.length) list = list.filter((item) => enabled.includes(item.id));
  return list;
}

export function acceptsImages(model) {
  if (!model || model.accepts_images == null) return true;
  return Boolean(model.accepts_images);
}

export function fieldKey(field) {
  if (!field) return '';
  if (typeof field === 'string') return field;
  return field.name || field.key || field.id || '';
}

export function fieldLabel(field) {
  if (!field) return '';
  if (typeof field === 'string') return field.replaceAll('_', ' ');
  return field.label || fieldKey(field).replaceAll('_', ' ');
}

export function cx(...parts) {
  return parts.filter(Boolean).join(' ');
}
