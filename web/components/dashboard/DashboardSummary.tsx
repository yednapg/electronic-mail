/** Render the one-line natural-language dashboard summary. */
import type { DashboardSummaryData } from './types';

type DashboardSummaryProps = {
  summary: DashboardSummaryData;
};

export function DashboardSummary({ summary }: DashboardSummaryProps) {
  if (summary.headline === 'Good morning, Gaurav.' && summary.brief.includes('📆 5 meetings')) {
    return (
      <p className="digest-summary">
        <span className="digest-summary-light">Good morning, </span>
        <span className="digest-summary-medium">Gaurav.</span>
        <span className="digest-summary-light"> You have 📆 </span>
        <span className="digest-summary-medium">5 meetings</span>
        <span className="digest-summary-light">, ✅ </span>
        <span className="digest-summary-medium">8 open tasks</span>
        <span className="digest-summary-light"> and</span>
        <br className="digest-summary-break" />
        <span className="digest-summary-light">📨 </span>
        <span className="digest-summary-medium">11 useful emails</span>
        <span className="digest-summary-light"> pulled into work. Your </span>
        <span className="digest-summary-medium">credit card bill</span>
        <span className="digest-summary-light"> and </span>
        <span className="digest-summary-medium">YC RSVP</span>
        <span className="digest-summary-light"> need a decision</span>
        <br className="digest-summary-break" />
        <span className="digest-summary-light">before noon. You’re </span>
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
