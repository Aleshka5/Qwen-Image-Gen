import { useMemo, useState } from 'react';
import { api } from '../api';
import {
  FALLBACK_CATALOG,
  REGIONS,
  enabledModelIds,
  fieldKey,
  fieldLabel,
  isLocalKind,
  modelId,
  modelLabel,
} from '../model';
import { useWorkspace } from '../workspace';

export default function SettingsView() {
  const { catalog, settings } = useWorkspace();
  const entries = catalog.length ? catalog : FALLBACK_CATALOG;
  const known = new Set(entries.map((entry) => entry.kind));
  const extras = settings.filter((provider) => !known.has(provider.kind) && !isLocalKind(provider));
  const localEntry = entries.find((entry) => isLocalKind(entry)) || FALLBACK_CATALOG[0];
  const rest = entries.filter((entry) => entry !== localEntry);

  return (
    <div className="view-frame">
      <div className="view-body">
        <div className="sheet">
          <p className="lede">
            Choose which providers can run a generation. Secrets are write-only; the service returns a hint, not the key.
          </p>
          <LocalBlock entry={localEntry} providers={settings.filter((provider) => provider.kind === localEntry.kind || isLocalKind(provider))} />
          {rest.map((entry) => (
            <section key={entry.kind} className="provider-section">
              <h2>{entry.label || entry.kind}</h2>
              {settings.filter((provider) => provider.kind === entry.kind && !isLocalKind(provider)).map((provider) => (
                <ProviderCard key={provider.id} entry={entry} provider={provider} />
              ))}
              <AddProvider entry={entry} />
            </section>
          ))}
          {extras.map((provider) => (
            <ProviderCard key={provider.id} entry={{ kind: provider.kind, label: provider.label, fields: [], models: [] }} provider={provider} />
          ))}
        </div>
      </div>
    </div>
  );
}

function LocalBlock({ entry, providers }) {
  return (
    <section className="provider-section">
      <h2>{entry.label || 'Local Qwen'}</h2>
      <p className="hint">Local Qwen is always present and does not use an API key.</p>
      {providers.length === 0 ? (
        <article className="provider-card">
          <p>It will show up in the provider list when settings include it.</p>
          <ModelList models={entry.models || []} checked={new Set((entry.models || []).map((model) => model.id))} readOnly />
        </article>
      ) : providers.map((provider) => (
        <ProviderCard key={provider.id} entry={entry} provider={provider} local />
      ))}
    </section>
  );
}

