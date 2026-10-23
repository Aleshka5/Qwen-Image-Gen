import { useEffect, useMemo, useState } from 'react';
import { api, asList } from '../api';
import { CYRILLIC, acceptsImages, modelsForMode, taskStatusText, withConfig } from '../model';
import { useWorkspace } from '../workspace';
import ReferenceList from './ReferenceList';

export default function GenerationForm({ mode, projectId, shapeId, showSize, maskRef }) {
  const { config, catalog, settings, notify, watchTasks, feed, push } = useWorkspace();
  const cfg = withConfig(config);
  const enabled = useMemo(
    () => (settings || []).filter((provider) => provider.enabled && provider.id),
    [settings],
  );

  const [prompt, setPrompt] = useState('');
  const [negative, setNegative] = useState('');
  const [steps, setSteps] = useState(cfg.default_steps);
  const [cfgScale, setCfgScale] = useState(cfg.default_cfg);
  const [seed, setSeed] = useState('-1');
  const [copies, setCopies] = useState(1);
  const [files, setFiles] = useState([]);
  const [sizeMode, setSizeMode] = useState('preset');
  const [quality, setQuality] = useState('high');
  const [aspect, setAspect] = useState(cfg.aspects[0]?.key || '1:1');
  const [width, setWidth] = useState(1024);
  const [height, setHeight] = useState(1024);
  const [providerId, setProviderId] = useState('');
  const [model, setModel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [runIds, setRunIds] = useState([]);

  useEffect(() => {
    setSteps(cfg.default_steps);
    setCfgScale(cfg.default_cfg);
  }, [cfg.default_steps, cfg.default_cfg]);

  useEffect(() => {
    if (providerId && enabled.some((provider) => provider.id === providerId)) return;
    const next = enabled.find((provider) => provider.is_default) || enabled[0];
    setProviderId(next?.id || '');
  }, [enabled, providerId]);

  const provider = enabled.find((item) => item.id === providerId) || null;
  const models = modelsForMode(catalog, provider, mode);
  const currentModel = models.find((item) => item.id === model) || null;

  useEffect(() => {
    if (currentModel) return;
    setModel(models[0]?.id || '');
  }, [models, currentModel]);

  const showRefs = acceptsImages(currentModel);
  const copiesTooMany = Number(copies) > 1;
  const aspectInfo = cfg.aspects.find((item) => item.key === aspect) || cfg.aspects[0];
  const previewW = aspectInfo ? (quality === 'medium' ? Math.round(aspectInfo.width / 2) : aspectInfo.width) : 0;
  const previewH = aspectInfo ? (quality === 'medium' ? Math.round(aspectInfo.height / 2) : aspectInfo.height) : 0;
  const runs = runIds.map((id) => feed.find((task) => task.id === id)).filter(Boolean);

  async function onSubmit(event) {
    event.preventDefault();
    setError('');
    setNote('');
    const text = prompt.trim();
    if (!text) {
      setError('Enter a prompt.');
      return;
    }
    if (CYRILLIC.test(text) || CYRILLIC.test(negative)) {
      setError('Write the prompt in English.');
      return;
    }
    if (!providerId || !model) {
      setError('Choose a provider and a model.');
      return;
    }
    let maskFile = null;
    if (mode === 'mask_fill') {
      if (!maskRef?.current?.hasPaint()) {
        setError('Paint the area to fill.');
        return;
      }
      try {
        maskFile = await maskRef.current.exportFile();
      } catch (err) {
        setError(err.message);
        return;
      }
    }
    const form = new FormData();
    form.set('project_id', projectId);
    if (shapeId) form.set('shape_id', shapeId);
    form.set('mode', mode);
    form.set('provider_id', providerId);
    form.set('model', model);
    form.set('prompt', text);
    form.set('negative_prompt', negative.trim());
    form.set('steps', String(clampInt(steps, 1, cfg.max_steps)));
    form.set('true_cfg_scale', String(clampNum(cfgScale, 1, 10)));
    form.set('copies', String(clampInt(copies, 1, cfg.max_copies)));
    if (!copiesTooMany) {
      const seedNum = Number(seed);
      if (seed !== '' && Number.isInteger(seedNum) && seedNum >= 0) form.set('seed', String(seedNum));
    }
    if (showSize) {
      form.set('size_mode', sizeMode);
      if (sizeMode === 'preset') {
        form.set('quality', quality);
        form.set('aspect', aspect);
      } else {
        form.set('width', String(clampInt(width, 32, 3000)));
        form.set('height', String(clampInt(height, 32, 3000)));
      }
    }
    if (maskFile) form.set('mask', maskFile);
    if (showRefs) files.forEach((item) => form.append('images', item.file));

    setBusy(true);
    try {
      const data = await api.generations(form);
      const tasks = asList(data, 'tasks');
      if (data?.reused) setNote('This matched an existing run.');
      setRunIds(tasks.map((task) => task.id));
      watchTasks(tasks);
    } catch (err) {
      setError(err.message);
      notify(err.message);
    } finally {
      setBusy(false);
    }
  }

  const submitLabel = busy
    ? 'Sending…'
    : mode === 'mask_fill'
      ? 'Run mask fill'
      : mode === 'customize'
        ? 'Run customisation'
        : 'Generate';

  return (
    <form className="gen-form" onSubmit={onSubmit}>
      <label className="field">
        <span>{mode === 'generate' ? 'Prompt (English)' : 'Prompt (English, what to change)'}</span>
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          maxLength={2000}
          rows={4}
          required
          placeholder={mode === 'generate'
            ? 'A cinematic portrait, soft rim light, 85mm lens'
            : 'Replace the sky with a late evening storm'}
        />
      </label>
      <label className="field">
        <span>Negative prompt (English, optional)</span>
        <input
          value={negative}
          onChange={(event) => setNegative(event.target.value)}
          maxLength={2000}
          placeholder="blurry, extra fingers, watermark"
        />
      </label>

      {showSize ? (
        <fieldset className="size-box">
          <legend>Size</legend>
          <div className="choice-row">
            <label className="choice">
              <input type="radio" name="size_mode" value="preset" checked={sizeMode === 'preset'} onChange={() => setSizeMode('preset')} />
              Quality and aspect
            </label>
            <label className="choice">
              <input type="radio" name="size_mode" value="custom" checked={sizeMode === 'custom'} onChange={() => setSizeMode('custom')} />
              Custom size
            </label>
          </div>
          {sizeMode === 'preset' ? (
            <>
              <div className="split">
                <label className="field">
                  <span>Quality</span>
                  <select value={quality} onChange={(event) => setQuality(event.target.value)}>
                    <option value="high">High (~2K)</option>
                    <option value="medium">Medium (~1K)</option>
                  </select>
                </label>
                <label className="field">
                  <span>Aspect ratio</span>
                  <select value={aspect} onChange={(event) => setAspect(event.target.value)}>
                    {cfg.aspects.map((item) => (
                      <option key={item.key} value={item.key}>{item.key}</option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="hint">{previewW}×{previewH}</p>
            </>
          ) : (
            <>
              <div className="split">
                <label className="field">
                  <span>Height</span>
                  <input type="number" min="32" max="3000" step="32" value={height} onChange={(event) => setHeight(event.target.value)} />
                </label>
                <label className="field">
                  <span>Width</span>
                  <input type="number" min="32" max="3000" step="32" value={width} onChange={(event) => setWidth(event.target.value)} />
                </label>
              </div>
              <p className="hint">From 32 to 3000 px. Each side rounds down to a multiple of 32.</p>
            </>
          )}
        </fieldset>
      ) : null}

      <div className="split">
        <label className="field">
          <span>Steps</span>
          <input type="number" min="1" max={cfg.max_steps} value={steps} onChange={(event) => setSteps(event.target.value)} />
        </label>
        <label className="field">
          <span>CFG (1 is no guidance)</span>
          <input type="number" min="1" max="10" step="0.1" value={cfgScale} onChange={(event) => setCfgScale(event.target.value)} />
        </label>
        <label className="field">
          <span>Seed</span>
          <input
            type="number"
            min="-1"
            step="1"
            value={seed}
            disabled={copiesTooMany}
            onChange={(event) => setSeed(event.target.value)}
          />
          <small className="hint">
            {copiesTooMany ? 'Every copy gets a random seed.' : '−1 is a random seed.'}
          </small>
        </label>
      </div>

      {showRefs ? (
        <div className="field">
          <span>Reference photos (up to {cfg.max_images})</span>
          <ReferenceList files={files} onChange={setFiles} max={cfg.max_images} disabled={busy} />
        </div>
      ) : null}

      <div className="run-box">
        {enabled.length === 0 ? (
          <p className="error-text">
            Enable a provider in Settings.{' '}
            <button type="button" className="text-btn" onClick={() => push({ type: 'settings' })}>Open settings</button>
          </p>
        ) : (
          <div className="split">
            <label className="field">
              <span>Provider</span>
              <select value={providerId} onChange={(event) => setProviderId(event.target.value)}>
                {enabled.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label || item.kind}{item.is_default ? ' (default)' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Model</span>
              <select value={model} onChange={(event) => setModel(event.target.value)}>
                {models.map((item) => (
                  <option key={item.id} value={item.id}>{item.label || item.id}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Copies</span>
              <input
                type="number"
                min="1"
                max={cfg.max_copies}
                value={copies}
                onChange={(event) => setCopies(event.target.value)}
              />
            </label>
          </div>
        )}
        {models.length === 0 && provider ? <p className="hint">No model for this mode is enabled on the provider.</p> : null}
      </div>

      {error ? <p className="error-text" role="alert">{error}</p> : null}
      {note ? <p className="hint">{note}</p> : null}
      <button className="btn" type="submit" disabled={busy || enabled.length === 0} aria-busy={busy}>
        {submitLabel}
      </button>

      {runs.length > 0 ? (
        <div className="run-list">
          {runs.map((task) => (
            <article key={task.id} className="task-card">
              <header>
                <strong>{task.model || 'Task'}</strong>
                <span>{taskStatusText(task)}</span>
              </header>
              {task.error ? <p className="error-text">{task.error}</p> : null}
              {task.result_url ? <img src={task.result_url} alt="" /> : null}
            </article>
          ))}
        </div>
      ) : null}
    </form>
  );
}

function clampInt(value, min, max) {
  const n = Math.round(Number(value));
  if (Number.isNaN(n)) return min;
  return Math.min(max, Math.max(min, n));
}

function clampNum(value, min, max) {
  const n = Number(value);
  if (Number.isNaN(n)) return min;
  return Math.min(max, Math.max(min, Math.round(n * 10) / 10));
}
