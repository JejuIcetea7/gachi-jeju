import { useEffect, useRef, useState } from 'react'
import OriginSearch from './OriginSearch.jsx'
import {
  CENTER_ORIGIN_NAME,
  getCenterOrigin,
  getCurrentOrigin,
} from './services/originLocation.js'

function OriginPicker({ initialKeyword, confirmedPlace, onConfirm }) {
  const initialName = initialKeyword.trim()
  const shouldLoadCenter = !confirmedPlace &&
    (!initialName || initialName === CENTER_ORIGIN_NAME)

  const [searchKeyword, setSearchKeyword] = useState(initialKeyword)
  const [searchVersion, setSearchVersion] = useState(0)
  const [isBusy, setIsBusy] = useState(shouldLoadCenter)
  const [message, setMessage] = useState(
    shouldLoadCenter
      ? '센터 기본 출발지를 확인하고 있습니다.'
      : '장소 검색이나 현재 위치로 출발지를 선택할 수 있습니다.',
  )

  const loadCenterRef = useRef(shouldLoadCenter)
  const requestIdRef = useRef(0)
  const onConfirmRef = useRef(onConfirm)

  useEffect(() => {
    onConfirmRef.current = onConfirm
  }, [onConfirm])

  useEffect(() => {
    const requestId = ++requestIdRef.current

    async function initialize() {
      try {
        const place = await getCenterOrigin()
        if (requestId !== requestIdRef.current) return
        onConfirmRef.current({ ...place })
        setSearchKeyword(place.name)
        setSearchVersion((version) => version + 1)
        setMessage('센터를 기본 출발지로 선택했습니다.')
      } catch (error) {
        if (requestId === requestIdRef.current) setMessage(error.message)
      } finally {
        if (requestId === requestIdRef.current) setIsBusy(false)
      }
    }

    if (loadCenterRef.current) {
      initialize()
    }

    return () => {
      requestIdRef.current += 1
    }
  }, [])

  async function handleChooseOrigin(loadOrigin, loadingMessage) {
    if (isBusy) return
    const requestId = ++requestIdRef.current
    setIsBusy(true)
    setMessage(loadingMessage)

    try {
      const place = await loadOrigin()
      if (requestId !== requestIdRef.current) return
      onConfirmRef.current({ ...place })
      setSearchKeyword(place.name)
      setSearchVersion((version) => version + 1)
      setMessage('출발지를 선택했습니다.')
    } catch (error) {
      if (requestId === requestIdRef.current) setMessage(error.message)
    } finally {
      if (requestId === requestIdRef.current) setIsBusy(false)
    }
  }

  function handleManualConfirm(place) {
    if (isBusy) return
    requestIdRef.current += 1
    onConfirmRef.current({ ...place })
    setSearchKeyword(place.name)
    setSearchVersion((version) => version + 1)
    setMessage('선택한 장소를 출발지로 설정했습니다.')
  }

  return (
    <div className="origin-picker">
      <div className="origin-actions">
        <button
          type="button"
          className="map-search-button"
          disabled={isBusy}
          onClick={() => handleChooseOrigin(
            getCenterOrigin,
            '센터 출발지를 확인하고 있습니다.',
          )}
        >
          센터에서 출발
        </button>
        <button
          type="button"
          className="map-search-button"
          disabled={isBusy}
          onClick={() => handleChooseOrigin(
            getCurrentOrigin,
            '현재 위치를 확인하고 있습니다.',
          )}
        >
          현재 위치 사용
        </button>
      </div>

      <p className="map-status" role="status">{message}</p>

      <OriginSearch
        key={searchVersion}
        keyword={searchKeyword}
        onKeywordChange={setSearchKeyword}
        onConfirm={handleManualConfirm}
        disabled={isBusy}
      />

      {Number.isFinite(confirmedPlace?.accuracy_m) && (
        <p className="map-status">
          현재 출발지의 기기 위치 오차 범위: 약 {Math.round(confirmedPlace.accuracy_m)}m
        </p>
      )}
    </div>
  )
}

export default OriginPicker