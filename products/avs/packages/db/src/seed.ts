import { prisma, JobStatus, LedgerType } from './index.js';

export async function seed() {
  await prisma.ledgerEntry.deleteMany();
  await prisma.job.deleteMany();
  await prisma.template.deleteMany();
  await prisma.wallet.deleteMany();
  await prisma.user.deleteMany();

  const user = await prisma.user.create({
    data: {
      email: 'seed-user@avs.network',
      name: 'Seed Operator',
      wallets: {
        create: {
          address: '0x1234567890abcdef1234567890abcdef12345678',
          balance: 1000.0,
          currency: 'USD',
          ledgerEntries: { create: { amount: 1000.0, type: LedgerType.CREDIT, reference: 'initial-funding' } },
        },
      },
      templates: {
        create: { name: 'Validator Task', description: 'Standard AVS task', config: { retries: 3 } },
      },
    },
    include: { wallets: true, templates: true },
  });

  const job = await prisma.job.create({
    data: { userId: user.id, templateId: user.templates[0].id, status: JobStatus.PENDING, payload: { targetBlock: 123456 } },
  });
  return { user, job };
}

if (process.argv[1]?.includes('seed')) {
  seed()
    .then(async () => { console.log('Database seeded successfully'); await prisma.$disconnect(); })
    .catch(async (e) => { console.error('Seed error:', e); await prisma.$disconnect(); process.exit(1); });
}
