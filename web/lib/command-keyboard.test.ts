import test from 'node:test';
import assert from 'node:assert/strict';

import {
  getKeyboardPlatform,
  getPaletteNavigationAction,
  isCommandPaletteShortcut,
  moveSelectionIndex,
} from './command-keyboard';

test('command palette shortcut follows platform conventions', () => {
  assert.equal(isCommandPaletteShortcut({ key: 'k', metaKey: true }, 'mac'), true);
  assert.equal(isCommandPaletteShortcut({ key: 'k', ctrlKey: true }, 'mac'), false);
  assert.equal(isCommandPaletteShortcut({ key: 'k', ctrlKey: true }, 'other'), true);
  assert.equal(isCommandPaletteShortcut({ key: 'k', ctrlKey: true, shiftKey: true }, 'other'), false);
});

test('keyboard platform detects Apple devices', () => {
  assert.equal(getKeyboardPlatform('MacIntel'), 'mac');
  assert.equal(getKeyboardPlatform('Win32'), 'other');
});

test('palette navigation keys map to local actions', () => {
  assert.equal(getPaletteNavigationAction({ key: 'Escape' }), 'close');
  assert.equal(getPaletteNavigationAction({ key: 'ArrowDown' }), 'next');
  assert.equal(getPaletteNavigationAction({ key: 'ArrowUp' }), 'previous');
  assert.equal(getPaletteNavigationAction({ key: 'Enter' }), 'run');
  assert.equal(getPaletteNavigationAction({ key: 'Tab' }), 'none');
});

test('selection movement wraps around result boundaries', () => {
  assert.equal(moveSelectionIndex(0, -1, 3), 2);
  assert.equal(moveSelectionIndex(2, 1, 3), 0);
  assert.equal(moveSelectionIndex(0, 1, 0), 0);
});
