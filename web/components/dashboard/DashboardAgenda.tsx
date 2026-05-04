/** Render the compact agenda strip above the feed sections. */
import type { DashboardAgendaItem } from './types';

type DashboardAgendaProps = {
  items: DashboardAgendaItem[];
};

export function DashboardAgenda({ items }: DashboardAgendaProps) {
  return (
    <section className="digest-agenda" aria-label="Calendar items">
      {items.length > 0 ? (
        <div className="digest-agenda-list">
          {items.map((item) => (
            <div key={item.id} className="digest-agenda-row">
              <span className={`digest-agenda-time digest-agenda-time-${item.tone}`}>{item.time}</span>
              <span className="digest-agenda-title">{item.title}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="digest-agenda-empty">No calendar items right now.</p>
      )}
    </section>
  );
}
