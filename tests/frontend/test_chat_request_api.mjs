import test from 'node:test'
import assert from 'node:assert/strict'
import { requestTravelChat } from '../../frontend/src/services/chatApi.js'
import { createDefaultChatOptions } from '../../frontend/src/services/travelOptions.js'

const ID_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const ID_B = 'bbbbbbbb-bbbb-4bbb-9bbb-bbbbbbbbbbbb'
const input = () => ({ message: '여행을 계획해 주세요.', options: createDefaultChatOptions() })
const sample = () => ({
  reply: '여행 날짜를 알려 주세요.',
  options: createDefaultChatOptions(),
  plan: null,
  warnings: [],
  meta: { model_call_count: 1, tool_call_counts: {}, elapsed_seconds: 1 },
})
const response = (data, status = 200) => ({
  ok: status >= 200 && status < 300, status, json: async () => data,
})
const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const flush = async () => {
  for (let index = 0; index < 6; index += 1) await Promise.resolve()
}

test('식당 검색 재시도에는 원래 식사 조건만 전송하고 기존 일정을 함께 보냅니다', async (t) => {
  const meal = '박물관 관람 후 12시에 근처 식당에서 점심을 먹도록 추가해줘.'
  const currentPlan = { assumptions: [], days: [{ day: 1, items: [] }] }
  const requests = []
  t.mock.method(globalThis, 'fetch', async (_, init) => {
    requests.push(JSON.parse(init.body))
    return response({ ...sample(), restaurant_retry_request: meal })
  })
  const first = await requestTravelChat({ ...input(), currentPlan })
  await requestTravelChat({ ...input(), currentPlan, message: first.restaurant_retry_request })
  assert.equal(requests[1].message, meal)
  assert.deepEqual(requests[1].current_plan, currentPlan)
})

function fakeTimers(t) {
  let nextId = 0
  const pending = new Map()
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => {
    const id = ++nextId
    pending.set(id, { callback, delay })
    return id
  })
  t.mock.method(globalThis, 'clearTimeout', (id) => pending.delete(id))
  return {
    pending,
    find(delay) {
      const entry = [...pending].find(([, value]) => value.delay === delay)
      assert.ok(entry, `timer missing: ${delay}`)
      return entry
    },
    fire(delay) {
      const [id, value] = this.find(delay)
      pending.delete(id)
      value.callback()
    },
  }
}

test('요청 식별자를 헤더에 넣고 기존 본문과 입력 데이터를 유지합니다', async (t) => {
  const value = input()
  value.history = [{ role: 'user', content: '  이전 요청  ', status: 'sent' }]
  value.currentPlan = { assumptions: [], days: [{ day: 1, items: [] }] }
  const before = structuredClone(value)
  const fetch = t.mock.method(globalThis, 'fetch', async (url, init) => {
    assert.equal(url, '/api/chat')
    assert.equal(init.method, 'POST')
    assert.equal(init.headers['X-Chat-Request-ID'], ID_A)
    assert.equal(init.signal.aborted, false)
    assert.deepEqual(JSON.parse(init.body), {
      message: value.message,
      history: [{ role: 'user', content: '이전 요청' }],
      options: value.options,
      current_plan: value.currentPlan,
    })
    return response(sample())
  })
  assert.equal((await requestTravelChat(value, { requestId: ID_A })).reply, sample().reply)
  assert.deepEqual(value, before)
  assert.equal(fetch.mock.callCount(), 1)
})

test('식별자를 생략하면 호출마다 새로운 UUID4를 사용합니다', async (t) => {
  const ids = []
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    ids.push(init.headers['X-Chat-Request-ID'])
    return response(sample())
  })
  await requestTravelChat(input())
  await requestTravelChat(input())
  for (const id of ids) {
    assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  }
  assert.notEqual(ids[0], ids[1])
})

test('잘못된 식별자와 취소 신호는 통신 전에 거부합니다', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch')
  for (const requestId of [null, '', 'invalid', ID_A.toUpperCase(), 1]) {
    await assert.rejects(requestTravelChat(input(), { requestId }), { code: 'invalid_request' })
  }
  await assert.rejects(requestTravelChat(input(), {
    requestId: ID_A, signal: { aborted: false },
  }), { code: 'invalid_request' })
  assert.equal(fetch.mock.callCount(), 0)
})

test('메시지와 대화 기록의 기존 입력 검사를 유지합니다', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch')
  for (const value of [
    { message: '' }, { message: '가'.repeat(1001) },
    { message: '요청', history: null },
    { message: '요청', history: [{ role: 'system', content: '요청' }] },
    { message: '요청', options: [] },
  ]) {
    await assert.rejects(requestTravelChat(value), { code: 'invalid_request' })
  }
  assert.equal(fetch.mock.callCount(), 0)
})

