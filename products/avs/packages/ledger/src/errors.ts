export class InsufficientBalanceError extends Error {
  constructor(message = 'Insufficient credit balance for this operation') {
    super(message);
    this.name = 'InsufficientBalanceError';
    Object.setPrototypeOf(this, InsufficientBalanceError.prototype);
  }
}

export class HoldAlreadyResolvedError extends Error {
  constructor(message = 'Credit hold is already resolved') {
    super(message);
    this.name = 'HoldAlreadyResolvedError';
    Object.setPrototypeOf(this, HoldAlreadyResolvedError.prototype);
  }
}

export class DuplicateRefError extends Error {
  constructor(message = 'Duplicate reference identifier') {
    super(message);
    this.name = 'DuplicateRefError';
    Object.setPrototypeOf(this, DuplicateRefError.prototype);
  }
}
