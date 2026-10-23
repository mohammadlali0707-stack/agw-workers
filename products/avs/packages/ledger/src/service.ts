import {
  CreditHold,
  ICreditLedgerService,
  LedgerTransaction,
  PrismaClientLike,
} from './types';
import {
  DuplicateRefError,
  HoldAlreadyResolvedError,
  InsufficientBalanceError,
} from './errors';

export class CreditLedgerService implements ICreditLedgerService {
  constructor(private readonly prisma: PrismaClientLike) {}

  async topup(userId: string, amount: number, refId: string): Promise<LedgerTransaction> {
    if (amount <= 0) {
      throw new Error('Topup amount must be positive');
    }
    return this.prisma.$transaction(async (tx) => {
      const existing = await tx.ledgerTransaction.findUnique({ where: { refId } });
      if (existing) {
        if (existing.userId === userId && existing.amount === amount) {
          return existing;
        }
        throw new DuplicateRefError(`Transaction with refId "${refId}" already exists`);
      }

      await tx.wallet.upsert({
        where: { userId },
        create: { userId, balance: amount },
        update: { balance: { increment: amount } },
      });

      return tx.ledgerTransaction.create({
        data: { userId, amount, type: 'TOPUP', refId },
      });
    });
  }

  async hold(userId: string, amount: number, refId: string): Promise<CreditHold> {
    if (amount <= 0) {
      throw new Error('Hold amount must be positive');
    }
    return this.prisma.$transaction(async (tx) => {
      const existingHold = await tx.creditHold.findUnique({ where: { refId } });
      if (existingHold) {
        throw new DuplicateRefError(`Hold with refId "${refId}" already exists`);
      }

      const wallet = await tx.wallet.findUnique({ where: { userId } });
      if (!wallet || wallet.balance < amount) {
        throw new InsufficientBalanceError(`User "${userId}" has insufficient balance`);
      }

      await tx.wallet.update({
        where: { userId },
        data: { balance: { decrement: amount } },
      });

      return tx.creditHold.create({
        data: { userId, amount, refId, status: 'PENDING' },
      });
    });
  }

  async capture(holdId: string): Promise<CreditHold> {
    return this.prisma.$transaction(async (tx) => {
      const hold = await tx.creditHold.findUnique({ where: { id: holdId } });
      if (!hold) {
        throw new Error(`Hold not found: ${holdId}`);
      }
      if (hold.status !== 'PENDING') {
        throw new HoldAlreadyResolvedError(`Hold "${holdId}" is already resolved with status ${hold.status}`);
      }

      return tx.creditHold.update({
        where: { id: holdId },
        data: { status: 'CAPTURED' },
      });
    });
  }

  async release(holdId: string): Promise<CreditHold> {
    return this.prisma.$transaction(async (tx) => {
      const hold = await tx.creditHold.findUnique({ where: { id: holdId } });
      if (!hold) {
        throw new Error(`Hold not found: ${holdId}`);
      }
      if (hold.status !== 'PENDING') {
        throw new HoldAlreadyResolvedError(`Hold "${holdId}" is already resolved with status ${hold.status}`);
      }

      const updated = await tx.creditHold.update({
        where: { id: holdId },
        data: { status: 'RELEASED' },
      });

      await tx.wallet.update({
        where: { userId: hold.userId },
        data: { balance: { increment: hold.amount } },
      });

      return updated;
    });
  }
}