test('서버의 제한 시간·취소·혼잡 오류와 상세 정보를 유지합니다', async (t) => {
  let status
  let code
  const fetch = t.mock.method(globalThis, 'fetch', async () => response({
    error: { code, message: '서버 안내', details: [
      { field: 'options', reason: '확인 필요' }, { field: 'invalid' },
    ] },
  }, status))
  for (const item of [[504, 'request_timeout'], [409, 'request_cancelled'], [503, 'chat_busy']]) {
    status = item[0]
    code = item[1]
    await assert.rejects(requestTravelChat(input()), {
      code, status, message: '서버 안내',
      details: [{ field: 'options', reason: '확인 필요' }],
    })
  }
  assert.equal(fetch.mock.callCount(), 3)
})

test('JSON 오류와 네트워크 오류를 구분합니다', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => ({
    ok: true, status: 200, json: async () => { throw new SyntaxError('test-only') },
  }))
  await assert.rejects(requestTravelChat(input()), { code: 'invalid_response', status: 200 })
  fetch.mock.mockImplementation(async () => { throw new TypeError('test-only') })
  await assert.rejects(requestTravelChat(input()), { code: 'network_error' })
})

test('잘못된 정상 응답을 결과로 반환하지 않습니다', async (t) => {
  let payload
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  for (const value of [null, { ...sample(), reply: '' }, { ...sample(), options: {} }, { ...sample(), plan: {} }]) {
    payload = value
    await assert.rejects(requestTravelChat(input()), { code: 'invalid_response' })
  }
})

test('요청 전에 취소됐으면 생성과 취소 API를 모두 호출하지 않습니다', async (t) => {
  const controller = new AbortController()
  controller.abort()
  const fetch = t.mock.method(globalThis, 'fetch')
  await assert.rejects(requestTravelChat(input(), {
    requestId: ID_A, signal: controller.signal,
  }), { name: 'ChatApiError', code: 'request_cancelled' })
  assert.equal(fetch.mock.callCount(), 0)
})

