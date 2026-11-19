import test from 'node:test'
import assert from 'node:assert/strict'
import { selectDetailDescriptions } from '../../frontend/src/services/placeDescriptionVisibility.js'

const spot = (name, description) => ({ name, description })
const fact = (name, label, value) => ({
  section: '시설 정보', facility_name: name, description: label, raw_value: value,
})
const context = (name, description, confirmed = true) => ({
  spot_name: name, description, mapping_confirmed: confirmed,
})
const photos = (...contexts) => ({
  status: 'ready', images: [{ image_id: 'photo-a', contexts }],
})
const details = (spots = [], facts = []) => ({ spots, accessibility_facts: facts })

test('같은 시설명과 설명이 사진에 있으면 사진 쪽에만 남깁니다', () => {
  const original = details([spot('주차장', '장애인주차 6대'), spot('동선', '입구 경사 10도')])
  const result = selectDetailDescriptions(original, photos(context('주차장', '장애인주차 6대')))
  assert.deepEqual(result.spots, [spot('동선', '입구 경사 10도')])
  assert.equal(result.hidden_spots, 1)
  assert.equal(result.hidden_facts, 0)
})

test('공백·줄바꿈과 한글 유니코드 표현 차이는 비교에서만 정리합니다', () => {
  const original = details([spot(' 주차장 ', '장애인주차\n  6대'), spot('동선', '  별도의 원문\n설명  ')])
  const result = selectDetailDescriptions(original, photos(context('주차장'.normalize('NFD'), '장애인주차 6대')))
  assert.deepEqual(result.spots, [original.spots[1]])
  assert.equal(result.spots[0].description, '  별도의 원문\n설명  ')
})

test('설명이 같아도 시설명이 다르거나 없으면 기존 정보를 유지합니다', () => {
  const original = details([spot('제2주차장', '장애인주차 6대'), spot(null, '장애인주차 6대'), spot('', '장애인주차 6대')])
  const result = selectDetailDescriptions(original, photos(context('제1주차장', '장애인주차 6대')))
  assert.deepEqual(result.spots, original.spots)
  assert.equal(result.hidden_spots, 0)
})

test('일부 문장만 같거나 계단·주차 대수가 다르면 숨기지 않습니다', () => {
  const original = details([
    spot('입구', '계단 있음'), spot('주차장', '장애인주차 6대'), spot('동선', '경사 10도. 우회로 확인 필요'),
  ])
  const result = selectDetailDescriptions(original, photos(
    context('입구', '계단 없음'), context('주차장', '장애인주차 5대'), context('동선', '경사 10도'),
  ))
  assert.deepEqual(result.spots, original.spots)
})

test('사진 조회 대기·실패·빈 목록에서는 설명을 숨기지 않습니다', () => {
  const original = details([spot('주차장', '장애인주차 6대')])
  for (const state of [
    { status: 'loading' }, { status: 'error' }, { status: 'empty' },
    { status: 'ready', images: [] }, { status: 'ready', images: null },
  ]) {
    assert.deepEqual(selectDetailDescriptions(original, state).spots, original.spots)
  }
})

test('장소 연결이 미확인이거나 사진 파일이 실패하면 기존 설명을 유지합니다', () => {
  const original = details([spot('주차장', '장애인주차 6대')])
  assert.deepEqual(selectDetailDescriptions(original, photos(context('주차장', '장애인주차 6대', false))).spots, original.spots)
  assert.deepEqual(selectDetailDescriptions(original, photos(context('주차장', '장애인주차 6대')), new Set(['photo-a'])).spots, original.spots)
})

test('같은 설명의 사진이 여러 장이면 정상인 사진 한 장이 있어도 중복 설명을 정리합니다', () => {
  const original = details([spot('주차장', '장애인주차 6대')])
  const state = { status: 'ready', images: [
    { image_id: 'failed', contexts: [context('주차장', '장애인주차 6대')] },
    { image_id: 'working', contexts: [context('주차장', '장애인주차 6대')] },
  ] }
  const result = selectDetailDescriptions(original, state, new Set(['failed']))
  assert.equal(result.hidden_spots, 1)
})

test('시설 값은 조사 분류와 항목명까지 사진 설명과 같아야 정리합니다', () => {
  const original = details([], [fact('제1주차장', '안전통로', '미설치')])
  assert.deepEqual(selectDetailDescriptions(original, photos(context('제1주차장', '미설치'))).accessibility_facts, original.accessibility_facts)
  assert.deepEqual(selectDetailDescriptions(original, photos(context('제1주차장', '안전통로 미설치'))).accessibility_facts, original.accessibility_facts)
  assert.equal(selectDetailDescriptions(original, photos(context('제1주차장', '시설 정보 안전통로 미설치'))).hidden_facts, 1)
})

test('항목명 없는 시설 설명도 같은 시설의 전체 원문과 일치할 때만 정리합니다', () => {
  const original = details([], [
    { ...fact('주출입구', null, '계단 오른쪽에 경사로 있음'), section: null },
    fact('화장실', null, ''),
  ])
  const result = selectDetailDescriptions(original, photos(context('주출입구', '계단 오른쪽에 경사로 있음')))
  assert.deepEqual(result.accessibility_facts, [original.accessibility_facts[1]])
  assert.equal(result.hidden_facts, 1)
})

test('시설명과 값이 같아도 조사 분류가 다른 항목을 함께 숨기지 않습니다', () => {
  const original = details([], [
    { ...fact('출발지', '유효폭', '90cm'), section: '화장실' },
    { ...fact('출발지', '유효폭', '90cm'), section: '주출입구' },
  ])
  assert.deepEqual(selectDetailDescriptions(original, photos(context('출발지', '유효폭 90cm'))).accessibility_facts, original.accessibility_facts)
  const result = selectDetailDescriptions(original, photos(context('출발지', '화장실 유효폭 90cm')))
  assert.deepEqual(result.accessibility_facts, [original.accessibility_facts[1]])
})

test('구분 문자가 있는 다른 시설과 설명을 서로 합치지 않습니다', () => {
  const original = details([spot('A|B', 'C'), spot('A', 'B|C'), spot('__proto__', 'constructor')])
  const result = selectDetailDescriptions(original, photos(context('A|B', 'C')))
  assert.deepEqual(result.spots, original.spots.slice(1))
})

test('입력 원문과 사진 설명을 변경하지 않으며 반환 목록도 독립적으로 수정할 수 있습니다', () => {
  const original = details([spot('주차장', '  별도 원문  ')], [fact('입구', '경사', '10도')])
  const state = photos(context('다른 시설', '다른 설명'))
  const before = structuredClone({ original, state })
  const result = selectDetailDescriptions(original, state)
  result.spots[0].description = 'changed'
  result.accessibility_facts[0].raw_value = 'changed'
  assert.deepEqual({ original, state }, before)
})

test('사진이 없는 장소의 빈 목록을 미설치나 다른 정보로 바꾸지 않습니다', () => {
  assert.deepEqual(selectDetailDescriptions(details(), photos()), {
    spots: [], accessibility_facts: [], hidden_spots: 0, hidden_facts: 0,
  })
})
