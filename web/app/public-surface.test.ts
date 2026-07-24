import assert from 'node:assert/strict';
import test from 'node:test';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { HomePageContent } from './page';
import {
  OAuthCompletionUnavailableView,
  OAuthCompletionView,
  resolveOAuthCompletionState,
} from './post-login/page';

test('public landing page presents the native Mac app without a web sign-in', () => {
  const markup = renderToStaticMarkup(React.createElement(HomePageContent, {
    downloadConfig: {
      enablement: 'enabled',
      url: 'https://downloads.electronicmail.app/ElectronicMail.dmg',
    },
  }));

  assert.match(markup, /Built for your Mac/);
  assert.match(markup, /native Gmail client for macOS/);
  assert.match(markup, /href="https:\/\/downloads\.electronicmail\.app\/ElectronicMail\.dmg"/);
  assert.match(markup, /Download for macOS/);
  assert.doesNotMatch(markup, /Continue with Google|auth\/google|href="\/gmail"|href="\/dashboard"/);
});

test('public landing page fails closed when downloads are paused', () => {
  const markup = renderToStaticMarkup(React.createElement(HomePageContent, {
    downloadConfig: {
      enablement: 'paused',
      url: '',
    },
  }));

  assert.match(markup, /Downloads paused/);
  assert.doesNotMatch(markup, /Download for macOS/);
  assert.doesNotMatch(markup, /href="https:\/\/downloads\.electronicmail\.app/);
});

test('public landing page does not describe broken configuration as an intentional pause', () => {
  for (const downloadConfig of [
    {
      enablement: 'invalid' as const,
      url: 'https://downloads.electronicmail.app/ElectronicMail.dmg',
    },
    {
      enablement: 'enabled' as const,
      url: '',
    },
  ]) {
    const markup = renderToStaticMarkup(React.createElement(HomePageContent, {
      downloadConfig,
    }));

    assert.match(markup, /Download unavailable/);
    assert.doesNotMatch(markup, /Downloads paused|Download for macOS/);
    assert.doesNotMatch(markup, /href="https:\/\/downloads\.electronicmail\.app/);
  }
});

test('OAuth completion tells the user to return to the native app', () => {
  const markup = renderToStaticMarkup(React.createElement(OAuthCompletionView));

  assert.match(markup, /Google account connected/);
  assert.match(markup, /Return to Electronic Mail on your Mac/);
  assert.match(markup, /close this browser window/);
  assert.doesNotMatch(markup, /href="\/gmail"|href="\/dashboard"/);
});

test('OAuth completion resolves backend failure to a safe unavailable state', async () => {
  assert.equal(
    await resolveOAuthCompletionState(async () => {
      throw new Error('fetch failed: private backend detail');
    }),
    'unavailable',
  );

  const markup = renderToStaticMarkup(React.createElement(OAuthCompletionUnavailableView));
  assert.match(markup, /Connection status unavailable/);
  assert.match(markup, /Return to Electronic Mail on your Mac/);
  assert.match(markup, /href="\/post-login"/);
  assert.match(markup, /Try again/);
  assert.match(markup, /href="\/support"/);
  assert.doesNotMatch(markup, /fetch failed|private backend detail|Google account connected/);
});

test('OAuth completion distinguishes connected and disconnected backend responses', async () => {
  assert.equal(await resolveOAuthCompletionState(async () => ({ connected: true })), 'connected');
  assert.equal(await resolveOAuthCompletionState(async () => ({ connected: false })), 'disconnected');
});
