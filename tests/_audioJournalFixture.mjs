// Simulated backend records for browser-only store tests. No audio-history adapter.
export const JOB_KEY = 'mediaforge:job:'

export function recordsFromEnqueue(payload, createdAt = Date.now() / 1000) {
  return payload.tasks.map((task, index) => ({
    schemaVersion: 2, jobId: task.jobId, command: payload.command, source: 'manual',
    cancelTaskId: payload.primaryJobId, status: 'queued', fraction: 0, message: 'queued', error: null, result: null,
    createdAt, updatedAt: createdAt, startedAt: null, finishedAt: null, owner: null,
    runtime: { stage: null, progress: null, nodes: {}, order: [] }, logs: [],
    audio: { groupId: payload.groupId, primaryJobId: payload.primaryJobId, index, request: structuredClone(task.request),
      ...(index === 0 ? { memberJobIds: payload.tasks.map(item => item.jobId) } : {}) },
    ...(index === 0 ? { payload: structuredClone(payload.payload), ...(payload.presentation ? { presentation: structuredClone(payload.presentation) } : {}) }
      : { payloadRef: payload.primaryJobId }),
  }))
}

export function putJobs(values, records) {
  records.forEach(record => values.set(JOB_KEY + record.jobId, JSON.stringify(record)))
}

export function readJobs(values) {
  return [...values].filter(([key]) => key.startsWith(JOB_KEY)).map(([, raw]) => JSON.parse(raw))
}

export function storageShim(values, onWrite = () => {}) {
  return {
    get length() { return values.size },
    key: index => [...values.keys()][index] ?? null,
    getItem: key => values.get(key) ?? null,
    setItem(key, value) { onWrite(key, value); values.set(key, value) },
    removeItem: key => values.delete(key),
  }
}
