export const MAX_PLAN_MESSAGE_LENGTH = 1000

const THEME_STYLES = {
  nature: ['자연', '산책로', '공원'],
  coast: ['해변'],
  culture: ['박물관·전시'],
  market: ['시장'],
  mixed: ['자연', '해변', '박물관·전시', '산책로', '공원', '시장'],
}
const TRANSPORT_MODES = ['walking', 'driving', 'transit']
const REQUIREMENT_IDS = [
  'step_free',
  'accessible_toilet',
  'accessible_parking',
]

export function buildPlanRequest({
  summary,
  themeId,
  transportMode,
  requirementIds = [],
  messages = [],
}) {
  const place = summary?.originPlace

  if (
    typeof summary?.originName !== 'string'
    || !summary.originName.trim()
    || !Number.isFinite(place?.latitude)
    || !Number.isFinite(place?.longitude)
  ) {
    throw new Error('첫 화면에서 출발지를 다시 선택해 주세요.')
  }

  const styles = THEME_STYLES[themeId]
  if (!Array.isArray(styles)) {
    throw new Error('여행 종류를 다시 선택해 주세요.')
  }
  if (!TRANSPORT_MODES.includes(transportMode)) {
    throw new Error('이동수단을 다시 선택해 주세요.')
  }
  if (
    !Array.isArray(requirementIds)
    || !requirementIds.every((id) => REQUIREMENT_IDS.includes(id))
  ) {
    throw new Error('필수 이용 조건을 다시 확인해 주세요.')
  }
  if (!Array.isArray(messages)) {
    throw new Error('추가 요청 목록을 확인해 주세요.')
  }

  const message = messages
    .filter((item) => item?.role === 'user')
    .map((item) => {
      if (typeof item.content !== 'string') {
        throw new Error('추가 요청 내용을 확인해 주세요.')
      }
      return item.content.trim()
    })
    .filter(Boolean)
    .join('\n')

  if (message.length > MAX_PLAN_MESSAGE_LENGTH) {
    throw new Error('추가 요청은 모두 합쳐 1,000자 이하로 작성해 주세요.')
  }

  return {
    departure: summary.originName.trim(),
    departure_latitude: place.latitude,
    departure_longitude: place.longitude,
    transport: transportMode,
    mobility_aid: null,
    start_date: summary.travelDate,
    trip_days: 1,
    start_time: summary.startTime,
    end_time: summary.endTime,
    travel_styles: [...styles],
    requirements: [...requirementIds],
    rest_minutes: summary.restMinutes,
    return_to_origin: summary.returnToOrigin,
    message: message || null,
  }
}