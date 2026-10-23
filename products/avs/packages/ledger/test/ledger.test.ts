import { describe, it, expect, beforeEach } from 'vitest';
import { CreditLedgerService } from '../src/service';
import {
  InsufficientBalanceError,
  HoldAlreadyResolvedError,
  DuplicateRefError,
} from '../src/errors';
import { MockPrismaClient } from './mock-prisma';

describe('CreditLedgerService Unit & Concurrency Suite', () => {
  let mockPrisma: MockPrismaClient;
  let service: CreditLedgerService;
  const user = 'user-test-1';

  beforeEach(() => {
    mockPrisma = new MockPrismaClient();
    service = new CreditLedgerService(mockPrisma);
  });

  describe('Idempotent Topup', () => {
    it('should top up wallet and increment balance', async () => {
      const tx = await service.topup(user, 100, 'topup-ref-1');
      expect(tx.amount).toBe(100);
      expect(tx.type).toBe('TOPUP');
      expect(mockPrisma.getWallet(user)?.balance).toBe(100);
    });

    it('should be idempotent on identical duplicate refId', async () => {
      const first = await service.topup(user, 100, 'topup-ref-idem');
      const second = await service.topup(user, 100, 'topup-ref-idem');
      expect(second.id).toBe(first.id);
      expect(mockPrisma.getWallet(user)?.balance).toBe(100);
    });

    it('should reject duplicate refId with mismatched amount or user', async () => {
      await service.topup(user, 100, 'topup-ref-dup');
      await expect(service.topup(user, 200, 'topup-ref-dup')).rejects.toThrow(DuplicateRefError);
      await expect(service.topup('user-other', 100, 'topup-ref-dup')).rejects.toThrow(DuplicateRefError);
    });

    it('should reject non-positive topup amounts', async () => {
      await expect(service.topup(user, 0, 'topup-zero')).rejects.toThrow('positive');
      await expect(service.topup(user, -50, 'topup-neg')).rejects.toThrow('positive');
    });
  });

  describe('Non-Negative Wallet Balance Enforcement', () => {
    it('should deduct balance when placing a valid hold', async () => {
      await service.topup(user, 100, 'ref-topup-init');
      const hold = await service.hold(user, 40, 'hold-ref-1');
      expect(hold.amount).toBe(40);
      expect(hold.status).toBe('PENDING');
      expect(mockPrisma.getWallet(user)?.balance).toBe(60);
    });

    it('should reject hold exceeding available balance and keep balance non-negative', async () => {
      await service.topup(user, 50, 'ref-topup-50');
      await expect(service.hold(user, 60, 'hold-too-much')).rejects.toThrow(InsufficientBalanceError);
      expect(mockPrisma.getWallet(user)?.balance).toBe(50);
    });

    it('should reject hold when wallet does not exist', async () => {
      await expect(service.hold('unknown-user', 10, 'hold-unknown')).rejects.toThrow(InsufficientBalanceError);
    });

    it('should reject non-positive hold amounts', async () => {
      await service.topup(user, 100, 'ref-init');
      await expect(service.hold(user, 0, 'hold-zero')).rejects.toThrow('positive');
      await expect(service.hold(user, -10, 'hold-neg')).rejects.toThrow('positive');
    });

    it('should reject duplicate hold refId', async () => {
      await service.topup(user, 100, 'ref-init-dup');
      await service.hold(user, 10, 'hold-same-ref');
      await expect(service.hold(user, 10, 'hold-same-ref')).rejects.toThrow(DuplicateRefError);
    });
  });

  describe('Strict Hold State Transitions', () => {
    it('should capture a pending hold and reject repeated captures or releases', async () => {
      await service.topup(user, 100, 'ref-t');
      const hold = await service.hold(user, 30, 'hold-cap');

      const captured = await service.capture(hold.id);
      expect(captured.status).toBe('CAPTURED');

      await expect(service.capture(hold.id)).rejects.toThrow(HoldAlreadyResolvedError);
      await expect(service.release(hold.id)).rejects.toThrow(HoldAlreadyResolvedError);
      expect(mockPrisma.getWallet(user)?.balance).toBe(70);
    });

    it('should release a pending hold, restore balance, and reject repeated releases or captures', async () => {
      await service.topup(user, 100, 'ref-r');
      const hold = await service.hold(user, 30, 'hold-rel');
      expect(mockPrisma.getWallet(user)?.balance).toBe(70);

      const released = await service.release(hold.id);
      expect(released.status).toBe('RELEASED');
      expect(mockPrisma.getWallet(user)?.balance).toBe(100);

      await expect(service.release(hold.id)).rejects.toThrow(HoldAlreadyResolvedError);
      await expect(service.capture(hold.id)).rejects.toThrow(HoldAlreadyResolvedError);
    });

    it('should reject capture or release on non-existent hold id', async () => {
      await expect(service.capture('invalid-id')).rejects.toThrow('Hold not found');
      await expect(service.release('invalid-id')).rejects.toThrow('Hold not found');
    });
  });

  describe('High-Concurrency Stress Testing', () => {
    it('should execute 100 concurrent holds atomically against a single wallet', async () => {
      const concurrencyUser = 'user-concurrent-100';
      await service.topup(concurrencyUser, 1000, 'ref-concurrency-init');

      const holdPromises = Array.from({ length: 100 }, (_, i) =>
        service.hold(concurrencyUser, 10, `concurrent-hold-${i}`)
      );

      const holds = await Promise.all(holdPromises);

      expect(holds).toHaveLength(100);
      expect(new Set(holds.map((h) => h.id)).size).toBe(100);
      expect(holds.every((h) => h.status === 'PENDING')).toBe(true);
      expect(mockPrisma.getWallet(concurrencyUser)?.balance).toBe(0);
    });

    it('should enforce race-condition safety with 100 concurrent holds competing for limited funds', async () => {
      const raceUser = 'user-race-limited';
      // 400 credits total: exactly 40 holds of 10 credits can succeed
      await service.topup(raceUser, 400, 'ref-race-init');

      const requests = Array.from({ length: 100 }, (_, i) =>
        service.hold(raceUser, 10, `race-hold-${i}`)
      );

      const results = await Promise.allSettled(requests);
      const fulfilled = results.filter((r) => r.status === 'fulfilled');
      const rejected = results.filter((r) => r.status === 'rejected');

      expect(fulfilled).toHaveLength(40);
      expect(rejected).toHaveLength(60);
      rejected.forEach((r) => {
        const reason = (r as PromiseRejectedResult).reason;
        expect(reason).toBeInstanceOf(InsufficientBalanceError);
      });

      expect(mockPrisma.getWallet(raceUser)?.balance).toBe(0);
    });
  });
});
