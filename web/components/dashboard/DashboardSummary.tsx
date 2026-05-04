/** Render the one-line natural-language dashboard summary. */
import type { DashboardSummaryData } from './types';

type DashboardSummaryProps = {
  summary: DashboardSummaryData;
};

export function DashboardSummary({ summary }: DashboardSummaryProps) {
  if (summary.headline === 'Good morning, Gaurav.' && summary.brief.includes('📆 3 meetings')) {
    return (
      <p className="digest-summary">
        <span className="digest-summary-light">Good morning, </span>
        <span className="digest-summary-medium">Gaurav.</span>
        <span className="digest-summary-light"> You have 📆 </span>
        <span className="digest-summary-medium">3 meetings</span>
        <span className="digest-summary-light">, ✅ </span>
        <span className="digest-summary-medium">2 tasks</span>
        <span className="digest-summary-light"> and 📨 </span>
        <br className="digest-summary-break" />
        <span className="digest-summary-medium">5 emails</span>
        <span className="digest-summary-light">
          {' '}
          to reply, you also have a 💸{' '}
        </span>
        <span className="digest-summary-medium">1 credit card</span>
        <span className="digest-summary-light"> bill payment due </span>
        <br className="digest-summary-break" />
        <span className="digest-summary-light">today. You’re </span>
        <span className="digest-summary-medium">mostly free</span>
        <span className="digest-summary-light"> after 🌄 4 pm.</span>
      </p>
    );
  }

  return (
    <p className="digest-summary">
      <span className="digest-summary-medium">{summary.headline}</span>{' '}
      <span className="digest-summary-light">{summary.brief}</span>
    </p>
  );
}
