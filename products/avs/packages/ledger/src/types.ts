export type HoldStatus = 'PENDING' | 'CAPTURED' | 'RELEASED';
export type TransactionType = 'TOPUP' | 'HOLD' | 'CAPTURE' | 'RELEASE';

export interface Wallet {
  id: string;
  userId: string;
  balance: number;
  createdAt: Date;
  updatedAt: Date;
}

export interface CreditHold {
  id: string;
  userId: string;
  amount: number;
  refId: string;
  status: HoldStatus;
  createdAt: Date;
  updatedAt: Date;
}

export interface LedgerTransaction {
  id: string;
  userId: string;
  amount: number;
  type: TransactionType;
  refId: string;
  createdAt: Date;
}

export interface PrismaTransactionClient {
  wallet: {
    findUnique(args: { where: { userId: string } }): Promise<Wallet | null>;
    upsert(args: {
      where: { userId: string };
      create: { userId: string; balance: number };
      update: { balance: { increment: number } } | { balance: number };
    }): Promise<Wallet>;
    update(args: {
      where: { userId: string };
      data: { balance: { increment: number } } | { balance: { decrement: number } } | { balance: number };
    }): Promise<Wallet>;
  };
  creditHold: {
    findUnique(args: { where: { id?: string; refId?: string } }): Promise<CreditHold | null>;
    findFirst(args: { where: { refId?: string; id?: string } }): Promise<CreditHold | null>;
    create(args: { data: { userId: string; amount: number; refId: string; status: HoldStatus } }): Promise<CreditHold>;
    update(args: { where: { id: string }; data: { status: HoldStatus } }): Promise<CreditHold>;
  };
  ledgerTransaction: {
    findUnique(args: { where: { refId?: string; id?: string } }): Promise<LedgerTransaction | null>;
    findFirst(args: { where: { refId?: string; userId?: string } }): Promise<LedgerTransaction | null>;
    create(args: { data: { userId: string; amount: number; type: TransactionType; refId: string } }): Promise<LedgerTransaction>;
  };
}

export interface PrismaClientLike {
  $transaction<T>(fn: (tx: PrismaTransactionClient) => Promise<T>): Promise<T>;
}

export interface ICreditLedgerService {
  topup(userId: string, amount: number, refId: string): Promise<LedgerTransaction>;
  hold(userId: string, amount: number, refId: string): Promise<CreditHold>;
  capture(holdId: string): Promise<CreditHold>;
  release(holdId: string): Promise<CreditHold>;
}
