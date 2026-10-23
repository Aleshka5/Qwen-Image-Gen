import { useEffect, useState } from 'react';
import { api, asList } from '../api';
import { useWorkspace } from '../workspace';

export default function ObjectPanel() {
  const { objectTarget, confirm, notify, bumpVariants, feed } = useWorkspace();
  const [variants, setVariants] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');

  const doneKey = feed
    .filter((task) => task.status === 'done' && matches(task, objectTarget))
    .map((task) => task.id)
    .join(',');

  useEffect(() => {
    if (!objectTarget) {
      setVariants([]);
      setError('');
      return undefined;
    }
    let stop = false;
    setLoading(true);
    setError('');
    const request = objectTarget.kind === 'project'
      ? api.projectVariants(objectTarget.id)
      : api.shapeVariants(objectTarget.id);
    request
      .then((data) => {
        if (!stop) setVariants(asList(data, 'variants'));
      })
      .catch((err) => {
        if (!stop) setError(err.message);
      })
      .finally(() => {
        if (!stop) setLoading(false);
      });
    return () => {
      stop = true;
    };
  }, [objectTarget, doneKey]);

  if (!objectTarget) {
    return (
      <p className="empty-note">
        Choose another variant to compare results for one object.
      </p>
    );
  }

  async function choose(variant) {
    if (variant.selected || busyId) return;
    const ok = await confirm({
      title: 'Use this variant?',
      message: 'It will replace the variant shown in this object.',
      confirmLabel: 'Use variant',
    });
    if (!ok) return;
    setBusyId(variant.id);
    try {
      if (objectTarget.kind === 'project') {
        await api.selectProjectVariant(objectTarget.id, variant.id);
      } else {
        await api.selectShapeVariant(objectTarget.id, variant.id);
      }
      const data = objectTarget.kind === 'project'
        ? await api.projectVariants(objectTarget.id)
        : await api.shapeVariants(objectTarget.id);
      setVariants(asList(data, 'variants'));
      bumpVariants();
    } catch (err) {
      notify(err.message);
    } finally {
      setBusyId('');
    }
  }

  return (
    <div className="object-panel">
      <p className="meta">{objectTarget.kind === 'project' ? 'Scratch project' : 'This object'}</p>
      {loading ? <p className="empty-note">Loading variants…</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      {!loading && !error && variants.length === 0 ? (
        <p className="empty-note">No variants yet.</p>
      ) : null}
      <div className="variant-grid">
        {variants.map((variant) => (
          <button
            key={variant.id}
            type="button"
            className={variant.selected ? 'variant is-selected' : 'variant'}
            onClick={() => choose(variant)}
            disabled={Boolean(busyId)}
            aria-pressed={variant.selected ? true : undefined}
          >
            {variant.url ? <img src={variant.url} alt="" /> : <span className="variant-missing">No image</span>}
            <span className="variant-meta">
              {[variant.provider, variant.model].filter(Boolean).join(' · ') || 'Variant'}
              {variant.seed != null ? ` · seed ${variant.seed}` : ''}
            </span>
            {variant.prompt ? <span className="variant-prompt">{variant.prompt}</span> : null}
            <span className="variant-state">
              {busyId === variant.id ? 'Saving…' : variant.selected ? 'Selected' : 'Choose'}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function matches(task, target) {
  if (!target) return false;
  if (target.kind === 'project') return task.project_id === target.id && !task.shape_id;
  return task.shape_id === target.id;
}
