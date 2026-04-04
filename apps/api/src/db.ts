import { PrismaClient } from '@prisma/client';

export function createPrismaClient(databaseUrl?: string): PrismaClient {
  if (databaseUrl !== undefined) {
    return new PrismaClient({
      datasources: {
        db: {
          url: databaseUrl,
        },
      },
    });
  }

  return new PrismaClient();
}

export const prisma = createPrismaClient();

export async function testDatabaseConnection(): Promise<void> {
  await prisma.$queryRaw`SELECT 1`;
}
