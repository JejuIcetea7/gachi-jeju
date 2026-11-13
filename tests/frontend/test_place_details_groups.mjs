import test from 'node:test'
import assert from 'node:assert/strict'
import { groupAccessibilityFacts } from '../../frontend/src/services/placeDetails.js'

const fact = (section, facility, description, value) => ({
  section, facility_name: facility, description, raw_value: value,
})

test('같은 시설의 항목은 원문과 항목 순서를 유지해 묶습니다', () => {
  const input = [
    fact('주차장', '제1주차장', '주차면수', ' 5대 '),
    fact('주차장', '제1주차장', '안전통로', '미설치'),
  ]
  const before = structuredClone(input)
  const groups = groupAccessibilityFacts(input)

  assert.equal(groups.length, 1)
  assert.equal(groups[0].facility_name, '제1주차장')
  assert.deepEqual(groups[0].facts, input)
  groups[0].facts[0].raw_value = '수정'
  assert.deepEqual(input, before)
})

test('시설명이 다르면 같은 주차장 분류여도 정보를 합치지 않습니다', () => {
  const input = [
    fact('주차장', '제1주차장', '주차면수', '5대'),
    fact('주차장', '제2주차장', '주차면수', '2대'),
    fact('주차장', '제1주차장', '안전통로', '미설치'),
  ]
  const groups = groupAccessibilityFacts(input)
  assert.deepEqual(groups.map((group) => group.facility_name), ['제1주차장', '제2주차장'])
  assert.deepEqual(groups[0].facts.map((item) => item.raw_value), ['5대', '미설치'])
  assert.deepEqual(groups[1].facts.map((item) => item.raw_value), ['2대'])
})

test('같은 시설명도 조사 분류가 다르면 구분합니다', () => {
  const groups = groupAccessibilityFacts([
    fact('주차장', '출발지', '주차면수', '5대'),
    fact('화장실', '출발지', '유효폭', '90cm'),
  ])
  assert.deepEqual(groups.map((group) => group.section), ['주차장', '화장실'])
})

test('분류·시설명이 비어 있어도 이름이나 추천 의미를 만들어 넣지 않습니다', () => {
  const input = [
    fact(null, '', null, '●'),
    fact('', '', '수동 휠체어', '● 동행인 / 보호자 동반 추천'),
  ]
  const groups = groupAccessibilityFacts(input)
  assert.equal(groups[0].section, null)
  assert.equal(groups[1].section, '')
  assert.equal(groups[0].facility_name, '')
  assert.deepEqual(groups.flatMap((group) => group.facts), input)
})

test('분류명과 시설명에 구분 문자가 포함돼도 다른 시설끼리 섞이지 않습니다', () => {
  const groups = groupAccessibilityFacts([
    fact('주차장|입구', 'A', '면수', '5대'),
    fact('주차장', '입구|A', '면수', '2대'),
    fact('__proto__', 'constructor', '원문', '●'),
  ])
  assert.equal(groups.length, 3)
  assert.deepEqual(groups.map((group) => group.facts[0].raw_value), ['5대', '2대', '●'])
})

test('원문이 없는 목록을 시설 미설치로 바꾸지 않습니다', () => {
  assert.deepEqual(groupAccessibilityFacts([]), [])
})
