import assert from 'node:assert/strict'
import test from 'node:test'

import { getDayMapRoutes } from '../../frontend/src/services/itineraryRoutes.js'

const move = (routeId) => ({ item_type: 'move', route_id: routeId })
const day = (...items) => ({ day: 1, items })
const route = (id = 'selected') => ({
  route_id: id,
  transport: 'driving',
  origin: { latitude: 33.4, longitude: 126.5 },
  destination: { latitude: 33.41, longitude: 126.51 },
  distance_m: 1000,
  duration_seconds: 600,
  geometry_status: 'ok',
  paths: [[[126.5, 33.4], [126.51, 33.41]]],
  requested_at: '2026-11-10T00:00:00+00:00',
})

test('선택한 날짜의 이동 순서대로 경로를 선택합니다', () => {
  const selectedDay = day(
    move('first'),
    { item_type: 'visit', route_id: 'visit-only' },
    move('second'),
  )
  const records = [
    route('other-day'),
    route('second'),
    route('visit-only'),
    route('first'),
  ]
  const result = getDayMapRoutes(selectedDay, records)

  assert.deepEqual(
    result.routes.map((item) => item.route_id),
    ['first', 'second'],
  )
  assert.equal(result.missingCount, 0)
})

test('이전 경로 ID와 경로 ID가 없는 일정은 경로 없이 유지합니다', () => {
  const selectedDay = day(move('previous-run'), move(null))
  const original = structuredClone(selectedDay)

  assert.deepEqual(getDayMapRoutes(selectedDay, [route()]), {
    routes: [],
    missingCount: 2,
  })
  assert.deepEqual(selectedDay, original)
})

test('경로 필드가 없는 이전 응답과 잘못된 목록 형식을 허용합니다', () => {
  assert.deepEqual(getDayMapRoutes(null), {
    routes: [],
    missingCount: 0,
  })
  assert.deepEqual(getDayMapRoutes(day(move('selected'))), {
    routes: [],
    missingCount: 1,
  })
  assert.equal(
    getDayMapRoutes(day(move('selected')), {}).missingCount,
    1,
  )
})

test('잘못된 좌표나 경로 형식을 표시 대상에서 제외합니다', () => {
  const patches = [
    { paths: [[[126.5, 33.4]]] },
    { paths: [[[true, 33.4], [126.51, 33.41]]] },
    { paths: [[[126.5, 91], [126.51, 33.41]]] },
    { paths: [[[NaN, 33.4], [126.51, 33.41]]] },
    { paths: [] },
    { geometry_status: 'missing' },
    { transport: 'unknown' },
    { duration_seconds: '600' },
    { requested_at: 'invalid-date' },
    { requested_at: '2026-11-10T00:00:00' },
  ]

  for (const patch of patches) {
    const result = getDayMapRoutes(day(move('selected')), [
      { ...route(), ...patch },
    ])

    assert.equal(result.routes.length, 0, JSON.stringify(patch))
    assert.equal(result.missingCount, 1)
  }
})

test('중복된 경로 ID나 여러 이동에 재사용한 ID는 선택하지 않습니다', () => {
  const duplicateRecords = getDayMapRoutes(
    day(move('selected')),
    [route(), route()],
  )
  const duplicateReferences = getDayMapRoutes(
    day(move('selected'), move('selected')),
    [route()],
  )

  assert.deepEqual(duplicateRecords, {
    routes: [],
    missingCount: 1,
  })
  assert.deepEqual(duplicateReferences, {
    routes: [],
    missingCount: 2,
  })
})

test('부분 경로의 도로별 좌표를 연결하지 않고 원본도 변경하지 않습니다', () => {
  const record = route()
  record.geometry_status = 'partial'
  record.paths = [
    [[126.5, 33.4], [126.51, 33.41]],
    [[126.6, 33.5], [126.61, 33.51]],
  ]
  const original = structuredClone(record)
  const result = getDayMapRoutes(day(move('selected')), [record])

  assert.equal(result.routes.length, 1)
  assert.equal(result.missingCount, 0)
  assert.equal(result.routes[0].geometry_status, 'partial')
  assert.deepEqual(result.routes[0].paths, original.paths)
  assert.deepEqual(record, original)
})