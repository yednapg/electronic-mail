'use client';

import { useEffect } from 'react';

const themeStorageKey = 'decision-pipeline-theme';
const dashboardViewStorageKey = 'decision-pipeline-dashboard-view';
const themeTransitionMs = 220;
let themeTransitionTimeout: number | undefined;

const defaultViewSettings = {
  brief: true,
  calendar: true,
  now: true,
  today: true,
  worthKnowing: true,
};

const viewDatasetKeys = {
  brief: 'showBrief',
  calendar: 'showCalendar',
  now: 'showNow',
  today: 'showToday',
  worthKnowing: 'showWorthKnowing',
} as const;

type ThemeMode = 'light' | 'dark';
type ViewSettingKey = keyof typeof defaultViewSettings;
type ViewSettings = Record<ViewSettingKey, boolean>;

function isThemeMode(value: string | null): value is ThemeMode {
  return value === 'light' || value === 'dark';
}

function getSystemTheme(): ThemeMode {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getStoredTheme(): ThemeMode | null {
  const storedTheme = window.localStorage.getItem(themeStorageKey);
  return isThemeMode(storedTheme) ? storedTheme : null;
}

function applyTheme(theme: ThemeMode, options: { animate?: boolean } = {}) {
  if (options.animate) {
    document.documentElement.dataset.themeTransition = 'true';
    window.clearTimeout(themeTransitionTimeout);
    themeTransitionTimeout = window.setTimeout(() => {
      delete document.documentElement.dataset.themeTransition;
    }, themeTransitionMs);
  } else {
    window.clearTimeout(themeTransitionTimeout);
    delete document.documentElement.dataset.themeTransition;
  }

  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  updateThemeControls(theme);
}

function updateThemeControls(theme: ThemeMode) {
  const label = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
  document.querySelectorAll('[data-theme-toggle]').forEach((control) => {
    control.setAttribute('aria-label', label);
    control.setAttribute('title', label);
  });
}

function normalizeViewSettings(value: unknown): ViewSettings {
  if (value === null || typeof value !== 'object') {
    return { ...defaultViewSettings };
  }

  const candidate = value as Partial<Record<ViewSettingKey, unknown>>;
  return Object.keys(defaultViewSettings).reduce<ViewSettings>((settings, key) => {
    const settingKey = key as ViewSettingKey;
    settings[settingKey] =
      typeof candidate[settingKey] === 'boolean' ? candidate[settingKey] : defaultViewSettings[settingKey];
    return settings;
  }, { ...defaultViewSettings });
}

function readViewSettings(): ViewSettings {
  try {
    const storedSettings = window.localStorage.getItem(dashboardViewStorageKey);
    return normalizeViewSettings(storedSettings === null ? null : JSON.parse(storedSettings));
  } catch (_error) {
    return { ...defaultViewSettings };
  }
}

function writeViewSettings(settings: ViewSettings) {
  try {
    window.localStorage.setItem(dashboardViewStorageKey, JSON.stringify(settings));
  } catch (_error) {}
}

function syncViewControls(settings: ViewSettings) {
  document.querySelectorAll('[data-dashboard-view-control]').forEach((control) => {
    if (!(control instanceof HTMLInputElement)) {
      return;
    }

    const key = control.getAttribute('data-dashboard-view-control') as ViewSettingKey | null;
    if (key !== null && key in settings) {
      control.checked = settings[key];
    }
  });
}

function applyViewSettings(settings: ViewSettings) {
  Object.keys(defaultViewSettings).forEach((key) => {
    const settingKey = key as ViewSettingKey;
    document.documentElement.dataset[viewDatasetKeys[settingKey]] = settings[settingKey] ? 'true' : 'false';
  });
  syncViewControls(settings);
}

function readViewControls(): ViewSettings {
  const settings = readViewSettings();
  document.querySelectorAll('[data-dashboard-view-control]').forEach((control) => {
    if (!(control instanceof HTMLInputElement)) {
      return;
    }

    const key = control.getAttribute('data-dashboard-view-control') as ViewSettingKey | null;
    if (key !== null && key in settings) {
      settings[key] = control.checked;
    }
  });
  return settings;
}

export function LayoutInteractions() {
  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (!(event.target instanceof Element)) {
        return;
      }

      const themeButton = event.target.closest('[data-theme-toggle]');
      if (themeButton !== null) {
        event.preventDefault();
        const currentTheme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
        const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
        try {
          window.localStorage.setItem(themeStorageKey, nextTheme);
        } catch (_error) {}
        applyTheme(nextTheme, { animate: true });
        return;
      }

      const resetButton = event.target.closest('[data-dashboard-view-reset]');
      if (resetButton !== null) {
        event.preventDefault();
        const settings = { ...defaultViewSettings };
        writeViewSettings(settings);
        applyViewSettings(settings);
      }
    }

    function handleChange(event: Event) {
      if (!(event.target instanceof Element)) {
        return;
      }

      const viewControl = event.target.closest('[data-dashboard-view-control]');
      if (viewControl === null) {
        return;
      }

      const settings = readViewControls();
      writeViewSettings(settings);
      applyViewSettings(settings);
    }

    try {
      applyTheme(getStoredTheme() ?? getSystemTheme());
      applyViewSettings(readViewSettings());
    } catch (_error) {}

    document.addEventListener('click', handleClick);
    document.addEventListener('change', handleChange);

    return () => {
      document.removeEventListener('click', handleClick);
      document.removeEventListener('change', handleChange);
    };
  }, []);

  return null;
}
