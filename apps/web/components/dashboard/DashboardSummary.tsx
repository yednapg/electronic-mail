import type { DashboardSummaryData } from './types';

type DashboardSummaryProps = {
  summary: DashboardSummaryData;
};

export function DashboardSummary({ summary }: DashboardSummaryProps) {
  return (
    <p className="digest-summary">
      <span className="digest-summary-light">{summary.greeting}, </span>
      <span className="digest-summary-medium">{summary.name}.</span>
      <span className="digest-summary-light"> You have </span>
      <span className="digest-summary-strong">
        {renderCountPhrase('📆', summary.meetingCount, 'meeting', 'meetings')}
      </span>
      <span className="digest-summary-light">, </span>
      <span className="digest-summary-strong">
        {renderCountPhrase('✅', summary.taskCount, 'task', 'tasks')}
      </span>
      <span className="digest-summary-light"> and </span>
      <span className="digest-summary-strong">
        {renderCountPhrase('📨', summary.replyCount, 'email to reply', 'emails to reply')}
      </span>
      <span className="digest-summary-light">, you also have a </span>
      <span className="digest-summary-strong">
        {renderCountPhrase('💸', summary.paymentCount, 'credit card bill payment due today', 'credit card bill payments due today')}
      </span>
      <span className="digest-summary-light">. You're </span>
      <span className="digest-summary-medium">mostly free</span>
      <span className="digest-summary-light"> after </span>
      <span className="digest-summary-medium">{summary.freeAfterLabel}</span>
      <span className="digest-summary-light">.</span>
    </p>
  );
}

function renderCountPhrase(
  emoji: string,
  count: number,
  singularLabel: string,
  pluralLabel: string,
): string {
  const suffix = count === 1 ? singularLabel : pluralLabel;
  return `${emoji} ${count} ${suffix}`;
}
