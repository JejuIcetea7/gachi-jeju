import test from 'node:test'
import assert from 'node:assert/strict'
import { requestPlaceImages } from '../../frontend/src/services/placeImagesApi.js'

const photoId = 'a'.repeat(64)
const empty = { place_id: 'P001', name: '한림공원', images: [] }

const response = (data, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => data,
})

test('사진이 없는 정상 응답을 유지합니다', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url) => {
    assert.equal(url, '/api/places/P001/images')
    return response(empty)
  })

  assert.deepEqual(await requestPlaceImages('P001'), empty)
})

test('사진 주소는 같은 서버의 사진 API로 구성합니다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => response({
    ...empty,
    images: [{
      image_id: photoId,
      url: 'https://example.invalid/other-image.jpg',
      caption: '입구',
      alt: '한림공원 입구',
    }],
  }))

  const result = await requestPlaceImages('P001')
  assert.equal(result.images[0].src, `/api/places/P001/images/${photoId}`)
  assert.equal(result.images[0].caption, '입구')
})

test('서버의 오류 메시지와 상태를 전달합니다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => response({
    error: {
      code: 'place_not_found',
      message: '장소 정보를 찾지 못했습니다.',
    },
  }, 404))

  await assert.rejects(requestPlaceImages('P001'), {
    code: 'place_not_found',
    status: 404,
    message: '장소 정보를 찾지 못했습니다.',
  })
})

test('읽을 수 없는 JSON 응답을 거부합니다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => ({
    ok: true,
    status: 200,
    json: async () => { throw new SyntaxError('test') },
  }))

  await assert.rejects(requestPlaceImages('P001'), {
    code: 'invalid_response',
  })
})

test('다른 장소의 응답과 잘못된 사진 목록을 거부합니다', async (t) => {
  let payload
  t.mock.method(globalThis, 'fetch', async () => response(payload))

  for (const value of [
    { ...empty, place_id: 'P002' },
    { ...empty, images: null },
    { ...empty, images: [{ image_id: '../private' }] },
  ]) {
    payload = value
    await assert.rejects(requestPlaceImages('P001'), {
      code: 'invalid_response',
    })
  }
})

test('네트워크 오류를 안내용 오류로 바꿉니다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => {
    throw new TypeError('test network failure')
  })

  await assert.rejects(requestPlaceImages('P001'), {
    code: 'network_error',
  })
})

test('호출자가 취소하면 요청도 취소됩니다', async (t) => {
  const controller = new AbortController()

  t.mock.method(globalThis, 'fetch', (_url, { signal }) => (
    new Promise((_resolve, reject) => {
      signal.addEventListener(
        'abort',
        () => reject(signal.reason),
        { once: true },
      )
    })
  ))

  const pending = requestPlaceImages('P001', {
    signal: controller.signal,
  })
  controller.abort()

  await assert.rejects(pending, { name: 'AbortError' })
})

test('시간 초과를 네트워크 오류와 구분합니다', async (t) => {
  const controller = new AbortController()
  controller.abort(new DOMException('test timeout', 'TimeoutError'))

  t.mock.method(AbortSignal, 'timeout', () => controller.signal)
  t.mock.method(globalThis, 'fetch', async (_url, { signal }) => {
    signal.throwIfAborted()
  })

  await assert.rejects(requestPlaceImages('P001'), {
    code: 'client_timeout',
  })
})