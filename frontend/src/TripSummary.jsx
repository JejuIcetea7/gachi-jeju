import TravelChat from './TravelChat.jsx'
import TravelPlan from './TravelPlan.jsx'

const FIELD_LABELS = {
  departure: '출발지',
  departure_latitude: '출발지 위치',
  departure_longitude: '출발지 위치',
  transport: '이동수단',
  mobility_aid: '이동 보조수단',
  start_date: '여행 날짜',
  trip_days: '여행 기간',
  start_time: '시작 시각',
  end_time: '종료 시각',
  travel_styles: '여행 종류',
  requirements: '필수 이용 조건',
  rest_minutes: '휴식 시간',
  return_to_origin: '귀환 여부',
  message: '추가 요청',
  body: '여행 조건',
  plan: '생성된 일정',
}

function formatErrorDetail(detail) {
  const field = detail.field.split('.')[0]
  const label = Object.hasOwn(FIELD_LABELS, field)
    ? FIELD_LABELS[field]
    : '여행 조건'
  const reason = detail.reason.replace(/^Value error,\s*/, '')
  return `${label}: ${reason}`
}

function TripSummary({
  summary,
  onEdit,
  onEditOrigin,
  chatMessages,
  onChatSend,
  onChatRemove,
  isGenerating = false,
  planResult = null,
  planError = null,
  onGenerate,
}) {
  if (!summary) {
    return null
  }

  const summaryItems = [
    { label: '출발지', value: summary.originName },
    {
      label: '출발지 주소',
      value: summary.originPlace?.address || '출발지 위치 확인이 필요합니다.',
    },
    { label: '여행 종류', value: summary.themeName },
    { label: '이동수단', value: summary.transportName },
    { label: '여행 날짜', value: summary.travelDate },
    {
      label: '여행 시간',
      value: `${summary.startTime} ~ ${summary.endTime}`,
    },
    {
      label: '필수 이용 조건',
      value: summary.requirementNames.length > 0
        ? summary.requirementNames.join(' · ')
        : '별도 조건 없음',
    },
    {
      label: '중간 휴식',
      value: summary.restMinutes > 0
        ? `${summary.restMinutes}분 (1회)`
        : '휴식 없이 계획',
    },
    {
      label: '여행 종료',
      value: summary.returnToOrigin
        ? '출발지로 돌아오기'
        : '마지막 여행지에서 마치기',
    },
  ]

  return (
    <section aria-labelledby="trip-summary-title">
      <button
        type="button"
        className="back-button"
        onClick={onEdit}
        disabled={isGenerating}
      >
        ← 여행 조건 수정
      </button>

      <div className="page-heading">
        <p className="eyebrow">여행 조건 요약</p>
        <h1 id="trip-summary-title">여행 조건을 확인해 주세요</h1>
        <p>
          시간과 이동 조건을 확인하고,
          바꾸고 싶은 내용은 수정할 수 있습니다.
        </p>
      </div>

      <button
        type="button"
        className="back-button"
        onClick={onEditOrigin}
        disabled={isGenerating}
      >
        출발지 변경
      </button>

      <dl className="summary-list">
        {summaryItems.map((item) => (
          <div key={item.label}>
            <dt>{item.label}</dt>
            <dd>{item.value}</dd>
          </div>
        ))}
      </dl>

      <TravelChat
        messages={chatMessages}
        onSend={onChatSend}
        onRemove={onChatRemove}
        disabled={isGenerating}
      />

      <div className="plan-actions">
        <button
          type="button"
          className="primary-button"
          onClick={onGenerate}
          disabled={isGenerating || typeof onGenerate !== 'function'}
        >
          {isGenerating
            ? '일정 만드는 중…'
            : planError
              ? '다시 시도하기'
              : planResult
                ? '일정 다시 만들기'
                : '일정 만들기'}
        </button>
      </div>

      {isGenerating && (
        <p className="plan-loading" role="status">
          여행 조건을 반영해 일정을 만드는 중입니다.
          완료되면 아래에 시간표가 표시됩니다.
        </p>
      )}

      {planError && (
        <div className="plan-error" role="alert">
          <p>{planError.message}</p>
          {planError.details.length > 0 && (
            <ul>
              {planError.details.map((detail, index) => (
                <li key={index}>{formatErrorDetail(detail)}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="plan-result-area" aria-busy={isGenerating}>
        {planResult?.warnings?.length > 0 && (
          <div className="plan-notice">
            <h2>확인이 필요한 내용</h2>
            <ul>
              {planResult.warnings.map((text, index) => (
                <li key={index}>{text}</li>
              ))}
            </ul>
          </div>
        )}
        <TravelPlan plan={planResult?.plan} />
      </div>
    </section>
  )
}

export default TripSummary