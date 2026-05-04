'use client';

/** Client-side view controls for the dashboard without changing server data shaping. */
import { useEffect, useState } from 'react';

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

const DASHBOARD_VIEW_STORAGE_KEY = 'decision-pipeline-dashboard-view';

const DEFAULT_VIEW_SETTINGS: DashboardViewSettings = {
  brief: true,
  calendar: true,
  now: true,
  today: true,
  worthKnowing: true,
};

const VIEW_SETTING_OPTIONS: Array<{ key: DashboardViewSettingKey; label: string }> = [
  { key: 'brief', label: 'Brief' },
  { key: 'calendar', label: 'Calendar' },
  { key: 'now', label: 'Now' },
  { key: 'today', label: 'Today' },
  { key: 'worthKnowing', label: 'Worth Knowing' },
];

export function DashboardView({ dateLabel, timeLabel, liveMeta, summary, agenda, sections }: DashboardViewProps) {
  const [settings, setSettings] = useState<DashboardViewSettings>(DEFAULT_VIEW_SETTINGS);
  const [loadedStoredSettings, setLoadedStoredSettings] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const visibleSections = sections.filter((section) => settings[settingKeyForSection(section.id)]);
  const hasVisibleMainContent = settings.brief || settings.calendar || visibleSections.length > 0;
  const shellClassName = [
    'digest-shell',
    !settings.brief ? 'digest-shell-brief-hidden' : '',
    !settings.calendar ? 'digest-shell-calendar-hidden' : '',
  ]
    .filter(Boolean)
    .join(' ');

  useEffect(() => {
    setSettings(readStoredSettings() ?? DEFAULT_VIEW_SETTINGS);
    setLoadedStoredSettings(true);
  }, []);

  useEffect(() => {
    if (!loadedStoredSettings) {
      return;
    }

    writeStoredSettings(settings);
  }, [loadedStoredSettings, settings]);

  useEffect(() => {
    if (!settingsOpen) {
      return;
    }

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSettingsOpen(false);
      }
    };

    window.addEventListener('keydown', closeOnEscape);

    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [settingsOpen]);

  function toggleSetting(key: DashboardViewSettingKey) {
    setSettings((currentSettings) => ({
      ...currentSettings,
      [key]: !currentSettings[key],
    }));
  }

  function resetSettings() {
    setSettings(DEFAULT_VIEW_SETTINGS);
  }

  return (
    <main className="digest-page">
      <DashboardSettingsPanel
        open={settingsOpen}
        settings={settings}
        onOpenChange={setSettingsOpen}
        onToggleSetting={toggleSetting}
        onReset={resetSettings}
      />
      <div className={shellClassName}>
        <DashboardMeta dateLabel={dateLabel} timeLabel={timeLabel} live={liveMeta} />
        {settings.brief ? <DashboardSummary summary={summary} /> : null}
        {settings.calendar ? <DashboardAgenda items={agenda} /> : null}

        {visibleSections.length > 0 ? (
          <div className="digest-sections">
            {visibleSections.map((section) => (
              <DashboardSection
                key={section.id}
                title={section.title}
                items={section.items}
                maxVisible={section.maxVisible}
                collapsedByDefault={section.collapsedByDefault}
              />
            ))}
          </div>
        ) : null}

        {!hasVisibleMainContent ? <p className="digest-empty-state">All dashboard blocks are hidden.</p> : null}
      </div>
    </main>
  );
}

function DashboardSettingsPanel({
  open,
  settings,
  onOpenChange,
  onToggleSetting,
  onReset,
}: {
  open: boolean;
  settings: DashboardViewSettings;
  onOpenChange: (open: boolean) => void;
  onToggleSetting: (key: DashboardViewSettingKey) => void;
  onReset: () => void;
}) {
  return (
    <div className={`view-settings ${open ? 'is-open' : ''}`}>
      <button
        type="button"
        className="view-settings-button"
        aria-label="Dashboard view settings"
        aria-expanded={open}
        aria-controls="dashboard-view-settings"
        title="Dashboard view settings"
        onClick={() => onOpenChange(!open)}
      >
        <SettingsIcon />
      </button>
      <div id="dashboard-view-settings" className="view-settings-popover" aria-hidden={!open}>
        <div className="view-settings-panel" aria-label="Dashboard view settings">
          <div className="view-settings-header">
            <p className="view-settings-title">View</p>
            <button type="button" className="view-settings-reset" disabled={!open} onClick={onReset}>
              Reset
            </button>
          </div>
          <div className="view-settings-options">
            {VIEW_SETTING_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                className="view-setting-row"
                role="switch"
                aria-checked={settings[option.key]}
                disabled={!open}
                onClick={() => onToggleSetting(option.key)}
              >
                <span className="view-setting-label">{option.label}</span>
                <span className={`view-setting-switch ${settings[option.key] ? 'is-enabled' : ''}`} aria-hidden="true">
                  <span className="view-setting-switch-knob" />
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
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

function settingKeyForSection(sectionId: DashboardSectionData['id']): DashboardViewSettingKey {
  switch (sectionId) {
    case 'now':
      return 'now';
    case 'today':
      return 'today';
    case 'worth-knowing':
      return 'worthKnowing';
  }

  const exhaustiveSectionId: never = sectionId;
  return exhaustiveSectionId;
}

function readStoredSettings(): DashboardViewSettings | null {
  try {
    const storedValue = window.localStorage.getItem(DASHBOARD_VIEW_STORAGE_KEY);

    if (storedValue === null) {
      return null;
    }

    const parsedValue: unknown = JSON.parse(storedValue);

    if (!isPartialSettingsRecord(parsedValue)) {
      return null;
    }

    return {
      ...DEFAULT_VIEW_SETTINGS,
      ...parsedValue,
    };
  } catch (_error) {
    return null;
  }
}

function writeStoredSettings(settings: DashboardViewSettings) {
  try {
    window.localStorage.setItem(DASHBOARD_VIEW_STORAGE_KEY, JSON.stringify(settings));
  } catch (_error) {
    // The visible state should still change when storage is unavailable.
  }
}

function isPartialSettingsRecord(value: unknown): value is Partial<DashboardViewSettings> {
  if (value === null || typeof value !== 'object') {
    return false;
  }

  const record = value as Record<string, unknown>;

  return VIEW_SETTING_OPTIONS.every((option) => {
    const settingValue = record[option.key];
    return settingValue === undefined || typeof settingValue === 'boolean';
  });
}
