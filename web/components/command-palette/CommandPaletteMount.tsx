'use client';

import { useEffect, useState } from 'react';

import { CommandPalette } from './CommandPalette';
import { getKeyboardPlatform, isCommandPaletteShortcut } from '../../lib/command-keyboard';

export function CommandPaletteMount() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const platform = getKeyboardPlatform(window.navigator.platform);

    function handleKeyDown(event: KeyboardEvent) {
      if (!isCommandPaletteShortcut(event, platform)) {
        return;
      }

      event.preventDefault();
      setOpen(true);
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return <CommandPalette open={open} onOpenChange={setOpen} />;
}
