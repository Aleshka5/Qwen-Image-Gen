import { cx } from '../model';

export default function ViewStack({ stack, motion, renderView }) {
  const topIndex = stack.length - 1;
  return (
    <div className="view-stack">
      {stack.map((view, index) => {
        const isTop = index === topIndex;
        const isUnder = Boolean(motion) && index === topIndex - 1;
        const parked = !isTop && !isUnder;
        const leaving = isTop && motion?.dir === 'back';
        return (
          <div
            key={view.key}
            className={cx(
              'view',
              isTop && 'view-top',
              isUnder && 'view-under',
              parked && 'view-parked',
              isTop && motion?.dir === 'forward' && 'is-forward',
              leaving && 'is-back',
            )}
            aria-hidden={parked || leaving ? true : undefined}
            inert={parked || leaving ? true : undefined}
          >
            {renderView(view, { visible: !parked })}
          </div>
        );
      })}
    </div>
  );
}
