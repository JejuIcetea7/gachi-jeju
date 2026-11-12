import { useEffect, useId, useRef, useState } from 'react'
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

function OriginSearch({
  keyword,
  onKeywordChange,
  onConfirm,
  disabled = false,
}) {
  const inputId = useId()
  const [places, setPlaces] = useState([])
  const [isSearching, setIsSearching] = useState(false)
  const [message, setMessage] = useState(
    '장소를 검색한 뒤 출발지로 사용할 결과를 선택하세요.',
  )
  const requestIdRef = useRef(0)

  useEffect(() => {
    return () => {
      requestIdRef.current += 1
    }
  }, [])

  async function handleSearch(event) {
    event.preventDefault()
    if (isSearching || disabled) return

    const searchKeyword = keyword.trim()
    if (!searchKeyword) {
      setMessage('검색할 장소 이름을 입력해 주세요.')
      return
    }

    const requestId = ++requestIdRef.current
    setIsSearching(true)
    setPlaces([])
    setMessage('장소를 검색하고 있습니다.')

    try {
      const maps = await loadKakaoMaps()
      if (requestId !== requestIdRef.current) return

      const rows = await searchPlaces(maps, searchKeyword)
      if (requestId !== requestIdRef.current) return

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
        ? '검색 결과를 누르면 출발지로 선택됩니다. 최대 5개까지 표시합니다.'
        : '검색 결과가 없습니다. 더 정확한 장소 이름으로 검색해 주세요.')
    } catch (error) {
      if (requestId === requestIdRef.current) {
        setMessage(error instanceof Error
          ? error.message
          : '장소를 검색하지 못했습니다. 다시 시도해 주세요.')
      }
    } finally {
      if (requestId === requestIdRef.current) setIsSearching(false)
    }
  }

  return (
    <section
      className="origin-search-panel"
      aria-labelledby={`${inputId}-title`}
      aria-busy={isSearching}
    >
      <h2 id={`${inputId}-title`}>출발지 검색</h2>

      <form className="origin-search-form" onSubmit={handleSearch}>
        <label className="origin-search-field" htmlFor={inputId}>
          장소 이름
          <input
            id={inputId}
            type="search"
            value={keyword}
            onChange={(event) => {
              onKeywordChange(event.target.value)
              setPlaces([])
              setMessage('장소를 검색한 뒤 출발지로 사용할 결과를 선택하세요.')
            }}
            maxLength={100}
            disabled={isSearching || disabled}
            required
          />
        </label>

        <button
          className="map-search-button"
          type="submit"
          disabled={isSearching || disabled}
        >
          {isSearching ? '검색 중…' : '장소 검색'}
        </button>
      </form>

      <p className="map-status" role="status">{message}</p>

      <ul className="origin-results">
        {places.map((place) => (
          <li key={place.id}>
            <button
              type="button"
              className="theme-card"
              disabled={isSearching || disabled}
              onClick={() => {
                if (isSearching || disabled) return
                onConfirm({ ...place })
              }}
            >
              <span>
                <strong>{place.name}</strong>
                <span className="theme-description">{place.address}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default OriginSearch