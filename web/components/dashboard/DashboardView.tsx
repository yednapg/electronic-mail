import React from 'react';

import { DashboardAgenda } from './DashboardAgenda';
import { DashboardMeta } from './DashboardMeta';
import { DashboardSection } from './DashboardSection';
import { DashboardSummary } from './DashboardSummary';
import type { DashboardAgendaItem, DashboardSectionData, DashboardSummaryData } from './types';

type DashboardViewProps = {
  dateLabel: string;
  timeLabel: string;
  liveMeta: boolean;
  summary: DashboardSummaryData;
  agenda: DashboardAgendaItem[];
  sections: DashboardSectionData[];
};

export function DashboardView({ dateLabel, timeLabel, liveMeta, summary, agenda, sections }: DashboardViewProps) {
  return (
    <main className="digest-page">
      <div className="digest-shell">
        <DashboardMeta dateLabel={dateLabel} timeLabel={timeLabel} live={liveMeta} />
        <DashboardSummary summary={summary} />
        <DashboardAgenda items={agenda} />

        <div className="digest-sections">
          {sections.map((section) => (
            <div key={section.id} className="digest-section-slot" data-dashboard-section={section.id}>
              <DashboardSection
                sectionId={section.id}
                title={section.title}
                items={section.items}
                maxVisible={section.maxVisible}
                collapsedByDefault={section.collapsedByDefault}
              />
            </div>
          ))}
        </div>

        <p className="digest-empty-state" data-dashboard-empty>
          All To-do blocks are hidden by view settings.
        </p>
      </div>
    </main>
  );
}
