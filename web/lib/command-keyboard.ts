export type KeyboardPlatform = 'mac' | 'other';
export type PaletteNavigationAction = 'close' | 'next' | 'previous' | 'run' | 'none';

type KeyboardLike = {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
};

export function getKeyboardPlatform(platform: string = ''): KeyboardPlatform {
  return /mac|iphone|ipad|ipod/i.test(platform) ? 'mac' : 'other';
}

export function isCommandPaletteShortcut(event: KeyboardLike, platform: KeyboardPlatform): boolean {
  const key = event.key.toLowerCase();
  if (key !== 'k' || event.altKey || event.shiftKey) {
    return false;
  }

  return platform === 'mac' ? Boolean(event.metaKey) : Boolean(event.ctrlKey);
}

export function getPaletteNavigationAction(event: KeyboardLike): PaletteNavigationAction {
  switch (event.key) {
    case 'Escape':
      return 'close';
    case 'ArrowDown':
      return 'next';
    case 'ArrowUp':
      return 'previous';
    case 'Enter':
      return 'run';
    default:
      return 'none';
  }
}

export function moveSelectionIndex(currentIndex: number, delta: number, itemCount: number): number {
  if (itemCount <= 0) {
    return 0;
  }

  return (currentIndex + delta + itemCount) % itemCount;
}
