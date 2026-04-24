import test from 'node:test';
import assert from 'node:assert/strict';

import { getBackendURL } from './api';

test('backend URL defaults to local backend', () => {
  const previous = process.env.DECISION_PIPELINE_BACKEND_URL;
  delete process.env.DECISION_PIPELINE_BACKEND_URL;

  try {
    assert.equal(getBackendURL(), 'http://localhost:3001');
  } finally {
    if (previous === undefined) {
      delete process.env.DECISION_PIPELINE_BACKEND_URL;
    } else {
      process.env.DECISION_PIPELINE_BACKEND_URL = previous;
    }
  }
});

test('backend URL comes from env without trailing slash', () => {
  const previous = process.env.DECISION_PIPELINE_BACKEND_URL;
  process.env.DECISION_PIPELINE_BACKEND_URL = 'https://api.example.com/';

  try {
    assert.equal(getBackendURL(), 'https://api.example.com');
  } finally {
    if (previous === undefined) {
      delete process.env.DECISION_PIPELINE_BACKEND_URL;
    } else {
      process.env.DECISION_PIPELINE_BACKEND_URL = previous;
    }
  }
});
