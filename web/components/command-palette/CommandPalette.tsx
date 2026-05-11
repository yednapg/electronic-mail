'use client';

import { useRouter } from 'next/navigation';
import {
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';

import {
  filterCommands,
  getStaticCommands,
  type CommandIndexResponse,
  type CommandItem,
} from '../../lib/command-index';
import { getPaletteNavigationAction, moveSelectionIndex } from '../../lib/command-keyboard';

type CommandPaletteProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

type RefreshState = 'idle' | 'refreshing' | 'stale';

const COMMAND_INDEX_STORAGE_KEY = 'electronic-mail-command-index:v1';
const RESULT_LIMIT = 8;

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [commands, setCommands] = useState<CommandItem[]>(() => readCachedCommands());
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [refreshState, setRefreshState] = useState<RefreshState>('idle');
  const [runningCommandId, setRunningCommandId] = useState<string | null>(null);
  const results = useMemo(
    () => filterCommands(commands, deferredQuery, RESULT_LIMIT),
    [commands, deferredQuery],
  );
  const selectedCommand = results[selectedIndex] ?? null;

  useEffect(() => {
    setSelectedIndex(0);
  }, [deferredQuery, commands]);

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

    let cancelled = false;
    setRefreshState('refreshing');

    fetch('/api/command-index', {
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
        setCommands(index.commands);
        writeCachedIndex(index);
        setRefreshState('idle');
      })
      .catch(() => {
        if (!cancelled) {
          setRefreshState('stale');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) {
    return null;
  }

  async function runCommand(command: CommandItem | null) {
    if (command === null || runningCommandId !== null) {
      return;
    }

    if (command.action?.kind === 'complete-entity') {
      setRunningCommandId(command.id);
      try {
        const response = await fetch(`/api/entities/${encodeURIComponent(command.action.entityId)}/complete`, {
          method: 'POST',
          cache: 'no-store',
          headers: {
            Accept: 'application/json',
          },
        });

        if (!response.ok) {
          throw new Error('Complete command failed');
        }

        removeEntityCommands(command.action.entityId);
        router.refresh();
        closePalette();
      } finally {
        setRunningCommandId(null);
      }
      return;
    }

    if (command.href) {
      router.push(command.href);
      closePalette();
    }
  }

  function removeEntityCommands(entityId: string) {
    setCommands((currentCommands) => {
      const nextCommands = currentCommands.filter((command) => command.entityId !== entityId);
      writeCachedIndex({ generatedAt: new Date().toISOString(), commands: nextCommands });
      return nextCommands;
    });
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
            placeholder="Search work or jump"
            aria-label="Search work or jump"
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
                    onMouseEnter={() => setSelectedIndex(index)}
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

function readCachedCommands(): CommandItem[] {
  const fallbackCommands = getStaticCommands();

  try {
    const cached = window.localStorage.getItem(COMMAND_INDEX_STORAGE_KEY);
    if (cached === null) {
      return fallbackCommands;
    }

    const parsed = JSON.parse(cached) as Partial<CommandIndexResponse>;
    if (!Array.isArray(parsed.commands)) {
      return fallbackCommands;
    }

    return parsed.commands.filter(isCommandItem);
  } catch (_error) {
    return fallbackCommands;
  }
}

function writeCachedIndex(index: CommandIndexResponse) {
  try {
    window.localStorage.setItem(COMMAND_INDEX_STORAGE_KEY, JSON.stringify(index));
  } catch (_error) {}
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
