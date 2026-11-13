import test from 'node:test'
import assert from 'node:assert/strict'
import {
  chooseLodging, getLodgingMapAnchors, getLodgingSearchCenter, getLodgingSelectionMessage,
  getLodgingStay, isLodgingOptions, normalizeLodgingOptions, removeLodging, resizeLodgingOptions,
} from '../../frontend/src/services/lodgingPlanning.js'
import { createDefaultChatOptions } from '../../frontend/src/services/travelOptions.js'
import { isChatOptions } from '../../frontend/src/services/chatApi.js'
import { createEmptyChatSession, loadChatSession, saveChatSession } from '../../frontend/src/services/chatSession.js'

const candidate = (id = '100') => ({
  provider_place_id: id, name: '선택한 숙소', address: '제주시 테스트 도로 1',
  latitude: 33.45, longitude: 126.55, place_url: `https://place.map.kakao.com/${id}`,
  phone: '064-000-0000', distance_m: 100,
})
const options = () => ({ ...createDefaultChatOptions(), trip_days: 3, start_date: '2026-11-20',
  departure: '원래 출발지', departure_latitude: 33.4, departure_longitude: 126.5, return_to_origin: true })
const visit = (changes = {}) => ({ item_type: 'visit', title: '마지막 관광지', verified: true,
  latitude: 33.41, longitude: 126.51, ...changes })

test('예전 대화 옵션은 숙소 상태 없이도 읽고 기본 숙소 필드를 보완합니다', () => {
  const old = options()
  delete old.lodging_status
  delete old.lodging_stays
  assert.equal(isChatOptions(old), true)
  assert.deepEqual(normalizeLodgingOptions(old).lodging_stays, [])
  assert.equal(normalizeLodgingOptions(old).lodging_status, null)
  assert.equal(Object.hasOwn(old, 'lodging_status'), false)
})

test('첫 숙박일 선택은 다른 조건을 유지하고 미정인 나머지 숙박일을 남깁니다', () => {
  const original = options()
  const before = structuredClone(original)
  const selected = chooseLodging(original, 1, candidate())
  assert.equal(selected.lodging_status, 'undecided')
  assert.deepEqual(selected.lodging_stays.map((stay) => stay.night), [1])
  assert.equal(selected.departure, original.departure)
  assert.equal(selected.start_date, original.start_date)
  assert.equal(Object.hasOwn(selected.lodging_stays[0], 'distance_m'), false)
  assert.equal(Object.hasOwn(selected.lodging_stays[0], 'phone'), false)
  selected.lodging_stays[0].name = '변경'
  assert.deepEqual(original, before)
})

test('숙박일별 선택을 교체하고 모든 숙소가 정해지면 제공 상태로 바꿉니다', () => {
  const first = chooseLodging(options(), 2, candidate('200'))
  const full = chooseLodging(first, 1, candidate())
  assert.equal(full.lodging_status, 'provided')
  assert.deepEqual(full.lodging_stays.map((stay) => stay.night), [1, 2])
  const changed = chooseLodging(full, 1, candidate('300'))
  assert.equal(changed.lodging_stays.length, 2)
  assert.equal(changed.lodging_stays[0].provider_place_id, '300')
  assert.equal(changed.lodging_stays[1].provider_place_id, '200')
})

test('범위를 벗어난 숙박일과 잘못된 숙소를 선택하지 않습니다', () => {
  for (const night of [0, 3, true, 1.5]) assert.throws(() => chooseLodging(options(), night, candidate()))
  for (const changes of [{ latitude: NaN }, { latitude: 37.5 }, { name: ' ' },
    { place_url: 'javascript:invalid' }, { provider_place_id: 'P001' }]) {
    assert.throws(() => chooseLodging(options(), 1, { ...candidate(), ...changes }))
  }
})

test('숙소 선택 취소는 해당 숙박일만 제거하고 미정 상태를 유지합니다', () => {
  const full = chooseLodging(chooseLodging(options(), 1, candidate()), 2, candidate('200'))
  const next = removeLodging(full, 1)
  assert.deepEqual(next.lodging_stays.map((stay) => stay.night), [2])
  assert.equal(next.lodging_status, 'undecided')
  assert.equal(full.lodging_stays.length, 2)
})

test('기간을 줄이면 범위를 벗어난 선택을 제거하고 늘리면 추가 숙박일을 미정으로 둡니다', () => {
  const full = chooseLodging(chooseLodging(options(), 1, candidate()), 2, candidate('200'))
  assert.deepEqual(resizeLodgingOptions({ ...full, trip_days: 2 }).lodging_stays.map((stay) => stay.night), [1])
  const longer = resizeLodgingOptions({ ...full, trip_days: 5 })
  assert.equal(longer.lodging_status, 'undecided')
  assert.equal(longer.lodging_stays.length, 2)
  assert.deepEqual(resizeLodgingOptions({ ...full, trip_days: null }).lodging_stays, [])
})

