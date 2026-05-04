/** Small date/time formatting helpers shared by dashboard components. */
export function formatClockTime(date: Date): string {
  return date.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

export function formatDate(date: Date): string {
  return date.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
}

export function formatScheduleTime(dateString: string): string {
  /** Format a timed event for agenda display; invalid values collapse to an empty label. */
  const date = toDate(dateString);

  if (date === null) {
    return '';
  }

  return date.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function toAgendaSortValue(dateString: string, isTimed: boolean): number {
  /** Convert agenda dates into a comparable numeric sort key. */
  const parseSource = isTimed ? dateString : `${dateString}T00:00:00`;
  const date = toDate(parseSource);

  if (date === null) {
    return Number.MAX_SAFE_INTEGER;
  }

  return date.getTime();
}

export function hasExplicitTime(dateString: string): boolean {
  /** Detect malformed all-day strings that still contain a time token. */
  return /T\d{2}:\d{2}| \d{2}:\d{2}/.test(dateString);
}

export function isTimedIsoTimestamp(dateString: string): boolean {
  /** Treat only parseable ISO datetime strings as timed events. */
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(dateString)) {
    return false;
  }

  return toDate(dateString) !== null;
}

function toDate(value: string): Date | null {
  /** Accept a few date shapes used by the feed and normalize them for JS Date parsing. */
  const normalizedValue =
    value.match(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?/)?.[0].replace(' ', 'T') ??
    value.match(/^\d{4}-\d{2}-\d{2}T[^ ]+/)?.[0] ??
    value.match(/^\d{4}-\d{2}-\d{2}$/)?.[0] ??
    value;
  const date = new Date(normalizedValue);

  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return date;
}
