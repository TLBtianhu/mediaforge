/**
 * Helpers for deriving frontend types from `workerContracts.generated.ts`.
 *
 * Generated result objects keep an index signature when the protocol allows
 * extra fields. `KnownObject` strips that index while preserving the fields the
 * protocol actually declares, so a view can use the generated shape without
 * every unknown key becoming `{}`.
 */
export type KnownKeys<T> = keyof {
  [K in keyof T as string extends K ? never : number extends K ? never : K]: T[K]
}

export type KnownObject<T> = Pick<T, KnownKeys<T>>
