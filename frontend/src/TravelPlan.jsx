import { useEffect, useRef } from 'react'

function formatDistance(value) {
  if (!Number.isFinite(value) || value < 0) {
    return '거리 확인 필요'
  }
  if (value < 1) {
    return `${Math.round(value * 1000).toLocaleString('ko-KR')}m`
  }
  return `${value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}km`
}

function TravelPlan({ plan }) {
  const headingRef = useRef(null)

  useEffect(() => {
    if (plan) {
      headingRef.current?.focus()
    }
  }, [plan])

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
        이용 가능한 동선과 편의시설은 각 항목의 안내를 확인해 주세요.
      </p>

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
              위의 확인사항을 참고해 주세요.
            </p>
          ) : (
            <div
              className="plan-table-scroll"
              role="region"
              aria-label={`${day.day}일차 일정표`}
              tabIndex={0}
            >
              <table className="plan-table">
                <caption>방문·이동 시간표</caption>
                <thead>
                  <tr>
                    <th scope="col">시간</th>
                    <th scope="col">일정</th>
                    <th scope="col">소요시간</th>
                    <th scope="col">접근성 안내</th>
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
                        <strong className="plan-item-title">{item.title}</strong>
                        {item.item_type === 'visit' && (
                          <span className="plan-source">
                            {item.verified === true
                              ? '여행지 자료 등록'
                              : '여행지 정보 확인 필요'}
                          </span>
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
                      <td>
                        {item.accessibility_notes.length > 0 ? (
                          <ul className="plan-notes">
                            {item.accessibility_notes.map((text, noteIndex) => (
                              <li key={noteIndex}>{text}</li>
                            ))}
                          </ul>
                        ) : (
                          <p className="plan-muted">
                            접근성 안내가 제공되지 않았습니다.
                          </p>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </section>
  )
}

export default TravelPlan