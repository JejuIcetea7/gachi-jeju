import test from 'node:test'
import assert from 'node:assert/strict'
import { requestNearbyLodgings } from '../../frontend/src/services/lodgingsApi.js'

const center = { latitude: 33.41, longitude: 126.51 }
const sample = () => ({ source: 'kakao_local', requested_at: '2026-11-13T05:00:00+00:00',
  center: { ...center }, radius_m: 5000, places: [{ provider_place_id: '100', name: '숙소 후보',
    address: '제주시 테스트 도로 1', phone: '064-000-0000', latitude: 33.42, longitude: 126.52,
    distance_m: 1500, place_url: 'https://place.map.kakao.com/100' }] })
const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data })

test('같은 서버에서 요청한 위치·반경·개수로 검색하고 공개 필드만 복사합니다', async (t) => {
  const payload = sample()
  payload.api_key = 'private'
  payload.places[0].accessible = true
  payload.places[0].available = true
  const before = structuredClone(payload)
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    assert.equal(url, '/api/lodgings/nearby?latitude=33.41&longitude=126.51&radius_m=5000&max_count=5')
    assert.equal(init.signal.aborted, false)
    return response(payload)
  })
  const result = await requestNearbyLodgings(center)
  assert.equal(result.places[0].name, '숙소 후보')
  assert.equal(Object.hasOwn(result, 'api_key'), false)
  assert.equal(Object.hasOwn(result.places[0], 'accessible'), false)
  assert.equal(Object.hasOwn(result.places[0], 'available'), false)
  result.places[0].name = '변경'
  assert.deepEqual(payload, before)
})

test('숙소가 없는 정상 응답에서도 조회 시각과 검색 위치를 유지합니다', async (t) => {
  const payload = { ...sample(), places: [] }
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  assert.deepEqual(await requestNearbyLodgings(center), payload)
})

test('잘못된 위치·반경·개수는 요청 전에 거부합니다', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch')
  for (const patch of [{ latitude: NaN }, { latitude: true }, { latitude: 37.5 },
    { longitude: null }, { radiusM: 0 }, { radiusM: 20001 }, { radiusM: true },
    { maxCount: 0 }, { maxCount: 11 }]) {
    await assert.rejects(requestNearbyLodgings({ ...center, ...patch }), { code: 'invalid_request' })
  }
  assert.equal(fetch.mock.callCount(), 0)
})

test('다른 검색 위치나 반경의 응답을 현재 후보로 사용하지 않습니다', async (t) => {
  let payload
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  for (const patch of [{ center: { ...center, latitude: 33.5 } }, { radius_m: 2000 }]) {
    payload = { ...sample(), ...patch }
    await assert.rejects(requestNearbyLodgings(center), { code: 'invalid_response' })
  }
})

test('중복 식별값과 요청 개수를 초과한 후보를 거부합니다', async (t) => {
  let payload = sample()
  payload.places.push({ ...payload.places[0] })
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  await assert.rejects(requestNearbyLodgings(center), { code: 'invalid_response' })
  payload = sample()
  payload.places.push({ ...payload.places[0], provider_place_id: '101', place_url: 'https://place.map.kakao.com/101' })
  await assert.rejects(requestNearbyLodgings({ ...center, maxCount: 1 }), { code: 'invalid_response' })
})

test('잘못된 좌표·이름·거리·지도 링크를 표시 대상으로 사용하지 않습니다', async (t) => {
  let payload
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  for (const patch of [{ latitude: NaN }, { latitude: 37.5 }, { name: ' ' }, { address: null },
    { phone: null }, { distance_m: -1 }, { distance_m: true }, { distance_m: 6000 },
    { provider_place_id: 'P001' }, { place_url: 'javascript:invalid' },
    { place_url: 'https://place.map.kakao.com/101' }]) {
    payload = sample()
    payload.places[0] = { ...payload.places[0], ...patch }
    await assert.rejects(requestNearbyLodgings(center), { code: 'invalid_response' })
  }
})

test('조회 시각에는 시간대가 있어야 하며 알 수 없는 자료 제공자를 거부합니다', async (t) => {
  let payload
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  for (const patch of [{ requested_at: 'invalid' }, { requested_at: '2026-11-13T05:00:00' }, { source: 'unknown' }]) {
    payload = { ...sample(), ...patch }
    await assert.rejects(requestNearbyLodgings(center), { code: 'invalid_response' })
  }
})

test('서버 오류 코드·상태·메시지를 전달하고 빈 메시지는 대체합니다', async (t) => {
  let payload = { error: { code: 'lodging_search_rate_limited', message: '잠시 후 다시 조회해 주세요.' } }
  const fetch = t.mock.method(globalThis, 'fetch', async () => response(payload, 429))
  await assert.rejects(requestNearbyLodgings(center), { code: 'lodging_search_rate_limited', status: 429 })
  payload = { error: { message: ' ' } }
  fetch.mock.mockImplementation(async () => response(payload, 503))
  await assert.rejects(requestNearbyLodgings(center), { code: 'api_error', status: 503, message: '주변 숙소를 조회하지 못했습니다.' })
})

test('JSON 오류와 네트워크 오류를 구분합니다', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => ({
    ok: true, status: 200, json: async () => { throw new SyntaxError('invalid') },
  }))
  await assert.rejects(requestNearbyLodgings(center), { code: 'invalid_response' })
  fetch.mock.mockImplementation(async () => { throw new TypeError('offline') })
  await assert.rejects(requestNearbyLodgings(center), { code: 'network_error' })
})

test('조회 전 취소되면 서버를 호출하지 않습니다', async (t) => {
  const controller = new AbortController()
  const reason = new Error('닫힘')
  controller.abort(reason)
  const fetch = t.mock.method(globalThis, 'fetch')
  await assert.rejects(requestNearbyLodgings({ ...center, signal: controller.signal }), (error) => error === reason)
  assert.equal(fetch.mock.callCount(), 0)
})

test('조회 중 취소하면 호출자가 지정한 취소 사유를 유지합니다', async (t) => {
  const controller = new AbortController()
  const reason = new Error('날짜 변경')
  t.mock.method(globalThis, 'fetch', async () => {
    controller.abort(reason)
    throw new TypeError('aborted')
  })
  await assert.rejects(requestNearbyLodgings({ ...center, signal: controller.signal }), (error) => error === reason)
})

test('본문을 읽던 중 취소된 응답은 후보로 반환하지 않습니다', async (t) => {
  const controller = new AbortController()
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, status: 200, json: async () => {
    controller.abort()
    return sample()
  } }))
  await assert.rejects(requestNearbyLodgings({ ...center, signal: controller.signal }), { name: 'AbortError' })
})

test('조회 시간 초과를 네트워크 오류와 구분합니다', async (t) => {
  t.mock.method(AbortSignal, 'timeout', () => AbortSignal.abort(new DOMException('timeout', 'TimeoutError')))
  t.mock.method(globalThis, 'fetch', async (url, { signal }) => { signal.throwIfAborted() })
  await assert.rejects(requestNearbyLodgings(center), { code: 'client_timeout' })
})
