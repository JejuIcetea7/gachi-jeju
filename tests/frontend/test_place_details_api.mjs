import test from 'node:test'
import assert from 'node:assert/strict'
import { requestPlaceDetails } from '../../frontend/src/services/placeDetailsApi.js'

const sample = () => ({
  place_id: 'P166',
  name: '퍼시픽마리나요트공원',
  category: '공원',
  closed_days: '확인 안 됨',
  spots: [{ name: '출입구', description: '동선 설명 원문' }],
  accessibility_facts: [
    { section: '주차장', facility_name: '퍼시픽리솜 2주차장', description: '주차면수', raw_value: '5대' },
    { section: '주차장', facility_name: '퍼시픽리솜 2주차장', description: '보행안전통로', raw_value: '미설치' },
    { section: '추천 대상', facility_name: '', description: '수동 휠체어', raw_value: '● 동행인 / 보호자 동반 추천' },
  ],
})
const response = (data, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => data,
})

test('같은 서버에서 조회하고 시설·추천 원문을 변경하지 않습니다', async (t) => {
  const payload = sample()
  payload.accessibility_facts[0].raw_value = ' 5대 '
  payload.source_file = 'private'
  payload.spots[0].source_file = 'private'
  payload.accessibility_facts[0].source_file = 'private'
  const before = structuredClone(payload)
  t.mock.method(globalThis, 'fetch', async (url) => {
    assert.equal(url, '/api/places/P166/details')
    return response(payload)
  })

  const result = await requestPlaceDetails(' P166 ')
  assert.equal(result.accessibility_facts[0].raw_value, ' 5대 ')
  assert.equal(result.accessibility_facts[1].raw_value, '미설치')
  assert.equal(result.accessibility_facts[2].raw_value, '● 동행인 / 보호자 동반 추천')
  assert.equal(result.closed_days, '확인 안 됨')
  assert.equal(Object.hasOwn(result, 'source_file'), false)
  assert.equal(Object.hasOwn(result.spots[0], 'source_file'), false)
  assert.equal(Object.hasOwn(result.accessibility_facts[0], 'source_file'), false)
  result.accessibility_facts[0].raw_value = '수정'
  assert.deepEqual(payload, before)
})

test('비어 있는 원문 목록과 알 수 없는 이름을 임의로 채우지 않습니다', async (t) => {
  const payload = { ...sample(), category: null, spots: [{ name: null, description: '설명' }], accessibility_facts: [] }
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  assert.deepEqual(await requestPlaceDetails('P166'), payload)
})

test('잘못된 장소 ID는 요청 전에 거부합니다', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => response(sample()))
  for (const value of [undefined, null, 166, '', ' ', 'P'.repeat(81)]) {
    await assert.rejects(requestPlaceDetails(value), { code: 'invalid_request' })
  }
  assert.equal(fetch.mock.callCount(), 0)
})

test('장소 ID를 URL 경로에 맞게 인코딩합니다', async (t) => {
  const id = '제주 장소?'
  t.mock.method(globalThis, 'fetch', async (url) => {
    assert.equal(url, `/api/places/${encodeURIComponent(id)}/details`)
    return response({ ...sample(), place_id: id })
  })
  assert.equal((await requestPlaceDetails(id)).place_id, id)
})

test('다른 장소의 응답과 잘못된 필드 형식을 거부합니다', async (t) => {
  let payload
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  for (const value of [
    null,
    { ...sample(), place_id: 'P001' },
    { ...sample(), name: ' ' },
    { ...sample(), category: 3 },
    { ...sample(), closed_days: null },
    { ...sample(), spots: null },
    { ...sample(), spots: [null] },
    { ...sample(), spots: [{ name: 3, description: '설명' }] },
    { ...sample(), spots: [{ name: null }] },
    { ...sample(), accessibility_facts: {} },
    { ...sample(), accessibility_facts: [null] },
    { ...sample(), accessibility_facts: [{ section: null, facility_name: null, description: null, raw_value: '5대' }] },
    { ...sample(), accessibility_facts: [{ section: null, facility_name: '', description: null, raw_value: 5 }] },
    { ...sample(), accessibility_facts: [{ section: '주차장', facility_name: '', raw_value: '미설치' }] },
  ]) {
    payload = value
    await assert.rejects(requestPlaceDetails('P166'), { code: 'invalid_response' })
  }
})

test('알 수 없는 항목 이름은 null과 빈 문자열을 보존합니다', async (t) => {
  const payload = { ...sample(), accessibility_facts: [{ section: null, facility_name: '', description: null, raw_value: '●' }] }
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  assert.deepEqual((await requestPlaceDetails('P166')).accessibility_facts, payload.accessibility_facts)
})

test('서버의 오류 상태와 메시지를 전달하고 빈 메시지는 대체합니다', async (t) => {
  let payload = { error: { code: 'place_not_found', message: '장소 정보를 찾지 못했습니다.' } }
  let status = 404
  t.mock.method(globalThis, 'fetch', async () => response(payload, status))
  await assert.rejects(requestPlaceDetails('P166'), {
    code: 'place_not_found', status: 404, message: '장소 정보를 찾지 못했습니다.',
  })
  payload = { error: { message: ' ' } }
  status = 500
  await assert.rejects(requestPlaceDetails('P166'), {
    code: 'api_error', status: 500, message: '장소 상세 정보를 불러오지 못했습니다.',
  })
})

test('JSON 읽기 실패와 네트워크 실패를 구분합니다', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => ({
    ok: true, status: 200, json: async () => { throw new SyntaxError('test') },
  }))
  await assert.rejects(requestPlaceDetails('P166'), { code: 'invalid_response' })
  fetch.mock.mockImplementation(async () => { throw new TypeError('test network failure') })
  await assert.rejects(requestPlaceDetails('P166'), { code: 'network_error' })
})

test('요청 전에 취소됐으면 서버를 호출하지 않습니다', async (t) => {
  const controller = new AbortController()
  controller.abort()
  const fetch = t.mock.method(globalThis, 'fetch', async () => response(sample()))
  await assert.rejects(requestPlaceDetails('P166', { signal: controller.signal }), { name: 'AbortError' })
  assert.equal(fetch.mock.callCount(), 0)
})

test('요청 도중 취소하면 호출자의 취소 사유를 유지합니다', async (t) => {
  const controller = new AbortController()
  t.mock.method(globalThis, 'fetch', (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true })
  }))
  const pending = requestPlaceDetails('P166', { signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, { name: 'AbortError' })
})

test('응답 본문을 읽는 도중의 취소도 네트워크 오류로 바꾸지 않습니다', async (t) => {
  const controller = new AbortController()
  t.mock.method(globalThis, 'fetch', async () => ({
    ok: true,
    status: 200,
    json: async () => {
      controller.abort()
      throw controller.signal.reason
    },
  }))
  await assert.rejects(requestPlaceDetails('P166', { signal: controller.signal }), { name: 'AbortError' })
})

test('시간 초과를 네트워크 오류와 구분합니다', async (t) => {
  const controller = new AbortController()
  controller.abort(new DOMException('test timeout', 'TimeoutError'))
  t.mock.method(AbortSignal, 'timeout', () => controller.signal)
  t.mock.method(globalThis, 'fetch', async (_url, { signal }) => { signal.throwIfAborted() })
  await assert.rejects(requestPlaceDetails('P166'), { code: 'client_timeout' })
})
