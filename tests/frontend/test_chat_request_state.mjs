import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createChatRequestTracker } from '../../frontend/src/services/chatRequestTracker.js'
import {
  createEmptyChatSession, loadChatSession, markChatMessageCancelled, saveChatSession,
} from '../../frontend/src/services/chatSession.js'

function savedState() {
  const state = createEmptyChatSession()
  state.options = {
    ...state.options, departure: '테스트 출발지', departure_latitude: 33.4,
    departure_longitude: 126.5, start_date: '2026-11-20', trip_days: 2,
    transport: 'driving', mobility_aid: 'none', requirements: ['accessible_parking'],
    lodging_status: 'undecided',
  }
  state.originPlace = {
    id: 'origin', name: '테스트 출발지', address: '테스트 주소',
    latitude: 33.4, longitude: 126.5,
  }
  state.messages = [
    { id: 'first', role: 'user', content: '일정을 만들어 주세요.', status: 'sent' },
    { id: 'answer', role: 'assistant', content: '일정을 만들었습니다.', status: 'sent' },
    { id: 'pending', role: 'user', content: '방문지를 바꿔 주세요.', status: 'pending' },
  ]
  state.planResult = {
    options: structuredClone(state.options),
    plan: { assumptions: ['테스트 일정입니다.'], days: [
      { day: 1, items: [] }, { day: 2, items: [] },
    ] },
    warnings: [], map_routes: [], transit_routes: [],
    meta: { model_call_count: 1, tool_call_counts: {}, elapsed_seconds: 1 },
  }
  state.selectedDay = 2
  return state
}

function withStorage(check) {
  const previous = globalThis.window
  const cache = new Map()
  globalThis.window = { sessionStorage: {
    getItem: (key) => cache.get(key) ?? null,
    setItem: (key, value) => cache.set(key, value),
  } }
  try {
    check(cache)
  } finally {
    if (previous === undefined) delete globalThis.window
    else globalThis.window = previous
  }
}

