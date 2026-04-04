export function formatClockTime(date: Date): string {
  return date.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
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
  const date = toDate(dateString);

  if (date === null) {
    return '';
  }

  return formatClockTime(date);
}

export function toAgendaSortValue(dateString: string, isTimed: boolean): number {
  const parseSource = isTimed ? dateString : `${dateString}T00:00:00`;
  const date = toDate(parseSource);

  if (date === null) {
    return Number.MAX_SAFE_INTEGER;
  }

  return date.getTime();
}

export function hasExplicitTime(dateString: string): boolean {
  return /T\d{2}:\d{2}| \d{2}:\d{2}/.test(dateString);
}

export function isTimedIsoTimestamp(dateString: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(dateString)) {
    return false;
  }

  return toDate(dateString) !== null;
}

function toDate(value: string): Date | null {
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
