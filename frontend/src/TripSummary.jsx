import OriginMap from './OriginMap.jsx'
import TravelChat from './TravelChat.jsx'

function TripSummary({
  summary,
  onEdit,
  onOriginConfirm,
  chatMessages,
  onChatSend,
}) {
  if (!summary) {
    return null
  }

  const summaryItems = [
    {
      label: '출발지',
      value: summary.originName,
    },
    {
      label: '출발지 주소',
      value: summary.originPlace?.address
        || '지도에서 출발지를 선택해 주세요.',
    },
    {
      label: '여행 종류',
      value: summary.themeName,
    },
    {
      label: '이동수단',
      value: summary.transportName,
    },
    {
      label: '여행 날짜',
      value: summary.travelDate,
    },
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
      >
        ← 여행 조건 수정
      </button>

      <div className="page-heading">
        <p className="eyebrow">여행 조건 요약</p>
        <h1 id="trip-summary-title">
          여행 조건을 확인해 주세요
        </h1>
        <p>
          시간과 이동 조건을 확인하고,
          바꾸고 싶은 내용은 수정할 수 있습니다.
        </p>
      </div>

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
      />

      <OriginMap
        initialKeyword={summary.originName}
        confirmedPlace={summary.originPlace}
        onConfirm={onOriginConfirm}
      />
    </section>
  )
}

export default TripSummary