test('숙소 상태·좌표·숙박일 중복과 임의 시설 보장 필드를 거부합니다', () => {
  const selected = chooseLodging(options(), 1, candidate())
  assert.equal(isChatOptions(selected), true)
  assert.equal(isLodgingOptions(null), false)
  for (const patch of [{ lodging_status: 'unknown' }, { lodging_stays: null },
    { lodging_status: null }, { lodging_status: 'not_needed' },
    { lodging_stays: [selected.lodging_stays[0], selected.lodging_stays[0]] },
    { lodging_stays: [{ ...selected.lodging_stays[0], night: true }] },
    { lodging_stays: [{ ...selected.lodging_stays[0], night: 3 }] },
    { lodging_stays: [{ ...selected.lodging_stays[0], accessible: true }] }]) {
    assert.equal(isChatOptions({ ...selected, ...patch }), false)
  }
})

test('조회 기준은 확인된 마지막 관광지이며 앞선 방문지나 원래 출발지로 대체하지 않습니다', () => {
  const good = { day: 1, items: [visit()] }
  assert.deepEqual(getLodgingSearchCenter(good), { latitude: 33.41, longitude: 126.51, name: '마지막 관광지' })
  for (const last of [visit({ verified: false }), visit({ latitude: null }), visit({ latitude: 37.5 })]) {
    assert.equal(getLodgingSearchCenter({ items: [visit(), last] }), null)
  }
  assert.equal(getLodgingSearchCenter({ items: [] }), null)
})

test('미정인 중간 날짜의 지도에 원래 출발지나 가짜 숙소를 넣지 않습니다', () => {
  const selected = { ...options(), lodging_status: 'undecided' }
  assert.deepEqual(getLodgingMapAnchors({ day: 2 }, selected), [])
  assert.deepEqual(getLodgingMapAnchors({ day: 1 }, selected).map((place) => place.id), ['origin'])
  assert.deepEqual(getLodgingMapAnchors({ day: 3 }, selected).map((place) => place.id), ['origin-return'])
})

test('지도에는 날짜별 숙소 출발·도착과 마지막 날 귀환을 구분합니다', () => {
  const full = chooseLodging(chooseLodging(options(), 1, candidate()), 2, { ...candidate('200'), latitude: 33.48 })
  const middle = getLodgingMapAnchors({ day: 2 }, full)
  assert.deepEqual(middle.map((place) => place.label), ['숙소 출발', '숙소 도착'])
  assert.deepEqual(middle.map((place) => place.latitude), [33.45, 33.48])
  const last = getLodgingMapAnchors({ day: 3 }, full)
  assert.deepEqual(last.map((place) => place.id), ['lodging-start', 'origin-return'])
})

test('숙박 불필요 조건과 귀환 미선택을 지도에 반영합니다', () => {
  assert.deepEqual(getLodgingMapAnchors({ day: 2 }, { ...options(), lodging_status: 'not_needed' })
    .map((place) => place.id), ['origin', 'origin-return'])
  assert.deepEqual(getLodgingMapAnchors({ day: 3 }, { ...options(), return_to_origin: false }), [])
  assert.equal(getLodgingMapAnchors(null, options()).length, 1)
})

test('같은 숙박일의 중복 선택은 지도 기준으로 임의 선택하지 않습니다', () => {
  const selected = chooseLodging(options(), 1, candidate())
  selected.lodging_stays.push({ ...selected.lodging_stays[0], name: '다른 숙소' })
  assert.equal(getLodgingStay(selected, 1), null)
})

test('선택 요청 문구는 숙박일을 지정하고 메시지 길이 제한을 지킵니다', () => {
  const selected = chooseLodging(options(), 1, { ...candidate(), name: '가'.repeat(200) })
  const message = getLodgingSelectionMessage(selected.lodging_stays[0])
  assert.match(message, /1일차/)
  assert.match(message, /다음 날/)
  assert.ok(message.length <= 1000)
})

function withStorage(callback) {
  const previous = globalThis.window
  const cache = new Map()
  globalThis.window = { sessionStorage: {
    getItem: (key) => cache.get(key) ?? null,
    setItem: (key, value) => cache.set(key, value),
  } }
  try { callback(cache) } finally {
    if (previous === undefined) delete globalThis.window
    else globalThis.window = previous
  }
}

test('새로고침 시 숙소 미정 상태와 숙박일별 선택을 복원합니다', () => withStorage(() => {
  const state = createEmptyChatSession()
  state.options = chooseLodging(options(), 1, candidate())
  assert.equal(saveChatSession(state), true)
  const restored = loadChatSession()
  assert.equal(restored.options.lodging_status, 'undecided')
  assert.equal(restored.options.lodging_stays[0].provider_place_id, '100')
}))

test('이전 저장 대화와 일정의 옵션에도 숙소 기본 필드를 보완합니다', () => withStorage((cache) => {
  const state = createEmptyChatSession()
  state.options = options()
  delete state.options.lodging_status
  delete state.options.lodging_stays
  state.planResult = { options: structuredClone(state.options), warnings: [],
    plan: { assumptions: [], days: [{ day: 1, items: [] }] } }
  cache.set('gachi-jeju.chat-session', JSON.stringify({ version: 1, state }))
  const restored = loadChatSession()
  assert.equal(restored.options.lodging_status, null)
  assert.deepEqual(restored.planResult.options.lodging_stays, [])
  assert.deepEqual(restored.options, restored.planResult.options)
}))
