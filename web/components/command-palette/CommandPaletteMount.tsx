'use client';

import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';

import { getKeyboardPlatform, isCommandPaletteShortcut } from '../../lib/command-keyboard';

const CommandPalette = dynamic(() => import('./CommandPalette').then((mod) => mod.CommandPalette), {
  ssr: false,
});

type IdleCallbacks = {
  requestIdleCallback?: (callback: IdleRequestCallback, options?: IdleRequestOptions) => number;
  cancelIdleCallback?: (handle: number) => void;
};

export function CommandPaletteMount() {
  const [open, setOpen] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const idleCallbacks = window as unknown as IdleCallbacks;
    const load = () => setReady(true);
    const idleId = idleCallbacks.requestIdleCallback ? idleCallbacks.requestIdleCallback(load, { timeout: 1500 }) : 0;
    const timeoutId = idleCallbacks.requestIdleCallback ? 0 : window.setTimeout(load, 500);

    return () => {
      if (idleCallbacks.cancelIdleCallback && idleId !== 0) {
        idleCallbacks.cancelIdleCallback(idleId);
      }
      if (timeoutId !== 0) {
        window.clearTimeout(timeoutId);
      }
    };
  }, []);

  useEffect(() => {
    const platform = getKeyboardPlatform(window.navigator.platform);

    function handleKeyDown(event: KeyboardEvent) {
      if (!isCommandPaletteShortcut(event, platform)) {
        return;
      }

      event.preventDefault();
      setReady(true);
      setOpen(true);
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return ready ? <CommandPalette open={open} onOpenChange={setOpen} /> : null;
}
