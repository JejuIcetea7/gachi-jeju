import test from 'node:test'
import assert from 'node:assert/strict'
import { getPlanTransitGuidance, isTransitMove } from '../../frontend/src/services/planTransit.js'

const move = (route_id, transport = '대중교통') => ({ item_type: 'move', route_id, transport })
const plan = (...items) => ({ days: [{ day: 1, items }] })
const segment = (type = 'BUS') => ({
  type, distance_m: 1000, duration_seconds: 600,
  route_numbers: type === 'BUS' ? ['202', '784-1'] : [],
  boarding_stop: type === 'BUS' ? '승차 정류장' : null,
  alighting_stop: type === 'BUS' ? '하차 정류장' : null,
  ride_stop_count: type === 'BUS' ? 2 : null,
})
const record = (route_id) => ({
  route_id,
  requested_at: '2026-11-13T02:00:00Z',
  segments: [segment('WALKING'), segment(), segment('WALKING')],
})

test('최종 일정에 사용한 버스 경로만 이동 순서대로 선택합니다', () => {
  const selected = getPlanTransitGuidance(plan(move('b'), move('a')),
    [record('unused'), record('a'), record('b')])
  assert.deepEqual([...selected.keys()], ['b', 'a'])
  assert.deepEqual(selected.get('b').segments.map((item) => item.type), ['WALKING', 'BUS', 'WALKING'])
})

test('자동차·도보·방문 항목에 버스 안내를 붙이지 않습니다', () => {
  const selected = getPlanTransitGuidance(plan(
    move('car', '자동차'), move('walk', '도보'), { item_type: 'visit', route_id: 'visit', transport: '버스' },
  ), [record('car'), record('walk'), record('visit')])
  assert.equal(selected.size, 0)
  for (const transport of ['버스', 'bus', 'transit', ' 대중교통 ', '대중교통(버스·도보)']) {
    assert.equal(isTransitMove(move('a', transport)), true)
  }
})

test('중복 원본 ID와 여러 이동·날짜에 재사용한 ID는 선택하지 않습니다', () => {
  assert.equal(getPlanTransitGuidance(plan(move('a')), [record('a'), record('a')]).size, 0)
  assert.equal(getPlanTransitGuidance(plan(move('a'), move('a')), [record('a')]).size, 0)
  const days = { days: [{ day: 1, items: [move('a')] }, { day: 2, items: [move('a')] }] }
  assert.equal(getPlanTransitGuidance(days, [record('a')]).size, 0)
  assert.equal(getPlanTransitGuidance(plan(move('a'), move('a', '자동차')), [record('a')]).size, 0)
})

test('이전 응답과 잘못된 목록은 일정을 바꾸지 않고 안내 없이 유지합니다', () => {
  const trip = plan(move('old'), move(null))
  const before = structuredClone(trip)
  for (const records of [undefined, null, {}, [], [null], [record('different')]]) {
    assert.equal(getPlanTransitGuidance(trip, records).size, 0)
  }
  assert.equal(getPlanTransitGuidance(null, [record('a')]).size, 0)
  assert.deepEqual(trip, before)
})

test('잘못된 정거장 수·구간·조회 시각을 표시 대상으로 선택하지 않습니다', () => {
  for (const patch of [
    { segments: [] },
    { segments: [null] },
    { segments: [{ ...segment(), ride_stop_count: 0 }] },
    { segments: [{ ...segment(), ride_stop_count: true }] },
    { segments: [{ ...segment(), ride_stop_count: '3' }] },
    { segments: [{ ...segment(), duration_seconds: -1 }] },
    { segments: [{ ...segment(), route_numbers: [202] }] },
    { segments: [{ ...segment(), boarding_stop: {} }] },
    { requested_at: '2026-11-13T02:00:00' },
    { requested_at: 'invalid' },
  ]) {
    assert.equal(getPlanTransitGuidance(plan(move('a')), [{ ...record('a'), ...patch }]).size, 0)
  }
})

test('정류장 수나 이름이 미확인이어도 확인한 노선 정보는 유지합니다', () => {
  const source = record('a')
  source.segments = [{ ...segment(), boarding_stop: null, alighting_stop: null, ride_stop_count: null }]
  const selected = getPlanTransitGuidance(plan(move('a')), [source]).get('a')
  assert.deepEqual(selected.segments[0].route_numbers, ['202', '784-1'])
  assert.equal(selected.segments[0].ride_stop_count, null)
})

test('알 수 없는 연결 구간도 생략하지 않아 이동 순서가 끊기지 않습니다', () => {
  const source = record('a')
  source.segments.splice(1, 0, segment('UNKNOWN'))
  const selected = getPlanTransitGuidance(plan(move('a')), [source]).get('a')
  assert.deepEqual(selected.segments.map((item) => item.type), ['WALKING', 'UNKNOWN', 'BUS', 'WALKING'])
})

test('선택 결과를 수정해도 원본 경로와 버스 노선 목록은 바뀌지 않습니다', () => {
  const trip = plan(move('a'))
  const source = [record('a')]
  source[0].segments[1].route_numbers.push('202')
  source[0].segments[1].remaining_stops = 17
  const before = structuredClone([trip, source])
  const selected = getPlanTransitGuidance(trip, source).get('a')
  assert.deepEqual(selected.segments[1].route_numbers, ['202', '784-1'])
  assert.equal(selected.segments[1].ride_stop_count, 2)
  assert.equal(Object.hasOwn(selected.segments[1], 'remaining_stops'), false)
  selected.segments[1].route_numbers.push('변경')
  assert.deepEqual([trip, source], before)
})
