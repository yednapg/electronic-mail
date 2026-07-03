import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

test('macOS real launch path uses the backend client, not demo fixtures', () => {
  const appSource = readSource('macos/ElectronicMail/ElectronicMail/Mac/ElectronicMailApp.swift');
  assert.match(appSource, /LiveBackendAppClient\(baseURL: AppConfiguration\.defaultBackendURL\)/);
  assert.doesNotMatch(appSource, /DemoAppClient\(/);
});

test('macOS demo client and run mode are debug-only', () => {
  const demoClientSource = readSource('macos/ElectronicMail/ElectronicMail/Core/DemoAppClient.swift');
  const appClientSource = readSource('macos/ElectronicMail/ElectronicMail/Core/AppClient.swift');

  assert.match(demoClientSource, /#if DEBUG\s+public final class DemoAppClient/);
  assert.match(appClientSource, /#if DEBUG\s+case demo\s+#endif/);
});

test('macOS setup keeps waiting for real inbox readiness instead of timing out', () => {
  const appSource = readSource('macos/ElectronicMail/ElectronicMail/Mac/ElectronicMailApp.swift');

  assert.match(appSource, /longWaitStatusSeconds: 60/);
  assert.match(appSource, /Still setting things up, keep this open/);
  assert.doesNotMatch(appSource, /elapsed >= maximumWaitSeconds/);
  assert.match(appSource, /store\.currentReadiness\?\.stage == "failed"/);
});

test('web root decides sign-in and inbox redirect from auth state, not dashboard payload', () => {
  const rootPageSource = readSource('web/app/page.tsx');

  assert.match(rootPageSource, /getGoogleAuthState/);
  assert.doesNotMatch(rootPageSource, /getDashboard/);
  assert.match(rootPageSource, /redirect\(firstRunReady \? '\/gmail' : '\/post-login'\)/);
});

test('web post-login redirects when backend readiness is true without artificial minimum wait', () => {
  const postLoginSource = readSource('web/app/post-login/rotating-status.tsx');

  assert.match(postLoginSource, /waitForPostLoginReady/);
  assert.match(postLoginSource, /warmPostLoginCaches/);
  assert.doesNotMatch(postLoginSource, /FIRST_TIME_MINIMUM_MS/);
  assert.doesNotMatch(postLoginSource, /RETURNING_MINIMUM_MS/);
  assert.doesNotMatch(postLoginSource, /remainingMs/);
});

function readSource(path: string): string {
  return readFileSync(resolve(repoRoot, path), 'utf8');
}
