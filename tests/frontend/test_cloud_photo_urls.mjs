import test from 'node:test'
import assert from 'node:assert/strict'
import { requestPlaceImages } from '../../frontend/src/services/placeImagesApi.js'

const url = 'https://dexample123.cloudfront.net/photos_web/P001/P001_0001.webp'
const image = {
  image_id: 'a'.repeat(64), photo_id: 'P001_0001', url,
  caption: '입구', alt: '한림공원 입구',
  contexts: [{ spot_name: '입구', description: '  원문\n설명입니다.  ', mapping_confirmed: true }],
}
const response = (data) => ({ ok: true, status: 200, json: async () => data })
const body = (value) => ({ place_id: 'P002', name: '연결된 장소', images: [value] })

test('공유 사진은 대표 장소 폴더의 CloudFront 주소와 해당 장소의 원문을 유지합니다', async (t) => {
  t.mock.method(globalThis, 'fetch', async (endpoint) => {
    assert.equal(endpoint, '/api/places/P002/images')
    return response(body(image))
  })
  const result = await requestPlaceImages('P002')
  assert.equal(result.images[0].src, url)
  assert.equal(result.images[0].photo_id, 'P001_0001')
  assert.equal(result.images[0].contexts[0].description, '  원문\n설명입니다.  ')
})

test('HTTP·다른 호스트·잘못된 사진 경로와 인증·쿼리 정보가 있는 주소를 거부합니다', async (t) => {
  let payload
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  for (const bad of [
    url.replace('https:', 'http:'), url.replace('dexample123.cloudfront.net', 'evil.test'),
    url.replace('P001_0001.webp', 'P001_0002.webp'), `${url}?token=private`, `${url}#fragment`,
    url.replace('https://', 'https://user@'), url.replace('.net/', '.net:443/'),
    url.replace('P001/P001', 'P002/P001'), url.replace('https://', '//'),
    'https://[', url.replace('.net/', '.net.evil.test/'),
  ]) {
    payload = body({ ...image, url: bad })
    await assert.rejects(requestPlaceImages('P002'), { code: 'invalid_response' })
  }
})

test('장소 연결 미확인과 빈 설명을 확인된 정보로 바꾸지 않습니다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => response(body({
    ...image, contexts: [{ spot_name: '', description: '', mapping_confirmed: false }],
  })))
  const result = await requestPlaceImages('P002')
  assert.deepEqual(result.images[0].contexts, [{ spot_name: '', description: '', mapping_confirmed: false }])
})

test('사진 ID와 출처 설명·확인 상태가 잘못된 CloudFront 응답을 거부합니다', async (t) => {
  let payload
  t.mock.method(globalThis, 'fetch', async () => response(payload))
  for (const change of [
    { photo_id: '../P001_0001' }, { photo_id: 1 }, { contexts: undefined }, { contexts: [] },
    { contexts: [{ ...image.contexts[0], mapping_confirmed: 'true' }] },
    { contexts: [{ ...image.contexts[0], description: null }] },
  ]) {
    payload = body({ ...image, ...change })
    await assert.rejects(requestPlaceImages('P002'), { code: 'invalid_response' })
  }
})

test('중복된 사진 ID로 서로 다른 설명이 덮이지 않도록 응답을 거부합니다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => response({ ...body(image), images: [image, { ...image }] }))
  await assert.rejects(requestPlaceImages('P002'), { code: 'invalid_response' })
})

test('화면에서 설명을 수정해도 원본 응답의 장소별 설명은 변경되지 않습니다', async (t) => {
  const original = structuredClone(image)
  t.mock.method(globalThis, 'fetch', async () => response(body(original)))
  const result = await requestPlaceImages('P002')
  result.images[0].contexts[0].description = 'changed by caller'
  assert.equal(original.contexts[0].description, image.contexts[0].description)
})

test('요청 전에 취소되면 사진 목록을 조회하지 않습니다', async (t) => {
  const controller = new AbortController()
  controller.abort()
  const fetch = t.mock.method(globalThis, 'fetch', async () => response(body(image)))
  await assert.rejects(requestPlaceImages('P002', { signal: controller.signal }), { name: 'AbortError' })
  assert.equal(fetch.mock.callCount(), 0)
})
