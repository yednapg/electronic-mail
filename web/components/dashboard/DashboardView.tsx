import { DashboardAgenda } from './DashboardAgenda';
import { DashboardMeta } from './DashboardMeta';
import { DashboardSection } from './DashboardSection';
import { DashboardSummary } from './DashboardSummary';
import type { DashboardAgendaItem, DashboardSectionData, DashboardSummaryData } from './types';

type DashboardViewSettings = {
  brief: boolean;
  calendar: boolean;
  now: boolean;
  today: boolean;
  worthKnowing: boolean;
};

type DashboardViewSettingKey = keyof DashboardViewSettings;

type DashboardViewProps = {
  dateLabel: string;
  timeLabel: string;
  liveMeta: boolean;
  summary: DashboardSummaryData;
  agenda: DashboardAgendaItem[];
  sections: DashboardSectionData[];
};

const VIEW_SETTING_OPTIONS: Array<{ key: DashboardViewSettingKey; label: string }> = [
  { key: 'brief', label: 'Brief' },
  { key: 'calendar', label: 'Calendar' },
  { key: 'now', label: 'Now' },
  { key: 'today', label: 'Today' },
  { key: 'worthKnowing', label: 'Worth Knowing' },
];

export function DashboardView({ dateLabel, timeLabel, liveMeta, summary, agenda, sections }: DashboardViewProps) {
  return (
    <main className="digest-page">
      <DashboardSettingsPanel />
      <div className="digest-shell">
        <DashboardMeta dateLabel={dateLabel} timeLabel={timeLabel} live={liveMeta} />
        <DashboardSummary summary={summary} />
        <DashboardAgenda items={agenda} />

        {sections.length > 0 ? (
          <div className="digest-sections">
            {sections.map((section) => (
              <div key={section.id} className="digest-section-slot" data-dashboard-section={section.id}>
                <DashboardSection
                  title={section.title}
                  items={section.items}
                  maxVisible={section.maxVisible}
                  collapsedByDefault={section.collapsedByDefault}
                />
              </div>
            ))}
          </div>
        ) : null}

        <p className="digest-empty-state" data-dashboard-empty>
          All dashboard blocks are hidden.
        </p>
      </div>
    </main>
  );
}

function DashboardSettingsPanel() {
  return (
    <details className="view-settings">
      <summary className="view-settings-button" aria-label="Dashboard view settings" title="Dashboard view settings">
        <SettingsIcon />
      </summary>
      <div id="dashboard-view-settings" className="view-settings-popover">
        <div className="view-settings-panel" aria-label="Dashboard view settings">
          <div className="view-settings-header">
            <p className="view-settings-title">View</p>
            <button type="button" className="view-settings-reset" data-dashboard-view-reset>
              Reset
            </button>
          </div>
          <div className="view-settings-options">
            {VIEW_SETTING_OPTIONS.map((option) => (
              <label
                key={option.key}
                className="view-setting-row"
              >
                <span className="view-setting-label">{option.label}</span>
                <input
                  type="checkbox"
                  className="view-setting-input"
                  data-dashboard-view-control={option.key}
                  defaultChecked
                />
                <span className="view-setting-switch" aria-hidden="true">
                  <span className="view-setting-switch-knob" />
                </span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </details>
  );
}

function SettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="view-settings-icon">
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 13.4a7.7 7.7 0 0 0 .1-1.4 7.7 7.7 0 0 0-.1-1.4l2-1.5-2-3.5-2.4 1a8.1 8.1 0 0 0-2.4-1.4L14.2 2h-4.4l-.4 3.2A8.1 8.1 0 0 0 7 6.6l-2.4-1-2 3.5 2 1.5a7.7 7.7 0 0 0-.1 1.4 7.7 7.7 0 0 0 .1 1.4l-2 1.5 2 3.5 2.4-1a8.1 8.1 0 0 0 2.4 1.4l.4 3.2h4.4l.4-3.2a8.1 8.1 0 0 0 2.4-1.4l2.4 1 2-3.5-2-1.5Z" />
    </svg>
  );
}
