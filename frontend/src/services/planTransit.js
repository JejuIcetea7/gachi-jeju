const TRANSIT_MODES = new Set([
  'transit', 'bus', '버스', '대중교통', '대중교통(버스·도보)',
])

function hasRouteId(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 80
}

function optionalText(value) {
  return value === null || value === undefined || typeof value === 'string'
}

function optionalInteger(value, minimum = 0) {
  return value === null || value === undefined
    || (Number.isSafeInteger(value) && value >= minimum)
}

function isSegment(value) {
  return ['BUS', 'WALKING', 'UNKNOWN'].includes(value?.type)
    && optionalInteger(value.distance_m)
    && optionalInteger(value.duration_seconds)
    && Array.isArray(value.route_numbers)
    && value.route_numbers.every((number) => (
      typeof number === 'string' && number.trim().length > 0
    ))
    && optionalText(value.boarding_stop)
    && optionalText(value.alighting_stop)
    && optionalInteger(value.ride_stop_count, 1)
}

function isGuidance(value) {
  return hasRouteId(value?.route_id)
    && Array.isArray(value.segments)
    && value.segments.length > 0
    && value.segments.every(isSegment)
    && typeof value.requested_at === 'string'
    && /(Z|[+-][0-9]{2}:[0-9]{2})$/i.test(value.requested_at)
    && Number.isFinite(Date.parse(value.requested_at))
}

function countIds(values) {
  const counts = new Map()
  for (const value of values) {
    if (!hasRouteId(value?.route_id)) continue
    counts.set(value.route_id, (counts.get(value.route_id) ?? 0) + 1)
  }
  return counts
}

export function isTransitMove(item) {
  const mode = typeof item?.transport === 'string'
    ? item.transport.replace(/\s+/g, '').toLowerCase()
    : ''
  return item?.item_type === 'move' && TRANSIT_MODES.has(mode)
}

export function getPlanTransitGuidance(plan, transitRoutes = []) {
  const days = Array.isArray(plan?.days) ? plan.days : []
  const moves = days.flatMap((day) => (
    Array.isArray(day?.items)
      ? day.items.filter((item) => item?.item_type === 'move')
      : []
  ))
  const records = Array.isArray(transitRoutes) ? transitRoutes : []
  const references = countIds(moves)
  const recordCounts = countIds(records)
  const byId = new Map(
    records.filter(isGuidance).map((record) => [record.route_id, record]),
  )
  const selected = new Map()

  for (const move of moves) {
    const id = move.route_id
    if (
      !isTransitMove(move)
      || references.get(id) !== 1
      || recordCounts.get(id) !== 1
      || !byId.has(id)
    ) continue

    const record = byId.get(id)
    selected.set(id, {
      route_id: id,
      requested_at: record.requested_at,
      segments: record.segments.map((segment) => ({
        type: segment.type,
        distance_m: segment.distance_m ?? null,
        duration_seconds: segment.duration_seconds ?? null,
        route_numbers: [...new Set(segment.route_numbers)],
        boarding_stop: segment.boarding_stop ?? null,
        alighting_stop: segment.alighting_stop ?? null,
        ride_stop_count: segment.ride_stop_count ?? null,
      })),
    })
  }

  return selected
}
