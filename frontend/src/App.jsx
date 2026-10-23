import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, asList, sleep } from './api';
import { motionMs } from './model';
import { WorkspaceContext } from './workspace';
import WorkChrome from './components/Chrome';
import Dialog from './components/Dialog';
import GenerateWorkspace from './components/GenerateWorkspace';
import Home from './components/Home';
import PhotoWorkspace from './components/PhotoWorkspace';
import SettingsView from './components/SettingsView';
import Sidebars from './components/Sidebars';
import StageEditor from './components/StageEditor';
import TextWorkspace from './components/TextWorkspace';
import ViewStack from './components/ViewStack';

const NARROW = '(max-width: 799px)';

export default function App() {
  const [config, setConfig] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const [settings, setSettings] = useState([]);
  const [projects, setProjects] = useState([]);
  const [feed, setFeed] = useState([]);
  const [stack, setStack] = useState([{ type: 'home', key: 'home' }]);
  const [motion, setMotion] = useState(null);
  const [leftOpen, setLeftOpen] = useState(() => !window.matchMedia(NARROW).matches);
  const [rightOpen, setRightOpen] = useState(() => !window.matchMedia(NARROW).matches);
  const [rightTab, setRightTab] = useState('feed');
  const [objectTarget, setObjectTarget] = useState(null);
  const [notice, setNotice] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [bootError, setBootError] = useState('');
  const [booting, setBooting] = useState(true);
  const [variantEpoch, setVariantEpoch] = useState(0);
  const [rendering, setRendering] = useState(false);
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW).matches);

  const stackRef = useRef(stack);
  const motionRef = useRef(null);
  const watchRef = useRef([]);
  const timer = useRef(null);
  const noticeTimer = useRef(null);

  useEffect(() => {
    const mq = window.matchMedia(NARROW);
    const onChange = () => setNarrow(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  useEffect(() => {
    if (narrow) {
      setLeftOpen(false);
      setRightOpen(false);
    }
  }, [narrow]);

  const notify = useCallback((message, tone = 'error') => {
    if (!message) return;
    setNotice({ message, tone });
    clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), 4200);
  }, []);

  const bumpVariants = useCallback(() => setVariantEpoch((n) => n + 1), []);

  const upsertTask = useCallback((task) => {
    if (!task?.id) return;
    setFeed((list) => {
      const index = list.findIndex((item) => item.id === task.id);
      if (index === -1) return [task, ...list];
      const next = list.slice();
      next[index] = { ...next[index], ...task };
      return next;
    });
  }, []);

  const watchTasks = useCallback((tasks) => {
    const ids = (tasks || []).map((task) => task.id).filter(Boolean);
    watchRef.current = [...new Set([...watchRef.current, ...ids])];
    (tasks || []).forEach(upsertTask);
  }, [upsertTask]);

  const refreshProjects = useCallback(async () => {
    const data = await api.projects();
    setProjects(asList(data, 'projects'));
  }, []);

  const refreshSettings = useCallback(async () => {
    const data = await api.settings();
    setSettings(asList(data, 'providers'));
  }, []);

  const loadAll = useCallback(async () => {
    setBooting(true);
    const settled = await Promise.allSettled([
      api.config(),
      api.catalog(),
      api.settings(),
      api.projects(),
      api.feed(),
    ]);
    const [cfg, cat, setng, proj, feedData] = settled;
    if (cfg.status === 'fulfilled') {
      setConfig(cfg.value);
      setBootError('');
    } else {
      setBootError(cfg.reason.message);
    }
    if (cat.status === 'fulfilled') setCatalog(asList(cat.value, 'providers'));
    if (setng.status === 'fulfilled') setSettings(asList(setng.value, 'providers'));
    if (proj.status === 'fulfilled') setProjects(asList(proj.value, 'projects'));
    if (feedData.status === 'fulfilled') setFeed(asList(feedData.value, 'tasks'));
    setBooting(false);
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    let stop = false;
    async function loop() {
      while (!stop) {
        const ids = watchRef.current.slice();
        if (ids.length) {
          await Promise.all(ids.map(async (id) => {
            try {
              const task = await api.task(id);
              upsertTask(task);
              if (task.status === 'done' || task.status === 'failed') {
                watchRef.current = watchRef.current.filter((item) => item !== id);
                if (task.status === 'done') setVariantEpoch((n) => n + 1);
              }
            } catch {
              /* A dropped poll is not a failed job. */
            }
          }));
        }
        await sleep(1500);
        if (stop) return;
      }
    }
    loop();
    return () => {
      stop = true;
    };
  }, [upsertTask]);

  useEffect(() => {
    let stop = false;
    async function loop() {
      while (!stop) {
        await sleep(4000);
        if (stop) return;
        try {
          const data = await api.feed();
          const incoming = asList(data, 'tasks');
          setFeed((current) => incoming.map((task) => preferTask(current.find((item) => item.id === task.id), task)));
        } catch {
          /* Keep the feed already on screen. */
        }
      }
    }
    loop();
    return () => {
      stop = true;
    };
  }, []);

  const runMotion = useCallback((dir) => {
    const ms = motionMs();
    if (ms === 0) {
      motionRef.current = null;
      setMotion(null);
      return;
    }
    motionRef.current = dir;
    setMotion({ dir });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      motionRef.current = null;
      setMotion(null);
    }, ms);
  }, []);

  const push = useCallback((view) => {
    if (motionRef.current === 'back') return;
    const current = stackRef.current;
    const top = current[current.length - 1];
    if (view.type === 'settings' && top?.type === 'settings') return;
    const entry = {
      ...view,
      key: `${view.type}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
    };
    const next = [...current, entry];
    stackRef.current = next;
    setStack(next);
    runMotion('forward');
    if (window.matchMedia(NARROW).matches) {
      setLeftOpen(false);
      setRightOpen(false);
    }
  }, [runMotion]);

  const pop = useCallback(() => {
    const current = stackRef.current;
    if (current.length <= 1 || motionRef.current === 'back') return;
    const ms = motionMs();
    if (ms === 0) {
      const next = current.slice(0, -1);
      stackRef.current = next;
      setStack(next);
      return;
    }
    motionRef.current = 'back';
    setMotion({ dir: 'back' });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const next = stackRef.current.slice(0, -1);
      stackRef.current = next.length ? next : [{ type: 'home', key: 'home' }];
      setStack(stackRef.current);
      motionRef.current = null;
      setMotion(null);
    }, ms);
  }, []);

  const resetHome = useCallback(() => {
    clearTimeout(timer.current);
    const next = [{ type: 'home', key: 'home' }];
    stackRef.current = next;
    setStack(next);
    motionRef.current = null;
    setMotion(null);
    if (window.matchMedia(NARROW).matches) setLeftOpen(false);
  }, []);

  const openProject = useCallback((project) => {
    if (project.kind === 'scratch') push({ type: 'generate', projectId: project.id });
    else push({ type: 'editor', projectId: project.id, parentId: null });
  }, [push]);

  const replaceProject = useCallback((project) => {
    setProjects((list) => list.map((item) => (item.id === project.id ? { ...item, ...project } : item)));
  }, []);

  const removeProject = useCallback((id) => {
    setProjects((list) => list.filter((item) => item.id !== id));
    if (stackRef.current.some((view) => view.projectId === id)) resetHome();
  }, [resetHome]);

  const confirm = useCallback((options) => new Promise((resolve) => {
    setDialog({
      title: options.title,
      message: options.message,
      confirmLabel: options.confirmLabel || 'Confirm',
      danger: Boolean(options.danger),
      onConfirm: () => {
        setDialog(null);
        resolve(true);
      },
      onCancel: () => {
        setDialog(null);
        resolve(false);
      },
    });
  }), []);

  const renderLock = useRef(false);
  const renderShot = useCallback(async (scope, ids) => {
    if (renderLock.current) return;
    renderLock.current = true;
    setRendering(true);
    try {
      const body = scope === 'shape'
        ? { scope: 'shape', shape_id: ids.shapeId }
        : { scope: 'project', project_id: ids.projectId };
      const data = await api.render(body);
      if (!data?.url) throw new Error('The render did not return an image.');
      setDialog({
        title: 'Render',
        imageUrl: data.url,
        onCancel: () => setDialog(null),
      });
    } catch (err) {
      notify(err.message);
    } finally {
      renderLock.current = false;
      setRendering(false);
    }
  }, [notify]);

  const openObject = useCallback((target) => {
    setObjectTarget(target);
    setRightTab('object');
    setRightOpen(true);
  }, []);

  const value = useMemo(() => ({
    config,
    catalog,
    settings,
    projects,
    feed,
    stack,
    narrow,
    leftOpen,
    rightOpen,
    setLeftOpen,
    setRightOpen,
    rightTab,
    setRightTab,
    objectTarget,
    booting,
    bootError,
    variantEpoch,
    rendering,
    notify,
    push,
    pop,
    resetHome,
    openProject,
    replaceProject,
    removeProject,
    refreshProjects,
    refreshSettings,
    confirm,
    watchTasks,
    bumpVariants,
    openObject,
    renderShot,
    loadAll,
  }), [
    config, catalog, settings, projects, feed, stack, narrow, leftOpen, rightOpen, rightTab,
    objectTarget, booting, bootError, variantEpoch, rendering, notify, push, pop, resetHome,
    openProject, replaceProject, removeProject, refreshProjects, refreshSettings, confirm,
    watchTasks, bumpVariants, openObject, renderShot, loadAll,
  ]);

  return (
    <WorkspaceContext.Provider value={value}>
      <div className="app">
        <WorkChrome />
        <div className="stage">
          <Sidebars />
          <ViewStack stack={stack} motion={motion} renderView={renderView} />
          {bootError ? (
            <div className="boot-banner" role="alert">
              <span>{bootError}</span>
              <button type="button" className="btn btn-ghost" onClick={loadAll}>Retry</button>
            </div>
          ) : null}
          {notice ? (
            <div className={`toast ${notice.tone === 'error' ? 'is-error' : ''}`} role="status">
              {notice.message}
            </div>
          ) : null}
        </div>
        {dialog ? (
          <Dialog
            title={dialog.title}
            message={dialog.message}
            imageUrl={dialog.imageUrl}
            confirmLabel={dialog.confirmLabel}
            danger={dialog.danger}
            onConfirm={dialog.onConfirm}
            onCancel={dialog.onCancel}
          />
        ) : null}
      </div>
    </WorkspaceContext.Provider>
  );
}

function renderView(view, { visible }) {
  if (view.type === 'home') return <Home />;
  if (view.type === 'settings') return <SettingsView />;
  if (view.type === 'editor') {
    return <StageEditor projectId={view.projectId} parentId={view.parentId || null} visible={visible} />;
  }
  if (view.type === 'photo') return <PhotoWorkspace projectId={view.projectId} shapeId={view.shapeId} />;
  if (view.type === 'text') return <TextWorkspace projectId={view.projectId} shapeId={view.shapeId} />;
  if (view.type === 'generate') return <GenerateWorkspace projectId={view.projectId} />;
  return null;
}

function preferTask(local, remote) {
  if (!local) return remote;
  const rank = (status) => (status === 'done' || status === 'failed' ? 3 : status === 'running' ? 2 : 1);
  if (rank(local.status) > rank(remote.status)) return { ...remote, ...local };
  const localStage = local.progress?.stage || 0;
  const remoteStage = remote.progress?.stage || 0;
  if (localStage > remoteStage && rank(local.status) === rank(remote.status)) {
    return { ...remote, status: local.status, progress: local.progress };
  }
  return remote;
}
