import { taskStatusText } from '../model';
import { useWorkspace } from '../workspace';

export default function FeedPanel() {
  const { feed, openFromFeed } = useWorkspace();
  if (!feed.length) {
    return <p className="empty-note">Generations will show up here.</p>;
  }
  return (
    <div className="feed-list">
      {feed.map((task) => (
        <button
          key={task.id}
          type="button"
          className="feed-tile"
          onClick={() => openFromFeed(task)}
        >
          <span className="feed-tile-row">
            <span>{taskStatusText(task)}</span>
            {task.seed != null ? <span>Seed {task.seed}</span> : null}
          </span>
          {task.status === 'done' && task.result_url ? <img src={task.result_url} alt="" /> : null}
        </button>
      ))}
    </div>
  );
}
