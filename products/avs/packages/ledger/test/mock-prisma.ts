import {
  CreditHold,
  LedgerTransaction,
  PrismaClientLike,
  PrismaTransactionClient,
  Wallet,
} from '../src/types';

export class MockPrismaClient implements PrismaClientLike {
  private wallets = new Map<string, Wallet>();
  private holds = new Map<string, CreditHold>();
  private holdsByRef = new Map<string, CreditHold>();
  private transactions = new Map<string, LedgerTransaction>();
  private txLock: Promise<void> = Promise.resolve();
  private autoId = 1;

  async $transaction<T>(fn: (tx: PrismaTransactionClient) => Promise<T>): Promise<T> {
    let releaseLock!: () => void;
    const nextLock = new Promise<void>((resolve) => {
      releaseLock = resolve;
    });
    const currentLock = this.txLock;
    this.txLock = nextLock;

    await currentLock;

    const walletSnap = new Map(Array.from(this.wallets.entries()).map(([k, v]) => [k, { ...v }]));
    const holdSnap = new Map(Array.from(this.holds.entries()).map(([k, v]) => [k, { ...v }]));
    const holdByRefSnap = new Map(Array.from(this.holdsByRef.entries()).map(([k, v]) => [k, { ...v }]));
    const txSnap = new Map(Array.from(this.transactions.entries()).map(([k, v]) => [k, { ...v }]));

    try {
      const txClient: PrismaTransactionClient = {
        wallet: {
          findUnique: async ({ where }) => {
            const w = this.wallets.get(where.userId);
            return w ? { ...w } : null;
          },
          upsert: async ({ where, create, update }) => {
            const existing = this.wallets.get(where.userId);
            const now = new Date();
            if (existing) {
              const inc = 'increment' in update.balance ? update.balance.increment : (typeof update.balance === 'number' ? update.balance : 0);
              const updated: Wallet = { ...existing, balance: existing.balance + inc, updatedAt: now };
              this.wallets.set(where.userId, updated);
              return { ...updated };
            }
            const created: Wallet = {
              id: `w-${this.autoId++}`,
              userId: create.userId,
              balance: create.balance,
              createdAt: now,
              updatedAt: now,
            };
            this.wallets.set(where.userId, created);
            return { ...created };
          },
          update: async ({ where, data }) => {
            const existing = this.wallets.get(where.userId);
            if (!existing) throw new Error(`Wallet not found: ${where.userId}`);
            let newBal = existing.balance;
            if ('increment' in data.balance) newBal += data.balance.increment;
            else if ('decrement' in data.balance) newBal -= data.balance.decrement;
            else if (typeof data.balance === 'number') newBal = data.balance;
            const updated: Wallet = { ...existing, balance: newBal, updatedAt: new Date() };
            this.wallets.set(where.userId, updated);
            return { ...updated };
          },
        },
        creditHold: {
          findUnique: async ({ where }) => {
            if (where.id) return (this.holds.get(where.id) && { ...this.holds.get(where.id)! }) || null;
            if (where.refId) return (this.holdsByRef.get(where.refId) && { ...this.holdsByRef.get(where.refId)! }) || null;
            return null;
          },
          findFirst: async ({ where }) => {
            if (where.id) return (this.holds.get(where.id) && { ...this.holds.get(where.id)! }) || null;
            if (where.refId) return (this.holdsByRef.get(where.refId) && { ...this.holdsByRef.get(where.refId)! }) || null;
            return null;
          },
          create: async ({ data }) => {
            const now = new Date();
            const hold: CreditHold = {
              id: `h-${this.autoId++}`,
              userId: data.userId,
              amount: data.amount,
              refId: data.refId,
              status: data.status,
              createdAt: now,
              updatedAt: now,
            };
            this.holds.set(hold.id, hold);
            this.holdsByRef.set(hold.refId, hold);
            return { ...hold };
          },
          update: async ({ where, data }) => {
            const existing = this.holds.get(where.id);
            if (!existing) throw new Error(`Hold not found: ${where.id}`);
            const updated: CreditHold = { ...existing, status: data.status, updatedAt: new Date() };
            this.holds.set(where.id, updated);
            this.holdsByRef.set(updated.refId, updated);
            return { ...updated };
          },
        },
        ledgerTransaction: {
          findUnique: async ({ where }) => {
            if (where.refId) return (this.transactions.get(where.refId) && { ...this.transactions.get(where.refId)! }) || null;
            return null;
          },
          findFirst: async ({ where }) => {
            if (where.refId) return (this.transactions.get(where.refId) && { ...this.transactions.get(where.refId)! }) || null;
            return null;
          },
          create: async ({ data }) => {
            const tx: LedgerTransaction = {
              id: `tx-${this.autoId++}`,
              userId: data.userId,
              amount: data.amount,
              type: data.type,
              refId: data.refId,
              createdAt: new Date(),
            };
            this.transactions.set(tx.refId, tx);
            return { ...tx };
          },
        },
      };

      return await fn(txClient);
    } catch (err) {
      this.wallets = walletSnap;
      this.holds = holdSnap;
      this.holdsByRef = holdByRefSnap;
      this.transactions = txSnap;
      throw err;
    } finally {
      releaseLock();
    }
  }

  getWallet(userId: string): Wallet | undefined {
    const w = this.wallets.get(userId);
    return w ? { ...w } : undefined;
  }
}
