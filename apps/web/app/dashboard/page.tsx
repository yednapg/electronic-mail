import { getFeed } from '../../lib/api';
import {
  formatClockTime,
  formatDisplayTitle,
  formatScheduleTime,
  getTimeOfDay,
} from '../../lib/formatting';
import type { FeedItem, PrimaryActionType } from '../../lib/types';

const FALLBACK_AGENDA_TIMES = ['09:00', '11:00', '13:30', '14:45', '15:00'];

export default async function DashboardPage() {
  const feed = await getFeed();
  const now = new Date();
  const visibleItems = [...feed.now, ...feed.today, ...feed.worth_knowing];
  const urgentItems = feed.now;
  const importantItems = [...feed.today, ...feed.worth_knowing];
  const agendaItems = buildAgendaItems(visibleItems);
  const replyCount = countByAction(visibleItems, 'reply');
  const paymentCount = countByAction(visibleItems, 'pay');
  const taskCount = visibleItems.filter(isTaskLikeItem).length;

  return (
    <main className="digest-page">
      <div className="digest-shell">
        <header className="digest-topbar">
          <h1 className="digest-greeting">
            <span className='digest-summary-light'>Good {getTimeOfDay(now)}</span>, <span className="digest-greeting-name, digest-summary-medium">TestUser.</span>
          </h1>
          <div className="digest-clock digest-summary-light">{formatClockTime(now)}</div>
        </header>

        <p className="digest-summary">
          <span className="digest-summary-light">You have </span>
          <span className="digest-summary-medium">
            {renderCountPhrase('🗓️', visibleItems.length, 'priority item', 'priority items')}
          </span>
          <span className="digest-summary-light">, </span>
          <span className="digest-summary-medium">
            {renderCountPhrase('✅', taskCount, 'task', 'tasks')}
          </span>
          <span className="digest-summary-light"> and </span>
          <span className="digest-summary-medium">
            {renderCountPhrase('📩', replyCount, 'email to reply', 'emails to reply')}
          </span>
          <span className="digest-summary-light">, you also have </span>
          <span className="digest-summary-medium">
            {renderCountPhrase('💸', paymentCount, 'bill payment due today', 'bill payments due today')}
          </span>
          <span className="digest-summary-light">. You're </span>
          <span className="digest-summary-medium">mostly free</span>
          <span className="digest-summary-light"> after </span>
          <span className="digest-summary-medium">4 pm</span>
          <span className="digest-summary-light">.</span>
        </p>

        <section className="digest-agenda" aria-label="Today's timeline">
          {agendaItems.length === 0 ? (
            <div className="digest-agenda-empty">No scheduled focus blocks for today.</div>
          ) : (
            agendaItems.map((item, index) => (
              <div key={item.id} className="digest-agenda-row">
                <span className={`digest-agenda-time digest-agenda-time-${index % 5}`}>
                  {item.time}
                </span>
                <span className="digest-agenda-title">{item.title}</span>
              </div>
            ))
          )}
        </section>

        <ChecklistSection
          title="Urgent"
          items={urgentItems}
          emptyMessage="Nothing urgent right now."
        />
        <ChecklistSection
          title="Important"
          items={importantItems}
          emptyMessage="No other important follow-ups for today."
        />
      </div>
    </main>
  );
}

function ChecklistSection({
  title,
  items,
  emptyMessage,
}: {
  title: string;
  items: FeedItem[];
  emptyMessage: string;
}) {
  return (
    <section className="digest-section">
      <div className="digest-section-header">
        <h2 className="digest-section-title">{title}</h2>
        <span className="digest-section-menu" aria-hidden="true">
          ...
        </span>
      </div>

      {items.length === 0 ? (
        <p className="digest-empty-state">{emptyMessage}</p>
      ) : (
        <ul className="digest-list">
          {items.map((item) => (
            <li key={item.id} className="digest-list-item">
              <span className="digest-checkbox" aria-hidden="true">
                ☑
              </span>
              <span>{formatDisplayTitle(item.title)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function buildAgendaItems(items: FeedItem[]) {
  return items.slice(0, 5).map((item, index) => ({
    id: item.id,
    time: formatScheduleTime(item.created_at) || FALLBACK_AGENDA_TIMES[index],
    title: formatDisplayTitle(item.title),
  }));
}

function countByAction(items: FeedItem[], action: PrimaryActionType): number {
  return items.filter((item) => item.primary_action === action).length;
}

function isTaskLikeItem(item: FeedItem): boolean {
  return item.primary_action !== 'reply' && item.primary_action !== 'pay';
}

function renderCountPhrase(
  emoji: string,
  count: number,
  singularLabel: string,
  pluralLabel: string,
): string {
  const safeCount = count > 0 ? count : 0;
  const suffix = safeCount === 1 ? singularLabel : pluralLabel;

  return `${emoji} ${safeCount} ${suffix}`;
}
