import test from 'node:test'
import assert from 'node:assert/strict'
import { requestItineraryEmail } from '../../frontend/src/services/itineraryEmailApi.js'
import { requestItineraryPdf } from '../../frontend/src/services/itineraryPdfApi.js'
import { createDefaultChatOptions } from '../../frontend/src/services/travelOptions.js'

function planResult() {
  return {
    options: {
      ...createDefaultChatOptions(),
      start_date: '2026-11-20',
      trip_days: 2,
      transport: 'driving',
    },
    plan: {
      assumptions: ['현장 확인 필요'],
      days: [
        {
          day: 1,
          items: [{
            item_type: 'visit',
            title: '한라수목원',
            start_time: '09:00',
            end_time: '10:00',
            accessibility_notes: ['전체 동선 미확인'],
          }],
        },
        { day: 2, items: [] },
      ],
    },
    warnings: ['일부 정보 미확인'],
    transit_routes: [],
    map_routes: [{ paths: [[[126.5, 33.4]]] }],
    reply: '도우미 답변',
    meta: {},
  }
}

function install(t, name, value) {
  const original = globalThis[name]
  globalThis[name] = value
  t.after(() => {
    if (original === undefined) delete globalThis[name]
    else globalThis[name] = original
  })
}

function accepted(duplicate = false) {
  return new Response(JSON.stringify({
    status: 'accepted',
    duplicate,
    message: '메일 서버가 접수했습니다.',
  }), {
    headers: { 'Content-Type': 'application/json' },
  })
}

test('다운로드와 같은 전체 일정에 수신자만 추가하고 원본을 유지합니다', async (t) => {
  const value = planResult()
  const before = structuredClone(value)
  let downloadBody
  let emailBody

  install(t, 'fetch', async (url, options) => {
    assert.equal(options.method, 'POST')
    if (url === '/api/itineraries/pdf') {
      downloadBody = JSON.parse(options.body)
      return new Response('%PDF-1.7\nfixture', {
        headers: { 'Content-Type': 'application/pdf' },
      })
    }
    assert.equal(url, '/api/itineraries/email')
    emailBody = JSON.parse(options.body)
    return accepted()
  })

  await requestItineraryPdf(value)
  await requestItineraryEmail(value, ' visitor@example.com ')
  assert.equal(emailBody.recipient, 'visitor@example.com')
  delete emailBody.recipient
  assert.deepEqual(emailBody, downloadBody)
  assert.equal(emailBody.plan.days.length, 2)
  assert.deepEqual(value, before)
})

test('새 접수와 기존 접수를 구분합니다', async (t) => {
  let duplicate = false
  install(t, 'fetch', async () => accepted(duplicate))

  assert.equal(
    (await requestItineraryEmail(
      planResult(), 'visitor@example.com',
    )).duplicate,
    false,
  )

  duplicate = true
  assert.equal(
    (await requestItineraryEmail(
      planResult(), 'visitor@example.com',
    )).duplicate,
    true,
  )
})

test('일정이 없으면 전송하지 않습니다', async (t) => {
  install(t, 'fetch', () => assert.fail('전송하면 안 됩니다.'))
  await assert.rejects(
    requestItineraryEmail(null, 'visitor@example.com'),
    { code: 'invalid_request' },
  )
})

test('잘못된 주소와 헤더 주입을 전송 전에 거부합니다', async (t) => {
  install(t, 'fetch', () => assert.fail('전송하면 안 됩니다.'))
  for (const address of [
    '',
    'bad',
    'a@example.com,b@example.com',
    'a@example.com\r\nBcc: b@example.com',
  ]) {
    await assert.rejects(
      requestItineraryEmail(planResult(), address),
      { code: 'invalid_recipient' },
    )
  }
})

test('요청 전에 중단됐으면 전송하지 않습니다', async (t) => {
  install(t, 'fetch', () => assert.fail('전송하면 안 됩니다.'))
  const controller = new AbortController()
  controller.abort()

  await assert.rejects(
    requestItineraryEmail(
      planResult(),
      'visitor@example.com',
      { signal: controller.signal },
    ),
    { code: 'cancelled', uncertain: false },
  )
})

test('미설정·처리 중·수신자 제한 등 서버 오류를 유지합니다', async (t) => {
  let failure
  install(t, 'fetch', async () => new Response(JSON.stringify({
    error: { code: failure.code, message: '서버 안내' },
  }), {
    status: failure.status,
    headers: { 'Content-Type': 'application/json' },
  }))

  for (const value of [
    { code: 'email_not_configured', status: 503 },
    { code: 'email_in_progress', status: 409 },
    { code: 'email_rate_limited', status: 429 },
    { code: 'email_rejected', status: 502 },
    { code: 'email_delivery_unknown', status: 502 },
  ]) {
    failure = value
    await assert.rejects(
      requestItineraryEmail(planResult(), 'visitor@example.com'),
      {
        code: value.code,
        status: value.status,
        uncertain: [
          'email_delivery_unknown', 'email_in_progress',
        ].includes(value.code),
      },
    )
  }
})