function ProviderCard({ entry, provider, local = false }) {
  const { refreshSettings, confirm, notify } = useWorkspace();
  const fields = fieldsFor(entry);
  const [values, setValues] = useState(() => ({
    region: provider.region || 'singapore',
    workspace_id: provider.workspace_id || '',
  }));
  const [secrets, setSecrets] = useState({});
  const [checked, setChecked] = useState(() => new Set(enabledModelIds(provider)));
  const [busy, setBusy] = useState('');
  const models = useMemo(() => modelChoices(entry, provider), [entry, provider]);

  function setField(name, value) {
    if (isSecret(name)) setSecrets((current) => ({ ...current, [name]: value }));
    else setValues((current) => ({ ...current, [name]: value }));
  }

  async function save(partial, label) {
    setBusy(label || 'save');
    try {
      await api.updateProvider(provider.id, partial);
      setSecrets({});
      await refreshSettings();
    } catch (err) {
      notify(err.message);
    } finally {
      setBusy('');
    }
  }

  async function saveForm(event) {
    event.preventDefault();
    const body = { models: [...checked] };
    for (const field of fields) {
      const key = fieldKey(field);
      if (!key || isSecret(key)) continue;
      if (values[key] != null) body[key] = values[key];
    }
    for (const [key, value] of Object.entries(secrets)) {
      if (value) body[key] = value;
    }
    await save(body, 'save');
  }

  async function remove() {
    const ok = await confirm({
      title: `Remove ${provider.label || entry.label}?`,
      message: 'This provider will disappear from the generation page.',
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    setBusy('delete');
    try {
      await api.deleteProvider(provider.id);
      await refreshSettings();
    } catch (err) {
      notify(err.message);
    } finally {
      setBusy('');
    }
  }

  return (
    <article className="provider-card">
      <header className="card-head">
        <div>
          <h3>{provider.label || entry.label || provider.kind}</h3>
          {provider.key_hint ? <p className="hint">Saved key: {provider.key_hint}</p> : null}
          {local ? <p className="hint">No API key.</p> : null}
        </div>
        <div className="card-actions">
          <button
            type="button"
            className={provider.enabled ? 'btn' : 'btn btn-ghost'}
            aria-pressed={Boolean(provider.enabled)}
            disabled={Boolean(busy)}
            onClick={() => save({ enabled: !provider.enabled }, 'enable')}
          >
            {busy === 'enable' ? 'Saving…' : provider.enabled ? 'Enabled' : 'Disabled'}
          </button>
          {provider.is_default ? <span className="pill">Default</span> : (
            <button
              type="button"
              className="btn btn-ghost"
              disabled={Boolean(busy)}
              onClick={async () => {
                setBusy('default');
                try {
                  await api.defaultProvider(provider.id);
                  await refreshSettings();
                } catch (err) {
                  notify(err.message);
                } finally {
                  setBusy('');
                }
              }}
            >
              {busy === 'default' ? 'Saving…' : 'Set default'}
            </button>
          )}
        </div>
      </header>
      <form onSubmit={saveForm} className="stack-form">
        {fields.map((field) => (
          <SecretOrField
            key={fieldKey(field)}
            field={field}
            value={isSecret(fieldKey(field)) ? (secrets[fieldKey(field)] || '') : (values[fieldKey(field)] ?? '')}
            onChange={(value) => setField(fieldKey(field), value)}
            hint={isSecret(fieldKey(field)) ? 'Leave blank to keep the saved secret.' : ''}
          />
        ))}
        <ModelList
          models={models}
          checked={checked}
          onToggle={(id) => {
            setChecked((current) => {
              const next = new Set(current);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            });
          }}
        />
        <div className="card-actions">
          <button type="submit" className="btn" disabled={Boolean(busy)}>
            {busy === 'save' ? 'Saving…' : 'Save'}
          </button>
          {local ? null : (
            <button type="button" className="btn btn-danger" disabled={Boolean(busy)} onClick={remove}>
              {busy === 'delete' ? 'Removing…' : 'Remove'}
            </button>
          )}
        </div>
      </form>
    </article>
  );
}

function AddProvider({ entry }) {
  const { refreshSettings, notify } = useWorkspace();
  const fields = fieldsFor(entry);
  const [values, setValues] = useState({ region: 'singapore', workspace_id: '' });
  const [secrets, setSecrets] = useState({});
  const [checked, setChecked] = useState(() => new Set((entry.models || []).map((model) => model.id).filter(Boolean)));
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button type="button" className="btn btn-ghost" onClick={() => setOpen(true)}>
        Add {entry.label || entry.kind}
      </button>
    );
  }

  async function submit(event) {
    event.preventDefault();
    for (const field of fields) {
      const key = fieldKey(field);
      if (isSecret(key) && !secrets[key]) {
        notify(`Enter ${fieldLabel(field)}.`);
        return;
      }
    }
    const body = {
      kind: entry.kind,
      label: entry.label || entry.kind,
      enabled: true,
      is_default: false,
      models: [...checked],
    };
    for (const field of fields) {
      const key = fieldKey(field);
      const value = isSecret(key) ? secrets[key] : values[key];
      if (value) body[key] = value;
    }
    setBusy(true);
    try {
      await api.createProvider(body);
      setOpen(false);
      setSecrets({});
      await refreshSettings();
    } catch (err) {
      notify(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="provider-card" onSubmit={submit}>
      <h3>Add {entry.label || entry.kind}</h3>
      {fields.map((field) => (
        <SecretOrField
          key={fieldKey(field)}
          field={field}
          value={isSecret(fieldKey(field)) ? (secrets[fieldKey(field)] || '') : (values[fieldKey(field)] || '')}
          onChange={(value) => {
            const key = fieldKey(field);
            if (isSecret(key)) setSecrets((current) => ({ ...current, [key]: value }));
            else setValues((current) => ({ ...current, [key]: value }));
          }}
        />
      ))}
      <ModelList
        models={entry.models || []}
        checked={checked}
        onToggle={(id) => {
          setChecked((current) => {
            const next = new Set(current);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
          });
        }}
      />
      <div className="card-actions">
        <button type="submit" className="btn" disabled={busy}>{busy ? 'Adding…' : 'Add provider'}</button>
        <button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}

function SecretOrField({ field, value, onChange, hint }) {
  const key = fieldKey(field);
  const options = optionsFor(field);
  return (
    <label className="field">
      <span>{fieldLabel(field)}</span>
      {options ? (
        <select value={value || options[0][0]} onChange={(event) => onChange(event.target.value)}>
          {options.map(([optValue, label]) => (
            <option key={optValue} value={optValue}>{label}</option>
          ))}
        </select>
      ) : (
        <input
          value={value}
          autoComplete="off"
          spellCheck="false"
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {hint ? <small className="hint">{hint}</small> : null}
    </label>
  );
}

function ModelList({ models, checked, onToggle, readOnly = false }) {
  if (!models.length) return <p className="hint">No models are listed for this provider yet.</p>;
  return (
    <fieldset className="model-list">
      <legend>Models</legend>
      {models.map((model) => {
        const id = modelId(model);
        return (
          <label key={id} className="model-choice">
            <input
              type="checkbox"
              checked={checked.has(id)}
              disabled={readOnly}
              onChange={() => onToggle?.(id)}
            />
            <span>{modelLabel(model)}</span>
          </label>
        );
      })}
    </fieldset>
  );
}

function fieldsFor(entry) {
  const raw = entry?.fields || [];
  if (raw.length) return raw;
  if (entry?.kind === 'wan') return ['api_key', 'region', 'workspace_id'];
  if (entry?.kind === 'higgsfield') return ['key_id', 'key_secret'];
  return [];
}

function optionsFor(field) {
  if (field && typeof field === 'object' && Array.isArray(field.options) && field.options.length) {
    return field.options.map((opt) => (
      typeof opt === 'string' ? [opt, opt] : [opt.value || opt.id, opt.label || opt.value || opt.id]
    ));
  }
  if (fieldKey(field) === 'region') return REGIONS;
  return null;
}

function isSecret(name) {
  return /key|secret|token|password/i.test(name || '');
}

function modelChoices(entry, provider) {
  if (entry?.models?.length) return entry.models;
  return (provider.models || []).map((model) => (
    typeof model === 'string' ? { id: model, label: model } : model
  )).filter((model) => modelId(model));
}
