import { useEffect, useRef, useState } from 'react';
import { api, asObject } from '../api';
import { cx } from '../model';
import { useWorkspace } from '../workspace';
import FeedPanel from './FeedPanel';
import { TrashIcon } from './Icons';
import ObjectPanel from './ObjectPanel';

export default function Sidebars() {
  const {
    leftOpen,
    rightOpen,
    setLeftOpen,
    setRightOpen,
    rightTab,
    setRightTab,
    narrow,
  } = useWorkspace();
  const showBackdrop = narrow && (leftOpen || rightOpen);

  return (
    <>
      {showBackdrop ? (
        <button
          type="button"
          className="backdrop"
          aria-label="Close panels"
          onClick={() => {
            setLeftOpen(false);
            setRightOpen(false);
          }}
        />
      ) : null}
      <aside id="project-sidebar" className={cx('sidebar sidebar-left', leftOpen && 'open')} aria-hidden={!leftOpen}>
        <ProjectList />
      </aside>
      <aside id="library-sidebar" className={cx('sidebar sidebar-right', rightOpen && 'open')} aria-hidden={!rightOpen}>
        <div className="tabs" role="tablist" aria-label="Library">
          <button
            type="button"
            role="tab"
            aria-selected={rightTab === 'feed'}
            className={rightTab === 'feed' ? 'is-active' : ''}
            onClick={() => setRightTab('feed')}
          >
            Feed
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={rightTab === 'object'}
            className={rightTab === 'object' ? 'is-active' : ''}
            onClick={() => setRightTab('object')}
          >
            This object
          </button>
        </div>
        <div className="side-scroll" role="tabpanel">
          {rightTab === 'feed' ? <FeedPanel /> : <ObjectPanel />}
        </div>
      </aside>
    </>
  );
}

function ProjectList() {
  const {
    projects,
    booting,
    openProject,
    resetHome,
    stack,
    confirm,
    notify,
    replaceProject,
    removeProject,
  } = useWorkspace();
  const [editingId, setEditingId] = useState(null);

  const activeId = [...stack].reverse().find((view) => view.projectId)?.projectId;

  return (
    <>
      <button type="button" className="new-project" onClick={resetHome}>
        New project
      </button>
      {booting ? <p className="empty-note">Loading projects…</p> : null}
      {!booting && projects.length === 0 ? <p className="empty-note">No projects yet.</p> : null}
      <ul className="project-list">
        {projects.map((project) => (
          <ProjectRow
            key={project.id}
            project={project}
            active={project.id === activeId}
            editing={editingId === project.id}
            onEdit={() => setEditingId(project.id)}
            onCancel={() => setEditingId(null)}
            onOpen={() => openProject(project)}
            onRename={async (name) => {
              try {
                const data = await api.renameProject(project.id, name);
                const updated = asObject(data, 'project') || {};
                replaceProject({ ...project, ...updated, name: updated.name || name });
                setEditingId(null);
              } catch (err) {
                notify(err.message);
              }
            }}
            onDelete={async () => {
              const ok = await confirm({
                title: `Delete “${project.name || 'Untitled'}”?`,
                message: 'The project and everything in it will be deleted.',
                confirmLabel: 'Delete',
                danger: true,
              });
              if (!ok) return;
              try {
                await api.deleteProject(project.id);
                removeProject(project.id);
              } catch (err) {
                notify(err.message);
              }
            }}
          />
        ))}
      </ul>
    </>
  );
}

function ProjectRow({ project, active, editing, onEdit, onCancel, onOpen, onRename, onDelete }) {
  const [value, setValue] = useState(project.name || '');
  const inputRef = useRef(null);
  const skipBlur = useRef(false);
  const name = project.name || 'Untitled';

  useEffect(() => {
    setValue(project.name || '');
  }, [project.name]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  async function commit() {
    const next = value.trim();
    if (!next || next === project.name) {
      onCancel();
      return;
    }
    await onRename(next);
  }

  return (
    <li className={cx('project-row', active && 'is-active')}>
      <button type="button" className="project-open" onClick={onOpen} aria-label={`Open ${name}`}>
        <span className="thumb">
          {project.preview_url ? <img src={project.preview_url} alt="" /> : <span>{project.kind === 'scratch' ? 'S' : 'P'}</span>}
        </span>
        <span className="kind">{project.kind === 'scratch' ? 'Scratch' : 'Photo'}</span>
      </button>
      {editing ? (
        <input
          ref={inputRef}
          className="rename-input"
          value={value}
          aria-label={`Rename ${name}`}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              skipBlur.current = true;
              commit();
            } else if (event.key === 'Escape') {
              event.preventDefault();
              skipBlur.current = true;
              onCancel();
            }
          }}
          onBlur={() => {
            if (skipBlur.current) {
              skipBlur.current = false;
              return;
            }
            commit();
          }}
        />
      ) : (
        <button type="button" className="project-name" onClick={onEdit}>
          {name}
        </button>
      )}
      <button type="button" className="trash" aria-label={`Delete ${name}`} onClick={onDelete}>
        <TrashIcon />
      </button>
    </li>
  );
}
