'use client';

import { useRouter } from 'next/navigation';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';

import {
  filterCommands,
  getStaticCommands,
  normalizeSearchText,
  type CommandIndexResponse,
  type CommandItem,
} from '../../lib/command-index';
import { getPaletteNavigationAction, moveSelectionIndex } from '../../lib/command-keyboard';

type CommandPaletteProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

type RefreshState = 'idle' | 'refreshing' | 'stale';

const COMMAND_INDEX_STORAGE_KEY = 'electronic-mail-command-index:v4';
const RESULT_LIMIT = 8;
const SEARCH_QUERY_MIN_LENGTH = 2;

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const staticCommands = useMemo(() => getStaticCommands(), []);
  const [dynamicCommands, setDynamicCommands] = useState<CommandItem[]>(() => readCachedDynamicCommands());
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [refreshState, setRefreshState] = useState<RefreshState>('idle');
  const [runningCommandId] = useState<string | null>(null);
  const normalizedQuery = useMemo(() => normalizeSearchText(query), [query]);
  const commands = useMemo(
    () =>
      normalizedQuery.length >= SEARCH_QUERY_MIN_LENGTH
        ? [...staticCommands, ...dynamicCommands]
        : staticCommands,
    [dynamicCommands, normalizedQuery.length, staticCommands],
  );
  const results = useMemo(
    () => filterCommands(commands, query, RESULT_LIMIT),
    [commands, query],
  );
  const selectedCommand = results[selectedIndex] ?? null;
  const refreshCommands = useCallback((showState: boolean) => {
    let cancelled = false;

    if (showState) {
      setRefreshState('refreshing');
    }

    fetch('/api/command-index?scope=search', {
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
      },
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error('Command index refresh failed');
        }
        return response.json() as Promise<CommandIndexResponse>;
      })
      .then((index) => {
        if (cancelled) {
          return;
        }
        const nextCommands = getDynamicCommands(index.commands);
        setDynamicCommands(nextCommands);
        writeCachedIndex(index);
        if (showState) {
          setRefreshState('idle');
        }
      })
      .catch(() => {
        if (!cancelled && showState) {
          setRefreshState('stale');
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query, commands]);

  useEffect(() => {
    for (const command of staticCommands) {
      if (command.href) {
        router.prefetch(command.href);
      }
    }
  }, [router, staticCommands]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const frameId = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => window.cancelAnimationFrame(frameId);
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }

    function handleDocumentKeyDown(event: globalThis.KeyboardEvent) {
      if (event.defaultPrevented || event.key !== 'Escape') {
        return;
      }

      event.preventDefault();
      closePalette();
    }

    document.addEventListener('keydown', handleDocumentKeyDown);
    return () => document.removeEventListener('keydown', handleDocumentKeyDown);
  }, [open]);

  useEffect(() => {
    if (!open || normalizedQuery.length < SEARCH_QUERY_MIN_LENGTH) {
      setRefreshState('idle');
      return;
    }

    let cleanup: (() => void) | undefined;
    const timeoutId = window.setTimeout(() => {
      cleanup = refreshCommands(true);
    }, 120);

    return () => {
      window.clearTimeout(timeoutId);
      cleanup?.();
    };
  }, [normalizedQuery, open, refreshCommands]);

  if (!open) {
    return null;
  }

  async function runCommand(command: CommandItem | null) {
    if (command === null || runningCommandId !== null) {
      return;
    }

    if (command.href) {
      closePalette();
      router.prefetch(command.href);
      router.push(command.href);
    }
  }

  function closePalette() {
    setQuery('');
    setSelectedIndex(0);
    onOpenChange(false);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const action = getPaletteNavigationAction(event);
    if (action === 'none') {
      return;
    }

    event.preventDefault();

    if (action === 'close') {
      closePalette();
      return;
    }

    if (action === 'next') {
      setSelectedIndex((currentIndex) => moveSelectionIndex(currentIndex, 1, results.length));
      return;
    }

    if (action === 'previous') {
      setSelectedIndex((currentIndex) => moveSelectionIndex(currentIndex, -1, results.length));
      return;
    }

    void runCommand(selectedCommand);
  }

  return (
    <div className="command-palette-layer" role="dialog" aria-modal="true" aria-label="Command palette">
      <button
        type="button"
        className="command-palette-backdrop"
        aria-label="Close command palette"
        onClick={closePalette}
      />
      <div className="command-palette-panel" onKeyDown={handleKeyDown}>
        <div className="command-palette-search">
          <input
            ref={inputRef}
            className="command-palette-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search work, email, or jump"
            aria-label="Search work, email, or jump"
            data-command-palette-input
          />
        </div>

        {refreshState === 'stale' ? (
          <p className="command-palette-status" role="status">
            Showing saved results.
          </p>
        ) : null}

        {results.length > 0 ? (
          <ul className="command-palette-results" role="listbox" aria-label="Command results">
            {results.map((command, index) => {
              const selected = index === selectedIndex;
              const running = runningCommandId === command.id;
              return (
                <li key={command.id} className="command-palette-result-item" role="option" aria-selected={selected}>
                  <button
                    type="button"
                    className={`command-palette-result ${selected ? 'is-selected' : ''}`}
                    onMouseEnter={() => {
                      setSelectedIndex(index);
                      if (command.href) {
                        router.prefetch(command.href);
                      }
                    }}
                    onFocus={() => {
                      if (command.href) {
                        router.prefetch(command.href);
                      }
                    }}
                    onClick={() => void runCommand(command)}
                    disabled={runningCommandId !== null}
                  >
                    <span className="command-palette-result-copy">
                      <span className="command-palette-result-title">{command.title}</span>
                      <span className="command-palette-result-subtitle">
                        {running ? 'Working...' : command.subtitle}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="command-palette-empty">No matching commands.</p>
        )}
      </div>
    </div>
  );
}

function readCachedDynamicCommands(): CommandItem[] {
  try {
    const cached = window.localStorage.getItem(COMMAND_INDEX_STORAGE_KEY);
    if (cached === null) {
      return [];
    }

    const parsed = JSON.parse(cached) as Partial<CommandIndexResponse>;
    if (!Array.isArray(parsed.commands)) {
      return [];
    }

    return getDynamicCommands(parsed.commands.filter(isCommandItem));
  } catch (_error) {
    return [];
  }
}

function writeCachedIndex(index: CommandIndexResponse) {
  try {
    window.localStorage.setItem(
      COMMAND_INDEX_STORAGE_KEY,
      JSON.stringify({ ...index, commands: getDynamicCommands(index.commands) }),
    );
  } catch (_error) {}
}

function getDynamicCommands(commands: CommandItem[]): CommandItem[] {
  return commands.filter((command) => command.kind !== 'navigation');
}

function isCommandItem(value: unknown): value is CommandItem {
  if (value === null || typeof value !== 'object') {
    return false;
  }

  const candidate = value as Partial<CommandItem>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.kind === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.subtitle === 'string' &&
    Array.isArray(candidate.keywords) &&
    typeof candidate.searchText === 'string' &&
    typeof candidate.priority === 'number'
  );
}
