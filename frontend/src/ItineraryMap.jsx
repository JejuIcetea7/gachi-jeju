import { getLodgingMapAnchors } from './services/lodgingPlanning.js'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { loadKakaoMaps } from './services/kakaoMaps.js'
import { getDayMapRoutes } from './services/itineraryRoutes.js'

const ROUTE_STYLES = {
  driving: {
    label: '자동차: 파란 실선',
    color: '#2458ad',
    line: 'solid',
  },
  walking: {
    label: '도보: 초록 점선',
    color: '#18735d',
    line: 'shortdash',
  },
  transit: {
    label: '대중교통: 주황 실선',
    color: '#a64f14',
    line: 'solid',
  },
}

function getQueryTimeLabel(routes) {
  if (routes.length === 0) return ''

  const times = routes.map((route) => Date.parse(route.requested_at))
  const formatter = new Intl.DateTimeFormat('ko-KR', {
    dateStyle: 'short',
    timeStyle: 'short',
    timeZone: 'Asia/Seoul',
  })
  const first = formatter.format(Math.min(...times))
  const last = formatter.format(Math.max(...times))

  return first === last ? first : `${first} ~ ${last}`
}

function hasCoordinates(value) {
  return Number.isFinite(value?.latitude)
    && Math.abs(value.latitude) <= 90
    && Number.isFinite(value?.longitude)
    && Math.abs(value.longitude) <= 180
}

function getMapPlaces(day, options) {
  const points = []
  const missing = []
  const anchors = getLodgingMapAnchors(day, options)
  points.push(...anchors.filter((point) => point.position === 'start'))
  if ((!day || day.day === 1 || options?.lodging_status === 'not_needed')
    && !points.some((point) => point.role === 'origin') && options?.departure) {
    missing.push(`출발지: ${options.departure}`)
  }

  let visitNumber = 0

  for (const [index, item] of (day?.items ?? []).entries()) {
    if (item.item_type !== 'visit') continue
    visitNumber += 1

    if (item.verified !== true || !hasCoordinates(item)) {
      missing.push(`${visitNumber}. ${item.title}`)
      continue
    }

    points.push({
      id: `visit-${index}`,
      label: String(visitNumber),
      title: item.title,
      latitude: item.latitude,
      longitude: item.longitude,
    })
  }

  points.push(...anchors.filter((point) => point.position === 'end'))
  return { points, missing }
}

function fitMapToPlaces(maps, map, points) {
  if (!map || points.length === 0) return

  const positions = new Map(points.map((point) => [
    `${point.latitude},${point.longitude}`,
    new maps.LatLng(point.latitude, point.longitude),
  ]))

  if (positions.size === 1) {
    map.setCenter(positions.values().next().value)
    map.setLevel(3)
    return
  }

  const bounds = new maps.LatLngBounds()

  for (const position of positions.values()) {
    bounds.extend(position)
  }

  map.setBounds(bounds, 48, 48, 48, 48)
}

