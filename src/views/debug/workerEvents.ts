// DebugView 的 worker 事件分组：按 taskId 聚合，单事件保持独立。
import type { DebugWorkerEvent, DebugWorkerEventGroup } from '../debug_types'

export function isNewerTimestamp(left: unknown, right: unknown) {
  const leftTime = Date.parse(String(left || ''))
  const rightTime = Date.parse(String(right || ''))
  if (!Number.isFinite(leftTime)) return false
  if (!Number.isFinite(rightTime)) return true
  return leftTime > rightTime
}

export function groupWorkerEvents(events: DebugWorkerEvent[]): DebugWorkerEventGroup[] {
  const groups: DebugWorkerEventGroup[] = []
  const taskGroups = new Map<string, DebugWorkerEventGroup>()

  for (const event of events) {
    const type = String(event?.type || 'unknown')
    const taskId = String(event?.taskId || '').trim()
    const timestamp = event?.timestamp
    if (!taskId) {
      groups.push({
        key: `event:${groups.length}:${timestamp || ''}:${type}`,
        taskId: null,
        latestType: type,
        firstTimestamp: timestamp,
        lastTimestamp: timestamp,
        eventCount: 1,
        latestPayload: event?.payload,
        typeCounts: { [type]: 1 },
        events: [event],
      })
      continue
    }

    let group = taskGroups.get(taskId)
    if (!group) {
      group = {
        key: `task:${taskId}`,
        taskId,
        latestType: type,
        firstTimestamp: timestamp,
        lastTimestamp: timestamp,
        eventCount: 0,
        latestPayload: event?.payload,
        typeCounts: {},
        events: [],
      }
      taskGroups.set(taskId, group)
      groups.push(group)
    }

    group.eventCount += 1
    group.typeCounts[type] = (group.typeCounts[type] || 0) + 1
    group.events.push(event)
    if (group.events.length === 1 || isNewerTimestamp(timestamp, group.lastTimestamp)) {
      group.latestType = type
      group.lastTimestamp = timestamp
      group.latestPayload = event?.payload
    }
    if (!group.firstTimestamp || isNewerTimestamp(group.firstTimestamp, timestamp)) {
      group.firstTimestamp = timestamp
    }
  }

  return groups
}
