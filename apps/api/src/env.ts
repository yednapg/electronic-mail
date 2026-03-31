import path from 'node:path';
import dotenv from 'dotenv';

dotenv.config({
  path: path.resolve(__dirname, '../.env'),
});

const port = Number(process.env.PORT ?? '3001');
const databaseUrl = process.env.DATABASE_URL;
const corsOrigin = process.env.CORS_ORIGIN ?? 'http://localhost:5173';

if (!databaseUrl) {
  throw new Error('DATABASE_URL is required');
}

export const env = {
  port,
  databaseUrl,
  corsOrigin,
};
