function hasRouteId(value) {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 80
}

function hasCoordinates(value) {
  return Number.isFinite(value?.latitude)
    && Math.abs(value.latitude) <= 90
    && Number.isFinite(value?.longitude)
    && Math.abs(value.longitude) <= 180
}

function isPath(path) {
  return Array.isArray(path)
    && path.length >= 2
    && path.every((point) => (
      Array.isArray(point)
      && point.length === 2
      && Number.isFinite(point[0])
      && Math.abs(point[0]) <= 180
      && Number.isFinite(point[1])
      && Math.abs(point[1]) <= 90
    ))
}

function isUsableRoute(route) {
  return hasRouteId(route?.route_id)
    && ['driving', 'walking', 'transit'].includes(route.transport)
    && hasCoordinates(route.origin)
    && hasCoordinates(route.destination)
    && Number.isSafeInteger(route.distance_m)
    && route.distance_m >= 0
    && Number.isSafeInteger(route.duration_seconds)
    && route.duration_seconds >= 0
    && ['ok', 'partial'].includes(route.geometry_status)
    && Array.isArray(route.paths)
    && route.paths.length > 0
    && route.paths.every(isPath)
    && typeof route.requested_at === 'string'
    && /(Z|[+-][0-9]{2}:[0-9]{2})$/i.test(route.requested_at)
    && Number.isFinite(Date.parse(route.requested_at))
}

function countRouteIds(items) {
  const counts = new Map()

  for (const item of items) {
    const id = item?.route_id
    if (!hasRouteId(id)) continue

    counts.set(id, (counts.get(id) ?? 0) + 1)
  }

  return counts
}

export function getDayMapRoutes(day, mapRoutes = []) {
  const moves = Array.isArray(day?.items)
    ? day.items.filter((item) => item?.item_type === 'move')
    : []
  const records = Array.isArray(mapRoutes) ? mapRoutes : []
  const referenceCounts = countRouteIds(moves)
  const recordCounts = countRouteIds(records)
  const byId = new Map(
    records
      .filter((route) => hasRouteId(route?.route_id))
      .map((route) => [route.route_id, route]),
  )
  const routes = []

  for (const move of moves) {
    const id = move.route_id

    if (referenceCounts.get(id) !== 1 || recordCounts.get(id) !== 1) {
      continue
    }

    const route = byId.get(id)
    if (isUsableRoute(route)) routes.push(route)
  }

  return {
    routes,
    missingCount: moves.length - routes.length,
  }
}