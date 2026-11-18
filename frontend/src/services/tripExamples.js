import { DEFAULT_ORIGIN } from './travelOptions.js'

const TRIP_EXAMPLES = [
  {
    text: '당일치기로 한림공원과 협재해수욕장을 자동차로 둘러보고 싶어요.',
    transport: 'driving',
    tripDays: 1,
  },
  {
    text: '한림공원에서 협재해수욕장까지 걸어가고 싶어요.',
    transport: 'walking',
    departure: '한림공원',
  },
  {
    text: '1박 2일 동안 자동차로 한라수목원과 제주국립박물관에 가고 싶어요.',
    transport: 'driving',
    tripDays: 2,
  },
]

export function getTripExampleMessages({ departure, transport, trip_days: tripDays }, originPlace) {
  const hasCustomDeparture = departure && (
    departure !== DEFAULT_ORIGIN || (originPlace && originPlace.source !== 'center')
  )

  return TRIP_EXAMPLES.filter((example) => (
    (!transport || transport === example.transport)
    && (!tripDays || !example.tripDays || tripDays === example.tripDays)
    && (!example.departure || !hasCustomDeparture || departure === example.departure)
  )).map((example) => example.text)
}