function ItineraryMap({ day = null, options, mapRoutes }) {
  const headingId = useId()
  const containerRef = useRef(null)
  const contextRef = useRef(null)
  const [retryCount, setRetryCount] = useState(0)
  const [mapState, setMapState] = useState(null)

  const { points, missing } = useMemo(
    () => getMapPlaces(day, options),
    [day, options],
  )

  const { routes, missingCount } = useMemo(
    () => getDayMapRoutes(day, mapRoutes),
    [day, mapRoutes],
  )

  const mapPoints = useMemo(() => [
    ...points,
    ...routes.flatMap((route) => route.paths.flatMap((path) => (
      path.map(([longitude, latitude]) => ({ longitude, latitude }))
    ))),
  ], [points, routes])

  const routeLegend = [...new Set(routes.map((route) => route.transport))]
    .map((mode) => ROUTE_STYLES[mode].label)
    .join(' · ')

  const queryTime = getQueryTimeLabel(routes)
  const hasPartialRoute = routes.some(
    (route) => route.geometry_status === 'partial',
  )

  const requestKey = JSON.stringify([points, routes, retryCount])
  const status = mapPoints.length === 0
    ? 'empty'
    : mapState?.key === requestKey ? mapState.status : 'loading'

  useEffect(() => {
    const container = containerRef.current
    if (!container || mapPoints.length === 0) return

    let active = true
    let map = null
    let observer = null
    const overlays = []
    const polylines = []

    function releaseMap() {
      observer?.disconnect()
      polylines.forEach((line) => line.setMap(null))
      overlays.forEach((overlay) => overlay.setMap(null))
      map?.setKeyboardShortcuts(false)

      if (contextRef.current?.map === map) {
        contextRef.current = null
      }

      container.replaceChildren()
    }

    async function initialize() {
      try {
        const maps = await loadKakaoMaps()
        if (!active) return

        map = new maps.Map(container, {
          center: new maps.LatLng(
            mapPoints[0].latitude,
            mapPoints[0].longitude,
          ),
          level: 5,
          scrollwheel: true,
          keyboardShortcuts: true,
        })

        map.addControl(
          new maps.ZoomControl(),
          maps.ControlPosition.TOPRIGHT,
        )

        contextRef.current = { maps, map }

        for (const route of routes) {
          const style = ROUTE_STYLES[route.transport]

          for (const path of route.paths) {
            polylines.push(new maps.Polyline({
              map,
              path: path.map(([longitude, latitude]) => (
                new maps.LatLng(latitude, longitude)
              )),
              strokeWeight: 5,
              strokeColor: style.color,
              strokeOpacity: 0.9,
              strokeStyle: style.line,
              zIndex: 1,
            }))
          }
        }

        const groups = new Map()

        for (const point of points) {
          const key = `${point.latitude},${point.longitude}`
          const group = groups.get(key) ?? []

          group.push(point)
          groups.set(key, group)
        }

        for (const group of groups.values()) {
          const point = group[0]
          const label = document.createElement('span')

          label.className = group.some((item) => item.role === 'origin')
            ? 'itinerary-map-marker is-origin'
            : group.some((item) => item.role === 'lodging')
              ? 'itinerary-map-marker is-lodging'
              : 'itinerary-map-marker'

          label.textContent = group.map((item) => item.label).join('·')
          label.title = group.map((item) => item.title).join(' / ')

          overlays.push(new maps.CustomOverlay({
            map,
            position: new maps.LatLng(
              point.latitude,
              point.longitude,
            ),
            content: label,
            xAnchor: 0.5,
            yAnchor: 0.5,
            zIndex: 3,
          }))
        }

        fitMapToPlaces(maps, map, mapPoints)

        observer = new ResizeObserver(() => {
          if (!active) return

          const center = map.getCenter()
          map.relayout()
          map.setCenter(center)
        })

        observer.observe(container)
        setMapState({ key: requestKey, status: 'ready' })
      } catch (error) {
        if (!active) return

        releaseMap()
        setMapState({
          key: requestKey,
          status: 'error',
          message: error instanceof Error
            ? error.message
            : '지도를 불러오지 못했습니다.',
        })
      }
    }

    void initialize()

    return () => {
      active = false
      releaseMap()
    }
  }, [points, routes, mapPoints, requestKey])

  function handleShowAll() {
    const context = contextRef.current

    if (context) {
      fitMapToPlaces(context.maps, context.map, mapPoints)
    }
  }

  function handleShowPlace(point) {
    const context = contextRef.current
    if (!context) return

    context.map.setCenter(
      new context.maps.LatLng(
        point.latitude,
        point.longitude,
      ),
    )
    context.map.setLevel(3)
  }

  return (
    <section className="itinerary-map" aria-labelledby={headingId}>
      <div className="itinerary-map-heading">
        <h3 id={headingId}>여행 경로 지도</h3>

        <button
          type="button"
          className="secondary-button"
          disabled={status !== 'ready'}
          onClick={handleShowAll}
        >
          {routes.length > 0 ? '전체 경로 보기' : '전체 위치 보기'}
        </button>
      </div>

      <p className="itinerary-map-description">
        {day ? `${day.day}일차의 ` : ''}출발·숙소·방문 장소와 이동 경로입니다.
      </p>

      {status === 'loading' && (
        <p className="map-status" role="status">
          지도를 불러오고 있습니다.
        </p>
      )}

      {status === 'empty' && (
        <p className="map-status" role="status">
          지도에 표시할 장소나 경로의 좌표가 아직 없습니다.
        </p>
      )}

      {status === 'error' && (
        <div className="itinerary-map-error" role="alert">
          <p>{mapState.message}</p>

          <button
            type="button"
            className="secondary-button"
            onClick={() => setRetryCount((previous) => previous + 1)}
          >
            지도 다시 불러오기
          </button>
        </div>
      )}

      <div
        ref={containerRef}
        className="itinerary-map-canvas"
        role="region"
        aria-label="여행 출발지, 숙소, 방문 장소와 이동 경로 지도"
        aria-busy={status === 'loading'}
        tabIndex={0}
      />

      {points.length > 0 && (
        <ul
          className="itinerary-map-places"
          aria-label="지도에 표시한 장소"
        >
          {points.map((point) => (
            <li key={point.id}>
              <button
                type="button"
                disabled={status !== 'ready'}
                onClick={() => handleShowPlace(point)}
              >
                <span className="itinerary-place-number">
                  {point.label}
                </span>
                <span>{point.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {routes.length > 0 && (
        <>
          <p className="itinerary-map-description">
            {routeLegend}
            <br />
            경로 조회 시각(한국): {queryTime}
          </p>
          <p className="itinerary-map-description">
            선은 조회 시점의 이동 경로입니다.
            휠체어 통행 가능 여부는 일정의 접근성 안내를 확인해 주세요.
          </p>
        </>
      )}

      {(missingCount > 0 || hasPartialRoute) && (
        <p className="itinerary-map-description">
          {missingCount > 0 && (
            <>이동 {missingCount}개 구간은 표시할 경로를 확인하지 못했습니다. </>
          )}
          {hasPartialRoute && '일부 구간은 확인된 경로만 표시합니다.'}
        </p>
      )}

      {missing.length > 0 && (
        <p className="itinerary-map-description">
          자료 또는 좌표 확인이 필요하여 지도에 표시하지 않은 장소:
          {' '}{missing.join(', ')}
        </p>
      )}
    </section>
  )
}

export default ItineraryMap