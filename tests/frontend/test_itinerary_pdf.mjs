import test from 'node:test'
import assert from 'node:assert/strict'
import { requestItineraryPdf, savePdfFile } from '../../frontend/src/services/itineraryPdfApi.js'
import { createDefaultChatOptions } from '../../frontend/src/services/travelOptions.js'

function planResult() {
  return {
    options: { ...createDefaultChatOptions(), start_date: '2026-11-20', trip_days: 2, transport: 'driving' },
    plan: { assumptions: ['현장 확인 필요'], days: [
      { day: 1, items: [{ item_type: 'visit', title: '한라수목원', start_time: '09:00',
        end_time: '10:00', accessibility_notes: ['전체 동선 미확인'] }] },
      { day: 2, items: [] },
    ] },
    warnings: ['일부 정보 미확인'], transit_routes: [],
    map_routes: [{ paths: [[[126.5, 33.4]]] }], reply: '도우미 답변', meta: {},
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

function pdfResponse() {
  return new Response('%PDF-1.7\nmock document', { headers: { 'Content-Type': 'application/pdf' } })
}

test('전체 일차와 일정의 옵션을 보내고 대화·지도 좌표는 보내지 않습니다', async (t) => {
  const value = planResult()
  const before = structuredClone(value)
  install(t, 'fetch', async (url, options) => {
    assert.equal(url, '/api/itineraries/pdf')
    assert.equal(options.method, 'POST')
    const sent = JSON.parse(options.body)
    assert.deepEqual(Object.keys(sent).sort(), ['options', 'plan', 'transit_routes', 'warnings'])
    assert.equal(sent.plan.days.length, 2)
    assert.deepEqual(sent.options, before.options)
    return pdfResponse()
  })
  const file = await requestItineraryPdf(value)
  assert.equal(file.filename, '같이가는제주_2026-11-20_여행일정.pdf')
  assert.equal(file.blob.type, 'application/pdf')
  assert.deepEqual(value, before)
})

test('일정이 없으면 통신 전에 거부합니다', async (t) => {
  install(t, 'fetch', () => assert.fail('통신하면 안 됩니다.'))
  await assert.rejects(requestItineraryPdf(null), { code: 'invalid_request' })
})

test('이미 취소된 요청은 전송하지 않습니다', async (t) => {
  install(t, 'fetch', () => assert.fail('통신하면 안 됩니다.'))
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(requestItineraryPdf(planResult(), { signal: controller.signal }), { code: 'cancelled' })
})

test('PDF가 아닌 정상 응답을 파일로 반환하지 않습니다', async (t) => {
  install(t, 'fetch', async () => new Response('{}', { headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(requestItineraryPdf(planResult()), { code: 'invalid_response' })
})

test('PDF 헤더라도 잘못된 파일 내용과 빈 파일은 거부합니다', async (t) => {
  for (const body of ['', 'not a PDF']) {
    install(t, 'fetch', async () => new Response(body, { headers: { 'Content-Type': 'application/pdf' } }))
    await assert.rejects(requestItineraryPdf(planResult()), { code: 'invalid_response' })
  }
})

test('서버 오류 안내를 유지합니다', async (t) => {
  install(t, 'fetch', async () => new Response(JSON.stringify({
    error: { code: 'pdf_unavailable', message: 'PDF 글꼴이 준비되지 않았습니다.' },
  }), { status: 503, headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(requestItineraryPdf(planResult()), {
    code: 'server_error', message: 'PDF 글꼴이 준비되지 않았습니다.',
  })
})

test('서버 오류 본문이 JSON이 아니어도 오류를 안내합니다', async (t) => {
  install(t, 'fetch', async () => new Response('Bad Gateway', { status: 502 }))
  await assert.rejects(requestItineraryPdf(planResult()), { code: 'server_error' })
})

test('네트워크 실패를 구분합니다', async (t) => {
  install(t, 'fetch', async () => { throw new TypeError('network') })
  await assert.rejects(requestItineraryPdf(planResult()), { code: 'network_error' })
})

test('30초 후에는 전송이 취소에 반응하지 않아도 대기를 끝냅니다', async (t) => {
  let expire
  let timeoutMs
  install(t, 'setTimeout', (callback, ms) => { expire = callback; timeoutMs = ms; return 1 })
  install(t, 'clearTimeout', () => {})
  install(t, 'fetch', () => new Promise(() => {}))
  const pending = requestItineraryPdf(planResult())
  assert.equal(timeoutMs, 30_000)
  expire()
  await assert.rejects(pending, { code: 'timeout' })
})

test('본문 읽기가 취소에 반응하지 않아도 대기를 끝냅니다', async (t) => {
  const controller = new AbortController()
  install(t, 'fetch', async () => ({
    ok: true, headers: new Headers({ 'Content-Type': 'application/pdf' }),
    arrayBuffer: () => new Promise(() => {}),
  }))
  const pending = requestItineraryPdf(planResult(), { signal: controller.signal })
  await Promise.resolve()
  await Promise.resolve()
  controller.abort()
  await assert.rejects(pending, { code: 'cancelled' })
})

test('취소 후 늦게 온 응답을 결과로 반환하지 않습니다', async (t) => {
  let deliver
  install(t, 'fetch', () => new Promise((resolve) => { deliver = resolve }))
  const controller = new AbortController()
  const pending = requestItineraryPdf(planResult(), { signal: controller.signal })
  controller.abort()
  deliver(pdfResponse())
  await assert.rejects(pending, { code: 'cancelled' })
})

test('한 다운로드 취소가 다른 다운로드에 영향을 주지 않습니다', async (t) => {
  const deliveries = []
  install(t, 'fetch', () => new Promise((resolve) => { deliveries.push(resolve) }))
  const controller = new AbortController()
  const first = requestItineraryPdf(planResult(), { signal: controller.signal })
  const rejected = assert.rejects(first, { code: 'cancelled' })
  const second = requestItineraryPdf(planResult())
  controller.abort()
  deliveries[1](pdfResponse())
  await rejected
  assert.equal((await second).blob.type, 'application/pdf')
})

test('다운로드 링크를 실행하고 임시 주소를 해제합니다', (t) => {
  const originalCreate = URL.createObjectURL
  const originalRevoke = URL.revokeObjectURL
  let released
  let cleanup
  const link = { click() { this.clicked = true }, remove() { this.removed = true } }
  URL.createObjectURL = () => 'blob:test'
  URL.revokeObjectURL = (value) => { released = value }
  t.after(() => { URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke })
  install(t, 'document', {
    createElement: () => link,
    body: { append: (value) => assert.equal(value, link) },
  })
  install(t, 'setTimeout', (callback) => { cleanup = callback; return 1 })
  savePdfFile({ blob: new Blob(['%PDF-']), filename: '여행일정.pdf' })
  assert.equal(link.download, '여행일정.pdf')
  assert.equal(link.clicked, true)
  assert.equal(link.removed, true)
  cleanup()
  assert.equal(released, 'blob:test')
})
