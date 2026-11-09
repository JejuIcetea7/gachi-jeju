import { useEffect, useRef, useState } from 'react'
import { loadKakaoMaps } from './services/kakaoMaps.js'

function searchPlaces(maps, keyword) {
  return new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      reject(new Error('장소 검색 시간이 초과됐습니다. 다시 검색해 주세요.'))
    }, 15000)

    try {
      const search = new maps.services.Places()

      search.keywordSearch(keyword, (rows, status) => {
        window.clearTimeout(timeoutId)

        if (status === maps.services.Status.ZERO_RESULT) {
          resolve([])
        } else if (
          status === maps.services.Status.OK &&
          Array.isArray(rows)
        ) {
          resolve(rows)
        } else {
          reject(new Error('장소를 검색하지 못했습니다. 다시 시도해 주세요.'))
        }
      }, {
        size: 5,
        rect: '126.0,33.0,127.0,34.1',
      })
    } catch {
      window.clearTimeout(timeoutId)
      reject(new Error('장소 검색을 시작하지 못했습니다.'))
    }
  })
}

function OriginMap({ initialKeyword, confirmedPlace, onConfirm }) {
  const [keyword, setKeyword] = useState(initialKeyword)
  const [places, setPlaces] = useState([])
  const [selectedPlace, setSelectedPlace] = useState(null)
  const [isSearching, setIsSearching] = useState(false)
  const [message, setMessage] = useState(
    '장소를 검색한 뒤 결과를 눌러 위치를 확인하세요.',
  )

  const containerRef = useRef(null)
  const mapsRef = useRef(null)
  const mapRef = useRef(null)
  const markerRef = useRef(null)
  const requestIdRef = useRef(0)

  useEffect(() => {
    const container = containerRef.current

    return () => {
      requestIdRef.current += 1
      markerRef.current?.setMap(null)
      mapRef.current?.setKeyboardShortcuts(false)
      markerRef.current = null
      mapRef.current = null
      container?.replaceChildren()
    }
  }, [])

  async function handleSearch(event) {
    event.preventDefault()

    if (isSearching) {
      return
    }

    const searchKeyword = keyword.trim()
    if (!searchKeyword) {
      setMessage('검색할 장소 이름을 입력해 주세요.')
      return
    }

    const requestId = ++requestIdRef.current
    setIsSearching(true)
    setPlaces([])
    setSelectedPlace(null)
    setMessage('지도와 장소 정보를 불러오고 있습니다.')
    markerRef.current?.setMap(null)
    markerRef.current = null

    try {
      const maps = await loadKakaoMaps()

      if (requestId !== requestIdRef.current || !containerRef.current) {
        return
      }

      mapsRef.current = maps

      if (!mapRef.current) {
        mapRef.current = new maps.Map(containerRef.current, {
          // 제주 지역을 보여주는 초기 화면이며 센터의 좌표가 아닙니다.
          center: new maps.LatLng(33.38, 126.55),
          level: 10,
          scrollwheel: false,
          keyboardShortcuts: true,
        })

        mapRef.current.addControl(
          new maps.ZoomControl(),
          maps.ControlPosition.TOPRIGHT,
        )
      }

      const rows = await searchPlaces(maps, searchKeyword)

      if (requestId !== requestIdRef.current) {
        return
      }

      const results = []

      for (const row of rows) {
        if (
          !row?.id || !row.place_name ||
          String(row.x ?? '').trim() === '' ||
          String(row.y ?? '').trim() === ''
        ) {
          continue
        }

        const latitude = Number(row.y)
        const longitude = Number(row.x)

        if (
          !Number.isFinite(latitude) || !Number.isFinite(longitude) ||
          Math.abs(latitude) > 90 || Math.abs(longitude) > 180
        ) {
          continue
        }

        results.push({
          id: String(row.id),
          name: String(row.place_name),
          address: String(
            row.road_address_name || row.address_name || '주소 정보 없음',
          ),
          latitude,
          longitude,
        })
      }

      setPlaces(results)
      setMessage(results.length > 0
        ? '검색 결과를 누르면 지도에 표시됩니다. 최대 5개까지 표시합니다.'
        : '검색 결과가 없습니다. 더 정확한 장소 이름으로 검색해 주세요.')
    } catch (error) {
      if (requestId === requestIdRef.current) {
        setMessage(
          error instanceof Error
            ? error.message
            : '지도를 불러오지 못했습니다.',
        )
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setIsSearching(false)
      }
    }
  }

  function handlePreview(place) {
    const maps = mapsRef.current

    if (!maps || !mapRef.current) {
      return
    }

    const position = new maps.LatLng(place.latitude, place.longitude)
    mapRef.current.setCenter(position)
    mapRef.current.setLevel(3)

    if (markerRef.current) {
      markerRef.current.setPosition(position)
      markerRef.current.setTitle(place.name)
    } else {
      markerRef.current = new maps.Marker({
        map: mapRef.current,
        position,
        title: place.name,
      })
    }

    setSelectedPlace(place)
  }

  return (
    <section
      className="origin-map-panel"
      aria-labelledby="origin-map-title"
    >
      <h2 id="origin-map-title">출발지 위치 선택</h2>

      <form className="origin-search-form" onSubmit={handleSearch}>
        <label className="origin-search-field" htmlFor="map-keyword">
          장소 이름
          <input
            id="map-keyword"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            maxLength={100}
            disabled={isSearching}
            required
          />
        </label>

        <button
          className="map-search-button"
          type="submit"
          disabled={isSearching}
        >
          {isSearching ? '검색 중…' : '장소 검색'}
        </button>
      </form>

      <p className="map-status" role="status">{message}</p>

      <div className="origin-map-layout">
        <ul className="origin-results">
          {places.map((place) => (
            <li key={place.id}>
              <button
                type="button"
                className={selectedPlace?.id === place.id
                  ? 'theme-card is-selected'
                  : 'theme-card'}
                aria-pressed={selectedPlace?.id === place.id}
                onClick={() => handlePreview(place)}
              >
                <span>
                  <strong>{place.name}</strong>
                  <span className="theme-description">
                    {place.address}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div
          ref={containerRef}
          className="origin-map-canvas"
          role="region"
          aria-label="장소 위치 미리보기 지도"
          tabIndex={0}
        />
      </div>

      {selectedPlace && (
        <div>
          <p className="map-status" role="status">
            지도에서 보는 장소: {selectedPlace.name} · {selectedPlace.address}
          </p>

          <button
            type="button"
            className="map-search-button"
            onClick={() => {
              onConfirm(selectedPlace)
              setKeyword(selectedPlace.name)
              setMessage('선택한 장소를 출발지에 반영했습니다.')
            }}
          >
            이 장소를 출발지로 사용
          </button>
        </div>
      )}

      {confirmedPlace && (
        <p className="map-status" role="status">
          현재 출발지: {confirmedPlace.name} · {confirmedPlace.address}
        </p>
      )}
    </section>
  )
}

export default OriginMap