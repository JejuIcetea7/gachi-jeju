import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { loadKakaoMaps } from './services/kakaoMaps.js'

function hasCoordinates(value) {
  return Number.isFinite(value?.latitude)
    && Math.abs(value.latitude) <= 90
    && Number.isFinite(value?.longitude)
    && Math.abs(value.longitude) <= 180
}

function getMapPlaces(day, options) {
  const points = []
  const missing = []
  const origin = {
    latitude: options?.departure_latitude,
    longitude: options?.departure_longitude,
  }

  if (hasCoordinates(origin)) {
    points.push({
      id: 'origin',
      label: '출발',
      title: options.departure || '여행 출발지',
      ...origin,
    })
  } else if (options?.departure) {
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

function ItineraryMap({ day = null, options }) {
  const headingId = useId()
  const containerRef = useRef(null)
  const contextRef = useRef(null)
  const [retryCount, setRetryCount] = useState(0)
  const [mapState, setMapState] = useState(null)

  const { points, missing } = useMemo(
    () => getMapPlaces(day, options),
    [day, options],
  )

  const requestKey = JSON.stringify([points, retryCount])
  const status = points.length === 0
    ? 'empty'
    : mapState?.key === requestKey ? mapState.status : 'loading'

  useEffect(() => {
    const container = containerRef.current
    if (!container || points.length === 0) return

    let active = true
    let map = null
    let observer = null
    const overlays = []

    function releaseMap() {
      observer?.disconnect()
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
            points[0].latitude,
            points[0].longitude,
          ),
          level: 5,
          scrollwheel: false,
          keyboardShortcuts: true,
        })

        map.addControl(
          new maps.ZoomControl(),
          maps.ControlPosition.TOPRIGHT,
        )

        contextRef.current = { maps, map }

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

          label.className = group.some((item) => item.id === 'origin')
            ? 'itinerary-map-marker is-origin'
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
          }))
        }

        fitMapToPlaces(maps, map, points)

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
  }, [points, requestKey])

  function handleShowAll() {
    const context = contextRef.current

    if (context) {
      fitMapToPlaces(context.maps, context.map, points)
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
        <h3 id={headingId}>여행 장소 지도</h3>

        <button
          type="button"
          className="secondary-button"
          disabled={status !== 'ready'}
          onClick={handleShowAll}
        >
          전체 위치 보기
        </button>
      </div>

      <p className="itinerary-map-description">
        여행 출발지와 {day ? `${day.day}일차 ` : ''}방문 장소의 위치입니다.
      </p>

      {status === 'loading' && (
        <p className="map-status" role="status">
          지도를 불러오고 있습니다.
        </p>
      )}

      {status === 'empty' && (
        <p className="map-status" role="status">
          지도에 표시할 장소의 좌표가 아직 없습니다.
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
        aria-label="여행 출발지와 방문 장소 위치 지도"
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