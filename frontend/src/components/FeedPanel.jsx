import { taskStatusText } from '../model';
import { useWorkspace } from '../workspace';

export default function FeedPanel() {
  const { feed } = useWorkspace();
  if (!feed.length) {
    return <p className="empty-note">Generations will show up here.</p>;
  }
  return (
    <div className="feed-list">
      {feed.map((task) => (
        <article key={task.id} className={`task-card status-${task.status || 'queued'}`}>
          <header>
            <strong>{task.project_name || 'Project'}</strong>
            <span>{taskStatusText(task)}</span>
          </header>
          {task.prompt ? <p>{task.prompt}</p> : null}
          <p className="meta">
            {[task.mode, task.provider_kind, task.model, task.seed != null ? `seed ${task.seed}` : null]
              .filter(Boolean)
              .join(' · ')}
          </p>
          {task.error ? <p className="error-text">{task.error}</p> : null}
          {task.result_url ? <img src={task.result_url} alt="" /> : null}
        </article>
      ))}
    </div>
  );
}
