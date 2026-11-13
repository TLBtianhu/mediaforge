import { invoke } from '@tauri-apps/api/core'
import type {
  WorkerCommandName,
  WorkerCommandPayload,
  WorkerCommandResult,
} from '@/types/workerContracts.generated'

/**
 * Invoke a Tauri command whose payload/result shapes are the worker protocol
 * contract. Only use this for commands that take `{ payload }` and return the
 * worker result unchanged; native commands keep their own wrappers.
 */
export function invokeWorkerCommand<C extends WorkerCommandName>(
  command: C,
  payload: WorkerCommandPayload<C>,
): Promise<WorkerCommandResult<C>> {
  return invoke<WorkerCommandResult<C>>(command, { payload })
}

/** Same as above for commands that take no payload at all. */
export function invokeWorkerQuery<C extends WorkerCommandName>(
  command: C,
): Promise<WorkerCommandResult<C>> {
  return invoke<WorkerCommandResult<C>>(command)
}