test('정상 응답의 접수 필드가 잘못됐으면 성공으로 판정하지 않습니다', async (t) => {
  let body
  install(t, 'fetch', async () => new Response(JSON.stringify(body)))

  for (const value of [
    {},
    { status: 'queued', duplicate: false, message: '처리 중' },
    { status: 'accepted', duplicate: 'false', message: '접수' },
    { status: 'accepted', duplicate: false, message: '' },
  ]) {
    body = value
    await assert.rejects(
      requestItineraryEmail(planResult(), 'visitor@example.com'),
      { code: 'email_delivery_unknown', uncertain: true },
    )
  }
})

test('202 응답을 SMTP 접수 완료로 해석하지 않습니다', async (t) => {
  install(t, 'fetch', async () => new Response(JSON.stringify({
    status: 'accepted',
    duplicate: false,
    message: '대기',
  }), { status: 202 }))

  await assert.rejects(
    requestItineraryEmail(planResult(), 'visitor@example.com'),
    { code: 'email_delivery_unknown', uncertain: true },
  )
})

test('JSON이 아닌 응답도 접수 여부 미확인으로 구분합니다', async (t) => {
  install(t, 'fetch', async () => new Response(
    'Bad Gateway', { status: 502 },
  ))
  await assert.rejects(
    requestItineraryEmail(planResult(), 'visitor@example.com'),
    { code: 'email_delivery_unknown', uncertain: true },
  )
})

test('네트워크 오류를 자동으로 다시 전송하지 않습니다', async (t) => {
  let calls = 0
  install(t, 'fetch', async () => {
    calls += 1
    throw new TypeError('network')
  })

  await assert.rejects(
    requestItineraryEmail(planResult(), 'visitor@example.com'),
    { code: 'email_delivery_unknown', uncertain: true },
  )
  assert.equal(calls, 1)
})

test('45초가 지나면 접수 여부 미확인으로 대기를 종료합니다', async (t) => {
  let expire
  let timeoutMs
  install(t, 'setTimeout', (callback, ms) => {
    expire = callback
    timeoutMs = ms
    return 1
  })
  install(t, 'clearTimeout', () => {})
  install(t, 'fetch', () => new Promise(() => {}))

  const pending = requestItineraryEmail(
    planResult(), 'visitor@example.com',
  )
  assert.equal(timeoutMs, 45_000)
  expire()
  await assert.rejects(
    pending, { code: 'email_delivery_unknown', uncertain: true },
  )
})

test('응답 본문 읽기가 중단에 반응하지 않아도 대기를 종료합니다', async (t) => {
  const controller = new AbortController()
  install(t, 'fetch', async () => ({
    ok: true,
    status: 200,
    json: () => new Promise(() => {}),
  }))

  const pending = requestItineraryEmail(
    planResult(),
    'visitor@example.com',
    { signal: controller.signal },
  )
  await Promise.resolve()
  controller.abort()

  await assert.rejects(
    pending, { code: 'email_delivery_unknown', uncertain: true },
  )
})

test('중단 후 늦은 응답을 성공 결과로 반영하지 않습니다', async (t) => {
  let deliver
  install(t, 'fetch', () => new Promise((resolve) => {
    deliver = resolve
  }))

  const controller = new AbortController()
  const pending = requestItineraryEmail(
    planResult(),
    'visitor@example.com',
    { signal: controller.signal },
  )
  controller.abort()
  deliver(accepted())

  await assert.rejects(
    pending, { code: 'email_delivery_unknown', uncertain: true },
  )
})

test('한 요청의 중단이 다른 요청의 결과를 바꾸지 않습니다', async (t) => {
  const responses = []
  install(t, 'fetch', () => new Promise((resolve) => {
    responses.push(resolve)
  }))

  const controller = new AbortController()
  const first = requestItineraryEmail(
    planResult(),
    'first@example.com',
    { signal: controller.signal },
  )
  const rejected = assert.rejects(
    first, { code: 'email_delivery_unknown' },
  )
  const second = requestItineraryEmail(
    planResult(), 'second@example.com',
  )

  controller.abort()
  responses[1](accepted())
  await rejected
  assert.equal((await second).status, 'accepted')
})

test('알 수 없는 서버 오류를 확정적인 미발송으로 해석하지 않습니다', async (t) => {
  install(t, 'fetch', async () => new Response(JSON.stringify({
    error: { code: 'internal_error', message: '서버 오류' },
  }), {
    status: 500,
    headers: { 'Content-Type': 'application/json' },
  }))

  await assert.rejects(
    requestItineraryEmail(planResult(), 'visitor@example.com'),
    { code: 'email_delivery_unknown', uncertain: true },
  )
})