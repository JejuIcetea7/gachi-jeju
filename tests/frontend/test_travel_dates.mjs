import test from 'node:test'
import assert from 'node:assert/strict'
import {
  createDefaultChatOptions,
  formatTripDuration,
  getKoreaToday,
  getTripDays,
  getTripEndDate,
  isPastTravelDate,
} from '../../frontend/src/services/travelOptions.js'
import { isChatOptions } from '../../frontend/src/services/chatApi.js'
import {
  createEmptyChatSession,
  loadChatSession,
  saveChatSession,
} from '../../frontend/src/services/chatSession.js'

function withStorage(check) {
  const previous = globalThis.window
  const cache = new Map()

  globalThis.window = {
    sessionStorage: {
      getItem: (key) => cache.get(key) ?? null,
      setItem: (key, value) => cache.set(key, value),
    },
  }

  try {
    check()
  } finally {
    if (previous === undefined) delete globalThis.window
    else globalThis.window = previous
  }
}

function savedSession() {
  const state = createEmptyChatSession()
  state.options = {
    ...state.options,
    start_date: '2020-01-01',
    trip_days: 5,
  }
  state.messages = [{
    id: 'saved-message',
    role: 'user',
    content: '5일 여행을 계획하고 싶어요.',
    status: 'sent',
  }]
  state.planResult = {
    options: { ...state.options },
    plan: {
      assumptions: ['복원 검증용 일정입니다.'],
      days: Array.from({ length: 5 }, (_, index) => ({
        day: index + 1,
        items: [],
      })),
    },
    map_routes: [],
    warnings: [],
  }
  state.selectedDay = 5
  return state
}

test('같은 날짜는 당일로 계산합니다', () => {
  assert.equal(getTripDays('2026-11-20', '2026-11-20'), 1)
  assert.equal(formatTripDuration(1), '당일')
})

test('5일과 30일 기간에 3일 제한을 적용하지 않습니다', () => {
  assert.equal(getTripDays('2026-11-20', '2026-11-24'), 5)
  assert.equal(formatTripDuration(5), '4박 5일')
  assert.equal(getTripDays('2026-11-01', '2026-11-30'), 30)
})

test('연도 변경과 윤년을 반영합니다', () => {
  assert.equal(getTripEndDate('2026-12-30', 5), '2027-01-03')
  assert.equal(getTripDays('2028-02-28', '2028-03-01'), 3)
})

test('역순 날짜와 존재하지 않는 날짜를 거부합니다', () => {
  assert.throws(() => getTripDays('2026-11-24', '2026-11-20'))
  assert.throws(() => getTripDays('2026-02-30', '2026-03-01'))
})

test('잘못된 일수와 달력 범위를 벗어난 날짜를 거부합니다', () => {
  for (const days of [0, -1, 1.5, true, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => getTripEndDate('2026-11-20', days))
  }

  assert.equal(getTripEndDate('9999-12-30', 2), '9999-12-31')
  assert.throws(() => getTripEndDate('9999-12-30', 3))
})

test('한국 시간 자정과 연말 경계를 반영합니다', () => {
  assert.equal(getKoreaToday(new Date('2026-11-11T14:59:59.999Z')), '2026-11-11')
  assert.equal(getKoreaToday(new Date('2026-11-11T15:00:00Z')), '2026-11-12')
  assert.equal(getKoreaToday(new Date('2026-12-31T15:00:00Z')), '2027-01-01')
})

test('지난 날짜를 구분하고 오늘과 미래 날짜는 허용합니다', () => {
  const now = new Date('2026-11-11T15:00:00Z')

  assert.equal(isPastTravelDate('2026-11-11', now), true)
  assert.equal(isPastTravelDate('2026-11-12', now), false)
  assert.equal(isPastTravelDate('2026-11-13', now), false)
})

test('응답 옵션은 긴 기간을 허용하고 잘못된 날짜 조합을 거부합니다', () => {
  const options = {
    ...createDefaultChatOptions(),
    start_date: '2026-11-20',
  }

  assert.equal(isChatOptions({ ...options, trip_days: 30 }), true)
  assert.equal(isChatOptions({ ...options, trip_days: 0 }), false)
  assert.equal(isChatOptions({
    ...options,
    start_date: '9999-12-30',
    trip_days: 3,
  }), false)
})

test('5일차와 과거 날짜가 있는 기존 대화를 복원합니다', () => {
  withStorage(() => {
    const state = savedSession()

    assert.equal(saveChatSession(state), true)
    assert.deepEqual(loadChatSession(), state)
  })
})

test('복원 시 대기 요청을 해제하고 없는 일차는 첫 일차로 바꿉니다', () => {
  withStorage(() => {
    const state = savedSession()
    state.messages[0].status = 'pending'
    state.selectedDay = 99

    assert.equal(saveChatSession(state), true)

    const restored = loadChatSession()
    assert.equal(restored.messages[0].status, 'failed')
    assert.equal(restored.selectedDay, 1)
  })
})