test('생성 응답이 오지 않아도 취소하면 즉시 종료하고 별도 신호로 취소를 통지합니다', { timeout: 1000 }, async (t) => {
  const controller = new AbortController()
  const wait = deferred()
  const calls = []
  t.mock.method(globalThis, 'fetch', (url, init) => {
    calls.push({ url, init })
    return url === '/api/chat' ? wait.promise : Promise.resolve(response({}))
  })
  const pending = requestTravelChat(input(), { requestId: ID_A, signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, { code: 'request_cancelled' })
  await flush()
  assert.equal(calls.length, 2)
  assert.equal(calls[0].init.signal.aborted, true)
  assert.equal(calls[1].url, '/api/chat/cancel')
  assert.deepEqual(JSON.parse(calls[1].init.body), { request_id: ID_A })
  assert.equal(calls[1].init.keepalive, true)
  assert.equal(calls[1].init.signal.aborted, false)
})

test('취소 후 늦게 도착한 생성 응답을 반환하지 않습니다', { timeout: 1000 }, async (t) => {
  const controller = new AbortController()
  const wait = deferred()
  let accepted = false
  const fetch = t.mock.method(globalThis, 'fetch', (url) => (
    url === '/api/chat' ? wait.promise : Promise.resolve(response({}))
  ))
  const pending = requestTravelChat(input(), { requestId: ID_A, signal: controller.signal })
  pending.then(() => { accepted = true }, () => {})
  controller.abort()
  controller.abort()
  await assert.rejects(pending, { code: 'request_cancelled' })
  wait.resolve(response(sample()))
  await flush()
  assert.equal(accepted, false)
  assert.equal(fetch.mock.callCount(), 2)
})

test('본문을 읽는 중 취소되면 정상 JSON이어도 반환하지 않습니다', async (t) => {
  const controller = new AbortController()
  t.mock.method(globalThis, 'fetch', async (url) => (
    url === '/api/chat' ? {
      ok: true, status: 200, json: async () => {
        controller.abort()
        return sample()
      },
    } : response({})
  ))
  await assert.rejects(requestTravelChat(input(), {
    requestId: ID_A, signal: controller.signal,
  }), { code: 'request_cancelled' })
  await flush()
})

test('본문 읽기가 취소에 반응하지 않아도 대기를 종료합니다', { timeout: 1000 }, async (t) => {
  const controller = new AbortController()
  const body = deferred()
  const entered = deferred()
  t.mock.method(globalThis, 'fetch', async (url) => (
    url === '/api/chat' ? {
      ok: true, status: 200, json: () => { entered.resolve(); return body.promise },
    } : response({})
  ))
  const pending = requestTravelChat(input(), { requestId: ID_A, signal: controller.signal })
  await entered.promise
  controller.abort()
  await assert.rejects(pending, { code: 'request_cancelled' })
  body.resolve(sample())
  await flush()
})

test('180초 제한 시간은 사용자 취소와 구분하고 서버에도 중단을 통지합니다', { timeout: 1000 }, async (t) => {
  const timers = fakeTimers(t)
  const controller = new AbortController()
  const wait = deferred()
  const calls = []
  t.mock.method(globalThis, 'fetch', (url) => {
    calls.push(url)
    return url === '/api/chat' ? wait.promise : Promise.resolve(response({}))
  })
  const pending = requestTravelChat(input(), { requestId: ID_A, signal: controller.signal })
  timers.fire(180_000)
  controller.abort()
  await assert.rejects(pending, { code: 'client_timeout' })
  await flush()
  assert.deepEqual(calls, ['/api/chat', '/api/chat/cancel'])
  assert.equal(timers.pending.size, 0)
})

test('사용자가 먼저 취소했으면 뒤늦은 제한 시간으로 사유를 바꾸지 않습니다', { timeout: 1000 }, async (t) => {
  const timers = fakeTimers(t)
  const controller = new AbortController()
  t.mock.method(globalThis, 'fetch', (url) => (
    url === '/api/chat' ? deferred().promise : Promise.resolve(response({}))
  ))
  const pending = requestTravelChat(input(), { requestId: ID_A, signal: controller.signal })
  const [, timeout] = timers.find(180_000)
  controller.abort()
  timeout.callback()
  await assert.rejects(pending, { code: 'request_cancelled' })
  await flush()
  assert.equal(timers.pending.size, 0)
})

test('완료 후에는 타이머와 호출자 신호로 취소 통지를 보내지 않습니다', async (t) => {
  const timers = fakeTimers(t)
  const controller = new AbortController()
  const fetch = t.mock.method(globalThis, 'fetch', async () => response(sample()))
  const pending = requestTravelChat(input(), { requestId: ID_A, signal: controller.signal })
  const [, timeout] = timers.find(180_000)
  await pending
  controller.abort()
  timeout.callback()
  await flush()
  assert.equal(timers.pending.size, 0)
  assert.equal(fetch.mock.callCount(), 1)
})

test('취소 통지의 네트워크 실패가 사용자 취소 사유를 덮어쓰지 않습니다', { timeout: 1000 }, async (t) => {
  const controller = new AbortController()
  t.mock.method(globalThis, 'fetch', (url) => (
    url === '/api/chat' ? deferred().promise : Promise.reject(new TypeError('test-only'))
  ))
  const pending = requestTravelChat(input(), { requestId: ID_A, signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, { code: 'request_cancelled' })
  await flush()
})

test('취소 통지 자체도 5초 후 중단합니다', { timeout: 1000 }, async (t) => {
  const timers = fakeTimers(t)
  const controller = new AbortController()
  let cancellationSignal
  t.mock.method(globalThis, 'fetch', (url, init) => {
    if (url === '/api/chat') return deferred().promise
    cancellationSignal = init.signal
    return new Promise((_, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true })
    })
  })
  const pending = requestTravelChat(input(), { requestId: ID_A, signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, { code: 'request_cancelled' })
  timers.fire(5_000)
  await flush()
  assert.equal(cancellationSignal.aborted, true)
  assert.equal(timers.pending.size, 0)
})

test('한 요청을 취소해도 다른 요청의 신호와 응답에는 영향을 주지 않습니다', { timeout: 1000 }, async (t) => {
  const controllerA = new AbortController()
  const controllerB = new AbortController()
  const waitA = deferred()
  const waitB = deferred()
  const signals = new Map()
  t.mock.method(globalThis, 'fetch', (url, init) => {
    if (url === '/api/chat/cancel') {
      assert.deepEqual(JSON.parse(init.body), { request_id: ID_A })
      return Promise.resolve(response({}))
    }
    const id = init.headers['X-Chat-Request-ID']
    signals.set(id, init.signal)
    return id === ID_A ? waitA.promise : waitB.promise
  })
  const first = requestTravelChat(input(), { requestId: ID_A, signal: controllerA.signal })
  const second = requestTravelChat(input(), { requestId: ID_B, signal: controllerB.signal })
  controllerA.abort()
  await assert.rejects(first, { code: 'request_cancelled' })
  assert.equal(signals.get(ID_B).aborted, false)
  waitB.resolve(response(sample()))
  assert.equal((await second).reply, sample().reply)
  waitA.resolve(response(sample()))
  await flush()
})
