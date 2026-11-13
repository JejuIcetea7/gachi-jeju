import { useEffect, useId, useRef, useState } from 'react'
import { getLodgingSearchCenter, getLodgingStay } from './services/lodgingPlanning.js'
import { requestNearbyLodgings } from './services/lodgingsApi.js'

function LodgingPanel({ day, options, disabled = false, onSelect, onRemove }) {
  const headingId = useId()
  const controllerRef = useRef(null)
  const [state, setState] = useState({ status: 'idle', result: null, error: null })
  const [open, setOpen] = useState(false)
  const center = getLodgingSearchCenter(day)
  const selected = getLodgingStay(options, day?.day)
  const canChoose = Number.isSafeInteger(day?.day) && day.day < options?.trip_days
    && options.lodging_status !== 'not_needed'

  useEffect(() => () => controllerRef.current?.abort(), [])

  async function search() {
    if (disabled || !center || state.status === 'loading') return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setOpen(true)
    setState({ status: 'loading', result: null, error: null })
    try {
      const result = await requestNearbyLodgings({ ...center, signal: controller.signal })
      if (controller.signal.aborted || controllerRef.current !== controller) return
      setState({ status: 'ready', result, error: null })
    } catch (error) {
      if (controller.signal.aborted || controllerRef.current !== controller) return
      setState({ status: 'error', result: null, error: error.message })
    }
  }

  if (!canChoose) return null
  const queryTime = state.result ? new Intl.DateTimeFormat('ko-KR', {
    dateStyle: 'short', timeStyle: 'short', timeZone: 'Asia/Seoul',
  }).format(new Date(state.result.requested_at)) : ''

  return (
    <section className="lodging-panel" aria-labelledby={headingId}>
      <div className="lodging-panel-heading">
        <h3 id={headingId}>{day.day}일차 숙소 {selected ? '선택됨' : '미정'}</h3>
        <button type="button" className="secondary-button" disabled={disabled || !center || state.status === 'loading'} onClick={search}>
          {state.status === 'loading' ? '숙소 조회 중…' : selected ? '다른 숙소 찾기' : '주변 숙소 보기'}
        </button>
      </div>

      {selected ? (
        <div className="lodging-selected">
          <p><strong>{selected.name}</strong></p>
          {selected.address && <p>{selected.address}</p>}
          <div className="lodging-candidate-actions">
            {selected.place_url && <a href={selected.place_url} target="_blank" rel="noopener noreferrer">지도에서 보기</a>}
            <button type="button" className="lodging-text-button" disabled={disabled} onClick={() => onRemove?.(day.day)}>
              이 숙박일 선택 취소
            </button>
          </div>
        </div>
      ) : (
        <p>숙소를 정하지 않아도 관광 일정을 볼 수 있습니다. 숙소 선택 후 전후 이동과 시간을 다시 계산합니다.</p>
      )}

      {!center && <p>마지막 방문지의 위치를 확인한 뒤 주변 숙소를 찾을 수 있습니다. 정해진 숙소는 대화로 알려주세요.</p>}
      {disabled && <p>요청 처리 중이거나 여행 옵션이 변경되었습니다. 현재 옵션으로 일정을 반영한 뒤 숙소를 선택해 주세요.</p>}

      {state.status !== 'idle' && (
        <button type="button" className="lodging-text-button" onClick={() => setOpen((previous) => !previous)} aria-expanded={open}>
          {open ? '검색 결과 접기' : '검색 결과 펼치기'}
        </button>
      )}

      {open && (
        <div className="lodging-search-results" role="region" aria-label="주변 숙소 검색 결과" tabIndex={0} aria-busy={state.status === 'loading'}>
          {state.status === 'loading' && <p role="status">주변 숙소 후보를 찾고 있습니다.</p>}
          {state.status === 'error' && <p role="alert">{state.error}</p>}
          {state.result && (
            <>
              <p>{center.name} 주변 반경 5km의 숙소 후보입니다. 거리 표시는 직선거리입니다.</p>
              <p>가격·잔여 객실·접근성은 확인되지 않았습니다. 지도 링크나 숙소 문의로 확인해 주세요.</p>
              <p className="lodging-query-time">카카오 장소 검색 · 조회 시각(한국): {queryTime}</p>
              {state.result.places.length === 0 ? <p role="status">이 범위에서 숙소 후보를 찾지 못했습니다. 숙소 이름이나 다른 지역을 대화로 알려주세요.</p> : (
                <ul className="lodging-candidates">
                  {state.result.places.map((candidate) => (
                    <li key={candidate.provider_place_id}>
                      <strong>{candidate.name}</strong>
                      <p>{candidate.address || '주소 확인 필요'}</p>
                      <p>직선거리 {(candidate.distance_m / 1000).toFixed(1)}km{candidate.phone ? ` · ${candidate.phone}` : ''}</p>
                      <div className="lodging-candidate-actions">
                        <a href={candidate.place_url} target="_blank" rel="noopener noreferrer">지도에서 보기</a>
                        <button type="button" className="secondary-button" disabled={disabled} onClick={() => onSelect?.(day.day, candidate)}>
                          이 숙박일에 선택
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}
    </section>
  )
}

export default LodgingPanel
