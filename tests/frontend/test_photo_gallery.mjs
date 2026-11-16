import test from 'node:test'
import assert from 'node:assert/strict'
import {
  PHOTO_PAGE_SIZE,
  getPhotoCategoryIds,
  getPhotoTitle,
  selectPhotoGallery,
} from '../../frontend/src/services/photoGallery.js'

const context = (spot_name, description = '', mapping_confirmed = true) => ({
  spot_name, description, mapping_confirmed,
})
const image = (id, contexts = [], caption = '', alt = '') => ({
  image_id: id, src: '/mock.webp', contexts, caption, alt,
})

test('연결이 확인된 시설·지점명으로 주제별 사진을 찾습니다', () => {
  for (const [name, category] of [
    ['제1주차장', 'parking'], ['주출입구(접근로)', 'entrance'],
    ['장애인화장실 정보', 'toilet'], ['경사로', 'slope'],
    ['관람 동선', 'path'], ['산책로', 'path'], ['문턱', 'slope'],
  ]) {
    assert.ok(getPhotoCategoryIds(image('a', [context(name)])).includes(category))
  }
})

test('설명에만 나온 시설은 사진 주제로 추정하지 않습니다', () => {
  assert.deepEqual(
    getPhotoCategoryIds(image('a', [
      context('관람동선', '주차장 옆에 화장실이 있습니다'),
    ])),
    ['path'],
  )
})

test('연결 미확인·시설명 없음·알 수 없는 시설은 미분류에 남깁니다', () => {
  for (const contexts of [
    [context('화장실', '', false)], [context('')], [context('전망대')], [],
  ]) {
    assert.deepEqual(getPhotoCategoryIds(image('a', contexts)), ['other'])
  }
})

test('여러 시설의 사진은 각 주제에 표시하고 한 분류에서는 중복 세지 않습니다', () => {
  const data = [
    image('a', [context('주차장'), context('주차장'), context('화장실')]),
  ]
  const result = selectPhotoGallery(data)
  assert.equal(result.totalCount, 1)
  assert.equal(result.categoryCounts.all, 1)
  assert.equal(result.categoryCounts.parking, 1)
  assert.equal(result.categoryCounts.toilet, 1)
  assert.equal(
    selectPhotoGallery(data, { category: 'toilet' }).matchingImages.length, 1,
  )
})

test('시설명·설명·기존 사진 설명과 대체 텍스트를 검색합니다', () => {
  const data = [
    image('a', [context('주차장', '장애인 주차 5대')]),
    image('b', [context('경사로', '주차장 옆 경사로')]),
    image('c', [], 'legacy caption', 'RAMP VIEW'),
  ]
  for (const [query, expected] of [
    ['장애인주차', ['a']],
    ['주차 경사로', ['b']],
    ['ramp', ['c']],
  ]) {
    assert.deepEqual(
      selectPhotoGallery(data, { query }).matchingImages.map((item) => item.image_id),
      expected,
    )
  }
})

test('검색 결과를 기준으로 분류별 건수를 표시합니다', () => {
  const data = [
    image('a', [context('주차장')]),
    image('b', [context('경사로', '주차장 옆')]),
    image('c', [context('화장실')]),
  ]
  const result = selectPhotoGallery(data, { query: '주차', category: 'slope' })
  assert.equal(result.totalCount, 3)
  assert.equal(result.categoryCounts.all, 2)
  assert.equal(result.categoryCounts.parking, 1)
  assert.equal(result.categoryCounts.slope, 1)
  assert.deepEqual(result.matchingImages.map((item) => item.image_id), ['b'])
})

test('사진은 원래 순서대로 16장씩 나눕니다', () => {
  const data = Array.from({ length: 35 }, (_, index) => image(String(index)))
  const first = selectPhotoGallery(data)
  const last = selectPhotoGallery(data, { page: 3 })
  assert.equal(PHOTO_PAGE_SIZE, 16)
  assert.equal(first.pageCount, 3)
  assert.deepEqual(
    first.pageImages.map((item) => item.image_id),
    data.slice(0, 16).map((item) => item.image_id),
  )
  assert.deepEqual(last.pageImages.map((item) => item.image_id), ['32', '33', '34'])
})

test('범위를 벗어난 페이지와 잘못된 분류를 안전하게 처리합니다', () => {
  const data = Array.from({ length: 35 }, (_, index) => image(String(index)))
  assert.equal(selectPhotoGallery(data, { page: 99 }).page, 3)
  for (const page of [-1, 0, 1.5, Infinity, '2']) {
    assert.equal(selectPhotoGallery(data, { page }).page, 1)
  }
  assert.equal(selectPhotoGallery(data, { category: 'missing' }).category, 'all')
})

test('빈 목록과 검색 결과 없음은 전체 사진 없음과 구분할 수 있습니다', () => {
  assert.equal(selectPhotoGallery(null).totalCount, 0)
  const result = selectPhotoGallery(
    [image('a', [context('주차장')])], { query: '화장실' },
  )
  assert.equal(result.totalCount, 1)
  assert.equal(result.matchingImages.length, 0)
  assert.equal(result.pageCount, 0)
  assert.equal(result.page, 1)
  assert.deepEqual(result.pageImages, [])
})

test('유니코드와 공백 차이는 검색에서만 정리하며 원문을 유지합니다', () => {
  const original = image('a', [context(' 경사로 ', '  원문\n설명 ')])
  const data = [original]
  const before = structuredClone(data)
  const result = selectPhotoGallery(data, { query: '경사로'.normalize('NFD') })
  assert.equal(result.matchingImages.length, 1)
  assert.deepEqual(data, before)
  assert.equal(result.pageImages[0].contexts[0].description, '  원문\n설명 ')
  assert.equal(getPhotoTitle(original), ' 경사로 ')
})

test('사진 제목은 지점명 원문을 보존하고 없는 경우 설명을 사용합니다', () => {
  assert.equal(
    getPhotoTitle(image('a', [
      context('입구'), context('입구'), context('화장실'),
    ])),
    '입구 · 화장실',
  )
  assert.equal(getPhotoTitle(image('a', [], '기존 설명')), '기존 설명')
  assert.equal(getPhotoTitle(image('a')), '시설·지점명 미등록')
})

test('이름이 비슷한 관람 시설을 관람동선으로 추정하지 않습니다', () => {
  assert.deepEqual(getPhotoCategoryIds(image('a', [context('관람차')])), ['other'])
  assert.deepEqual(getPhotoCategoryIds(image('b', [context('관람로')])), ['path'])
})