test('요청마다 메시지 식별자와 별도의 UUID4·취소 신호를 만듭니다', () => {
  const tracker = createChatRequestTracker()
  const ticket = tracker.begin('message')
  assert.equal(ticket.messageId, 'message')
  assert.match(ticket.requestId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  assert.equal(ticket.controller.signal.aborted, false)
  assert.equal(tracker.isCurrent(ticket), true)
  assert.equal(tracker.isLatest(ticket), true)
})

test('처리 중에는 중복 요청을 시작하거나 기존 요청을 취소하지 않습니다', () => {
  const tracker = createChatRequestTracker()
  const first = tracker.begin('first')
  assert.equal(tracker.begin('second'), null)
  assert.equal(tracker.isCurrent(first), true)
  assert.equal(first.controller.signal.aborted, false)
})

test('정상 완료 후에도 예약된 상태 갱신을 반영할 수 있습니다', () => {
  const tracker = createChatRequestTracker()
  const ticket = tracker.begin('message')
  assert.equal(tracker.finish(ticket), true)
  assert.equal(tracker.isCurrent(ticket), false)
  assert.equal(tracker.isLatest(ticket), true)
  assert.equal(tracker.finish(ticket), false)
  assert.equal(ticket.controller.signal.aborted, false)
})

test('같은 메시지를 다시 보내도 새 요청 식별자를 사용합니다', () => {
  const tracker = createChatRequestTracker()
  const first = tracker.begin('message')
  tracker.finish(first)
  const second = tracker.begin('message')
  assert.notEqual(first.requestId, second.requestId)
  assert.equal(tracker.isLatest(first), false)
  assert.equal(tracker.isCurrent(second), true)
})

test('취소 신호를 보내기 전에 이전 요청의 결과 반영을 차단합니다', () => {
  const tracker = createChatRequestTracker()
  const first = tracker.begin('first')
  first.controller.signal.addEventListener('abort', () => {
    assert.equal(tracker.isCurrent(first), false)
    assert.equal(tracker.isLatest(first), false)
  })
  assert.equal(tracker.cancel(), first)
  assert.equal(first.controller.signal.aborted, true)
  assert.equal(tracker.cancel(), null)
})

test('취소한 요청의 늦은 종료가 새 요청을 해제하지 못합니다', () => {
  const tracker = createChatRequestTracker()
  const first = tracker.begin('first')
  tracker.cancel()
  const second = tracker.begin('second')
  assert.equal(tracker.finish(first), false)
  assert.equal(tracker.isLatest(first), false)
  assert.equal(tracker.isCurrent(second), true)
  assert.equal(tracker.begin('third'), null)
})

test('새 여행을 시작하면 완료된 요청의 예약된 상태 갱신도 차단합니다', () => {
  const tracker = createChatRequestTracker()
  const ticket = tracker.begin('message')
  tracker.finish(ticket)
  tracker.reset()
  assert.equal(tracker.isLatest(ticket), false)
})

test('화면 종료 처리는 진행 중인 요청을 취소하며 반복해도 안전합니다', () => {
  const tracker = createChatRequestTracker()
  const ticket = tracker.begin('message')
  assert.equal(tracker.reset(), ticket)
  assert.equal(ticket.controller.signal.aborted, true)
  assert.equal(tracker.reset(), null)
  assert.equal(tracker.isLatest(ticket), false)
})

test('다른 화면의 요청을 현재 요청으로 인정하거나 취소하지 않습니다', () => {
  const first = createChatRequestTracker()
  const second = createChatRequestTracker()
  const a = first.begin('message')
  const b = second.begin('message')
  assert.equal(first.isCurrent(b), false)
  assert.equal(first.isLatest(b), false)
  first.cancel()
  assert.equal(second.isCurrent(b), true)
  assert.equal(b.controller.signal.aborted, false)
  assert.equal(a.controller.signal.aborted, true)
})

test('요청이 없을 때 취소해도 정상 완료 결과의 반영 권한을 바꾸지 않습니다', () => {
  const tracker = createChatRequestTracker()
  assert.equal(tracker.isCurrent(null), false)
  assert.equal(tracker.isLatest(null), false)
  const ticket = tracker.begin('message')
  tracker.finish(ticket)
  assert.equal(tracker.cancel(), null)
  assert.equal(tracker.isLatest(ticket), true)
})

test('취소 표시를 추가해도 일정·옵션·지도·선택 일차와 원본을 유지합니다', () => {
  const state = savedState()
  const before = structuredClone(state)
  const cancelled = markChatMessageCancelled(state, 'pending')
  assert.equal(cancelled.messages[2].status, 'cancelled')
  assert.equal(cancelled.planResult, state.planResult)
  assert.equal(cancelled.options, state.options)
  assert.equal(cancelled.originPlace, state.originPlace)
  assert.equal(cancelled.selectedDay, 2)
  assert.deepEqual(state, before)
  assert.equal(cancelled.messages[0], state.messages[0])
})

test('이미 보낸 메시지와 도우미 메시지를 취소 상태로 바꾸지 않습니다', () => {
  for (const status of ['sent', 'failed', 'cancelled']) {
    const state = savedState()
    state.messages[2].status = status
    assert.equal(markChatMessageCancelled(state, 'pending').messages[2].status, status)
  }
  const state = savedState()
  state.messages[2].role = 'assistant'
  assert.equal(markChatMessageCancelled(state, 'pending').messages[2].status, 'pending')
})

test('새로고침 후 취소 상태와 기존 일정의 선택 일차를 복원합니다', () => withStorage(() => {
  const state = markChatMessageCancelled(savedState(), 'pending')
  assert.equal(saveChatSession(state), true)
  assert.deepEqual(loadChatSession(), state)
}))

test('복원할 때 대기 요청은 실패로 바꾸고 명시적으로 취소한 요청은 유지합니다', () => withStorage(() => {
  const state = markChatMessageCancelled(savedState(), 'pending')
  state.messages.push({ id: 'interrupted', role: 'user', content: '다음 요청', status: 'pending' })
  assert.equal(saveChatSession(state), true)
  const restored = loadChatSession()
  assert.equal(restored.messages[2].status, 'cancelled')
  assert.equal(restored.messages[3].status, 'failed')
  assert.deepEqual(restored.planResult, state.planResult)
}))

test('기존 저장 형식을 유지하고 알 수 없는 메시지 상태는 저장하지 않습니다', () => withStorage((cache) => {
  const state = savedState()
  assert.equal(saveChatSession(state), true)
  assert.equal(JSON.parse(cache.get('gachi-jeju.chat-session')).version, 1)
  state.messages[2].status = 'unknown'
  assert.equal(saveChatSession(state), false)
}))

let panelRenderer
async function renderPanel(props) {
  if (!panelRenderer) {
    panelRenderer = (async () => {
      const require = createRequire(new URL('../../frontend/package.json', import.meta.url))
      const { createElement } = require('react')
      const { renderToStaticMarkup } = require('react-dom/server')
      const { transformWithOxc } = await import(pathToFileURL(require.resolve('vite')).href)
      const filename = new URL('../../frontend/src/ConversationPanel.jsx', import.meta.url)
      const source = await readFile(filename, 'utf8')
      const transformed = await transformWithOxc(source, fileURLToPath(filename), {
        jsx: { runtime: 'automatic', development: false },
      })
      const code = transformed.code.replace(
        /from (['"])(react(?:\/jsx-runtime)?)\1/g,
        (match, quote, name) => `from '${pathToFileURL(require.resolve(name)).href}'`,
      )
      const { default: Panel } = await import(
        'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
      )
      return (value) => renderToStaticMarkup(createElement(Panel, value))
    })()
  }
  return (await panelRenderer)(props)
}

test('대기 화면에 취소 버튼을 표시하고 입력창을 비활성화합니다', async () => {
  const html = await renderPanel({ isSending: true, onCancel() {} })
  assert.match(html, />요청 취소<\/button>/)
  assert.match(html, /<textarea[^>]*disabled/)
  assert.match(html, /role="status"/)
})

test('취소 화면에는 실패 표시 없이 취소 문구·재전송 버튼과 활성 입력창을 표시합니다', async () => {
  const html = await renderPanel({
    messages: [{ id: 'cancelled', role: 'user', content: '원래 요청', status: 'cancelled' }],
    notice: '요청을 취소했습니다.', onRetry() {},
  })
  assert.match(html, /conversation-cancelled/)
  assert.match(html, /이 요청 다시 보내기/)
  assert.doesNotMatch(html, /conversation-failed|conversation-error|is-failed/)
  assert.doesNotMatch(html, /<textarea[^>]*disabled/)
})

test('실패한 요청의 오류 안내와 재전송 버튼을 유지합니다', async () => {
  const html = await renderPanel({
    messages: [{ id: 'failed', role: 'user', content: '원래 요청', status: 'failed' }],
    error: { message: '오류 안내' }, onRetry() {},
  })
  assert.match(html, /conversation-failed/)
  assert.match(html, /role="alert"/)
  assert.match(html, /이 요청 다시 보내기/)
})
