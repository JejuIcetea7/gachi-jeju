import { useEffect, useRef, useState } from 'react'
import PlaceImageDialog from './PlaceImageDialog.jsx'
import { getPlanTransitGuidance, isTransitMove } from './services/planTransit.js'

function formatDistance(value) {
  if (!Number.isFinite(value) || value < 0) {
    return '거리 확인 필요'
  }
  if (value < 1) {
    return `${Math.round(value * 1000).toLocaleString('ko-KR')}m`
  }
  return `${value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}km`
}

function segmentEstimate(segment) {
  const values = []
  if (Number.isFinite(segment.distance_m)) {
    values.push(formatDistance(segment.distance_m / 1000))
  }
  if (Number.isFinite(segment.duration_seconds)) {
    values.push(`${Math.ceil(segment.duration_seconds / 60)}분`)
  }
  return values.join(' · ')
}

function TransitMoveGuide({ guidance }) {
  if (!guidance) {
    return (
      <p className="plan-transit-empty">
        버스 승하차 상세 정보를 확인하지 못했습니다.
      </p>
    )
  }

  return (
    <div className="plan-transit-guide">
      <ol className="plan-transit-steps" aria-label="대중교통 이동 순서">
        {guidance.segments.map((segment, index) => (
          <li key={index}>
            {segment.type === 'BUS' ? (
              <>
                <strong>
                  {segment.route_numbers.length > 0
                    ? `버스 ${segment.route_numbers.join(' 또는 ')}`
                    : '버스 노선 미확인'}
                </strong>
                <span>
                  {segment.boarding_stop?.trim()
                    ? `${segment.boarding_stop} 승차`
                    : '승차 정류장 미확인'}
                  {' → '}
                  {segment.alighting_stop?.trim()
                    ? `${segment.alighting_stop} 하차`
                    : '하차 정류장 미확인'}
                </span>
                <span>
                  {segment.ride_stop_count === null
                    ? '이동 정거장 수 미확인'
                    : `${segment.ride_stop_count}정거장 이동`}
                </span>
              </>
            ) : segment.type === 'WALKING' ? (
              <span>도보{segmentEstimate(segment) && ` · ${segmentEstimate(segment)}`}</span>
            ) : (
              <span>연결 이동 구간의 상세 정보가 확인되지 않았습니다.</span>
            )}
          </li>
        ))}
      </ol>
      <p className="plan-transit-query">
        경로 조회 시각(한국):{' '}
        {new Date(guidance.requested_at).toLocaleString('ko-KR', {
          timeZone: 'Asia/Seoul', dateStyle: 'short', timeStyle: 'short',
        })}
      </p>
    </div>
  )
}

function TravelPlan({ plan, autoFocus = true, transitRoutes = [] }) {
  const headingRef = useRef(null)
  const [photoPlace, setPhotoPlace] = useState(null)
  const guidanceById = getPlanTransitGuidance(plan, transitRoutes)

  useEffect(() => {
    if (plan && autoFocus) {
      headingRef.current?.focus()
    }
  }, [plan, autoFocus])

  if (!plan) {
    return null
  }

  return (
    <section className="travel-plan" aria-labelledby="travel-plan-title">
      <h2 id="travel-plan-title" ref={headingRef} tabIndex={-1}>
        추천 여행 일정
      </h2>
      <p className="plan-description">
        자료 등록 표시는 여행지 목록에 포함된 장소라는 뜻입니다.
        장소명을 누르면 이번 일정의 접근성 안내와 시설·동선 정보를 확인할 수 있습니다.
      </p>

      {plan.days.length === 0 && (
        <p className="plan-empty" role="status">
          조건에 맞는 일정을 찾지 못했습니다. 여행 조건을 조정해 주세요.
        </p>
      )}

      {plan.days.map((day, dayIndex) => (
        <div className="plan-day" key={`${day.day}-${dayIndex}`}>
          <h3>{day.day}일차</h3>
          {day.items.length === 0 ? (
            <p className="plan-empty" role="status">
              이 날짜에 조건을 충족하는 일정을 찾지 못했습니다.
              {plan.assumptions.length > 0
                && ' 아래의 확인사항을 참고해 주세요.'}
            </p>
          ) : (
            <div
              className="plan-table-scroll"
              role="region"
              aria-label={`${day.day}일차 일정표`}
              tabIndex={0}
            >
              <table className="plan-table plan-table-compact">
                <caption>방문·이동 시간표</caption>
                <thead>
                  <tr>
                    <th scope="col">시간</th>
                    <th scope="col">일정</th>
                    <th scope="col">소요시간</th>
                  </tr>
                </thead>
                <tbody>
                  {day.items.map((item, index) => (
                    <tr key={index}>
                      <td className="plan-time">
                        {item.start_time} ~ {item.end_time}
                      </td>
                      <td>
                        <span className="plan-type">
                          {item.item_type === 'visit' ? '방문' : '이동'}
                        </span>
                        {item.item_type === 'visit' ? (
                          <>
                            <button
                              type="button"
                              className="plan-item-title plan-place-button"
                              aria-haspopup="dialog"
                              aria-label={`${item.title} 상세 정보 및 사진 보기`}
                              onClick={() => setPhotoPlace(item)}
                            >
                              {item.title}
                            </button>
                            <span className="plan-source">
                              {item.verified === true
                                ? '여행지 자료 등록'
                                : '여행지 정보 확인 필요'}
                            </span>
                          </>
                        ) : (
                          <>
                            <strong className="plan-item-title">{item.title}</strong>
                            {isTransitMove(item) && (
                              <TransitMoveGuide guidance={guidanceById.get(item.route_id)} />
                            )}
                            {item.accessibility_notes.length > 0 && (
                              <details className="plan-move-notes">
                                <summary>이동 시 확인사항</summary>
                                <ul className="plan-notes">
                                  {item.accessibility_notes.map((text, noteIndex) => (
                                    <li key={noteIndex}>{text}</li>
                                  ))}
                                </ul>
                              </details>
                            )}
                          </>
                        )}
                      </td>
                      <td>
                        {Number.isFinite(item.duration_minutes)
                          && item.duration_minutes >= 0
                          ? `${item.duration_minutes}분`
                          : '시간 확인 필요'}
                        {item.item_type === 'move' && (
                          <small className="plan-move-detail">
                            {item.transport || '이동수단 확인 필요'}
                            {' · '}
                            {formatDistance(item.distance_km)}
                          </small>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {day.items.some(isTransitMove) && (
            <p className="plan-transit-footnote">
              정거장 수는 승차지를 제외하고 하차지를 포함합니다.
              조회 시점의 경로이므로 여행 당일 운행을 확인해 주세요.
            </p>
          )}
        </div>
      ))}

      {plan.assumptions.length > 0 && (
        <div className="plan-notice">
          <h3>계획 전제와 확인사항</h3>
          <ul>
            {plan.assumptions.map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ul>
        </div>
      )}
      {photoPlace && (
        <PlaceImageDialog
          key={`${photoPlace.place_id ?? ''}:${photoPlace.title}:${photoPlace.verified}`}
          place={photoPlace}
          onClose={() => setPhotoPlace(null)}
        />
      )}
    </section>
  )
}

export default TravelPlan
