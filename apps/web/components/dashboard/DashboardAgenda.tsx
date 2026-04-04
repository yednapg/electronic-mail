import type { DashboardAgendaItem } from './types';

type DashboardAgendaProps = {
  items: DashboardAgendaItem[];
};

export function DashboardAgenda({ items }: DashboardAgendaProps) {
  return (
    <section className="digest-agenda" aria-label="Calendar items">
      <div className="digest-agenda-list">
        {items.map((item) => (
          <div key={item.id} className="digest-agenda-row">
            <span className={`digest-agenda-time digest-agenda-time-${item.tone}`}>{item.time}</span>
            <span className="digest-agenda-title">{item.title}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
