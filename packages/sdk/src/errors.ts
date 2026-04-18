/**
 * Error taxonomy for the tapps-brain TypeScript SDK.
 *
 * Mirrors the Python-side `tapps_brain.errors` module so callers can
 * discriminate failure modes without string-matching HTTP status codes.
 */

/**
 * Base error class for all tapps-brain SDK errors.
 */
export class TappsBrainError extends Error {
  /** HTTP status code that produced this error (if applicable). */
  readonly statusCode?: number;
  /** Server-side error code string (e.g. `"RATE_LIMITED"`). */
  readonly errorCode?: string;

  constructor(
    message: string,
    options?: { statusCode?: number; errorCode?: string; cause?: unknown },
  ) {
    super(message, { cause: options?.cause });
    this.name = "TappsBrainError";
    this.statusCode = options?.statusCode;
    this.errorCode = options?.errorCode;
  }
}

/**
 * Authentication failed — check `authToken` / `TAPPS_BRAIN_AUTH_TOKEN`.
 */
export class AuthError extends TappsBrainError {
  constructor(message = "Authentication failed", options?: { cause?: unknown }) {
    super(message, { statusCode: 401, errorCode: "UNAUTHORIZED", ...options });
    this.name = "AuthError";
  }
}

/**
 * The requested project is not registered on this brain instance.
 */
export class ProjectNotFoundError extends TappsBrainError {
  readonly projectId: string;

  constructor(projectId: string, message?: string) {
    super(message ?? `Project not registered: ${projectId}`, {
      statusCode: 403,
      errorCode: "PROJECT_NOT_REGISTERED",
    });
    this.name = "ProjectNotFoundError";
    this.projectId = projectId;
  }
}

/**
 * The requested memory key was not found.
 */
export class NotFoundError extends TappsBrainError {
  constructor(message = "Not found") {
    super(message, { statusCode: 404, errorCode: "NOT_FOUND" });
    this.name = "NotFoundError";
  }
}

/**
 * Idempotency key conflict — a different request was previously submitted
 * with the same key.
 */
export class IdempotencyConflictError extends TappsBrainError {
  constructor(message = "Idempotency key conflict") {
    super(message, { statusCode: 409, errorCode: "IDEMPOTENCY_CONFLICT" });
    this.name = "IdempotencyConflictError";
  }
}

/**
 * Request was rate-limited.  Retry with back-off.
 */
export class RateLimitError extends TappsBrainError {
  /** Recommended delay before retry (seconds). */
  readonly retryAfter?: number;

  constructor(message = "Rate limited", retryAfter?: number) {
    super(message, { statusCode: 429, errorCode: "RATE_LIMITED" });
    this.name = "RateLimitError";
    this.retryAfter = retryAfter;
  }
}

/**
 * Invalid request — check parameters.
 */
export class InvalidRequestError extends TappsBrainError {
  constructor(message = "Invalid request") {
    super(message, { statusCode: 400, errorCode: "INVALID_REQUEST" });
    this.name = "InvalidRequestError";
  }
}

/**
 * Brain is temporarily degraded — retry shortly.
 */
export class BrainDegradedError extends TappsBrainError {
  constructor(message = "Brain degraded") {
    super(message, { statusCode: 503, errorCode: "BRAIN_DEGRADED" });
    this.name = "BrainDegradedError";
  }
}

/**
 * Unexpected server-side error.
 */
export class InternalError extends TappsBrainError {
  constructor(message = "Internal error") {
    super(message, { statusCode: 500, errorCode: "INTERNAL_ERROR" });
    this.name = "InternalError";
  }
}

// ---------------------------------------------------------------------------
// Error factory — map HTTP response bodies to typed errors
// ---------------------------------------------------------------------------

const ERROR_CODE_MAP: Record<string, new (...args: never[]) => TappsBrainError> = {
  UNAUTHORIZED: AuthError,
  PROJECT_NOT_REGISTERED: ProjectNotFoundError,
  NOT_FOUND: NotFoundError,
  IDEMPOTENCY_CONFLICT: IdempotencyConflictError,
  RATE_LIMITED: RateLimitError,
  INVALID_REQUEST: InvalidRequestError,
  BRAIN_DEGRADED: BrainDegradedError,
  INTERNAL_ERROR: InternalError,
};

/** Parse a structured HTTP error response body into a typed SDK error. */
export function parseErrorResponse(
  statusCode: number,
  body: Record<string, unknown>,
): TappsBrainError {
  const errorCode = typeof body.error === "string" ? body.error : "";
  const message =
    typeof body.message === "string" ? body.message : `HTTP ${statusCode}`;

  if (errorCode === "PROJECT_NOT_REGISTERED") {
    const projectId =
      typeof body.project_id === "string" ? body.project_id : "unknown";
    return new ProjectNotFoundError(projectId, message);
  }

  if (errorCode === "RATE_LIMITED") {
    const retryAfter =
      typeof body.retry_after === "number" ? body.retry_after : undefined;
    return new RateLimitError(message, retryAfter);
  }

  const Cls = ERROR_CODE_MAP[errorCode];
  if (Cls) {
    return new (Cls as new (msg: string) => TappsBrainError)(message);
  }

  // Fallback by status code
  if (statusCode === 401 || statusCode === 403) return new AuthError(message);
  if (statusCode === 404) return new NotFoundError(message);
  if (statusCode === 429) return new RateLimitError(message);
  if (statusCode === 503) return new BrainDegradedError(message);
  if (statusCode >= 500) return new InternalError(message);
  if (statusCode >= 400) return new InvalidRequestError(message);
  return new TappsBrainError(message, { statusCode });
}
