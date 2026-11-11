export const DEFAULT_ORIGIN = '제주빅데이터센터'

export const TRAVEL_STYLES = [
  '자연', '해변', '박물관·전시', '산책로', '공원', '시장',
]

export const TRANSPORT_OPTIONS = [
  { value: 'walking', label: '도보' },
  { value: 'driving', label: '자동차' },
  { value: 'transit', label: '대중교통' },
]

export const MOBILITY_OPTIONS = [
  { value: 'wheelchair', label: '휠체어' },
  { value: 'stroller', label: '유아차' },
  { value: 'none', label: '사용 안 함' },
]

export const ACCESS_REQUIREMENTS = [
  { value: 'step_free', label: '계단 피하기' },
  { value: 'accessible_toilet', label: '장애인 화장실' },
  { value: 'accessible_parking', label: '장애인 주차구역' },
]

export function createDefaultChatOptions() {
  return {
    departure: DEFAULT_ORIGIN,
    departure_latitude: null,
    departure_longitude: null,
    transport: null,
    mobility_aid: null,
    start_date: null,
    trip_days: null,
    travel_styles: [],
    requirements: [],
    return_to_origin: null,
  }
}

const DAY_MS = 24 * 60 * 60 * 1000

function parseDate(value) {
  if (
    typeof value !== 'string'
    || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)
    || value < '0001-01-01'
  ) {
    return null
  }

  const timestamp = Date.parse(`${value}T00:00:00Z`)

  return Number.isFinite(timestamp)
    && new Date(timestamp).toISOString().slice(0, 10) === value
    ? timestamp
    : null
}

function validateTripDays(days) {
  if (!Number.isSafeInteger(days) || days < 1) {
    throw new Error('여행 일수는 1일 이상의 정수여야 합니다.')
  }
}

export function isTravelDate(value) {
  return parseDate(value) !== null
}

export function getTripDays(startDate, endDate) {
  if (
    startDate == null || startDate === ''
    || endDate == null || endDate === ''
  ) {
    return null
  }

  const start = parseDate(startDate)
  const end = parseDate(endDate)

  if (start === null || end === null) {
    throw new Error('여행 날짜를 다시 확인해 주세요.')
  }
  if (end < start) {
    throw new Error('마지막 날은 출발일보다 빠를 수 없습니다.')
  }

  const days = (end - start) / DAY_MS + 1
  validateTripDays(days)
  return days
}

export function getTripEndDate(startDate, tripDays) {
  if (startDate == null || startDate === '' || tripDays == null) {
    return ''
  }

  validateTripDays(tripDays)
  const start = parseDate(startDate)

  if (start === null) {
    throw new Error('여행 날짜를 다시 확인해 주세요.')
  }

  const end = start + (tripDays - 1) * DAY_MS

  if (end > parseDate('9999-12-31')) {
    throw new Error('선택할 수 있는 날짜 범위를 벗어났습니다.')
  }

  return new Date(end).toISOString().slice(0, 10)
}

export function formatTripDuration(tripDays) {
  if (tripDays == null) return '기간 미정'

  validateTripDays(tripDays)
  return tripDays === 1 ? '당일' : `${tripDays - 1}박 ${tripDays}일`
}

export function getKoreaToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)

  const values = Object.fromEntries(
    parts.map(({ type, value }) => [type, value]),
  )

  return `${values.year}-${values.month}-${values.day}`
}

export function isPastTravelDate(value, now = new Date()) {
  return isTravelDate(value) && value < getKoreaToday(now)
}