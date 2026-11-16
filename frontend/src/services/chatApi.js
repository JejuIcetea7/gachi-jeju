import { isLodgingOptions } from './lodgingPlanning.js'
import { isPlanResponse } from './planApi.js'
import { getTripEndDate } from './travelOptions.js'

const REQUEST_TIMEOUT_MS = 180_000
const CANCEL_TIMEOUT_MS = 5_000
const REQUEST_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

function createChatError(message, code, status = null, details = []) {
  const error = new Error(message)
  error.name = 'ChatApiError'
  error.code = code
  error.status = status
  error.details = details
  return error
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isDateString(value) {
  if (
    typeof value !== 'string'
    || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)
  ) {
    return false
  }

  const timestamp = Date.parse(`${value}T00:00:00Z`)
  return value >= '0001-01-01'
    && Number.isFinite(timestamp)
    && new Date(timestamp).toISOString().slice(0, 10) === value
}

export function isChatOptions(options) {
  if (!isRecord(options)) return false

  try {
    getTripEndDate(options.start_date, options.trip_days)
  } catch {
    return false
  }

  const latitude = options.departure_latitude
  const longitude = options.departure_longitude
  const hasValidCoordinates = (latitude === null && longitude === null)
    || (
      Number.isFinite(latitude) && Math.abs(latitude) <= 90
      && Number.isFinite(longitude) && Math.abs(longitude) <= 180
    )

  return (options.departure === null || (
    typeof options.departure === 'string'
    && options.departure.trim().length > 0
  ))
    && hasValidCoordinates
    && isLodgingOptions(options)
    && [null, 'walking', 'driving', 'transit'].includes(options.transport)
    && [null, 'wheelchair', 'stroller', 'none'].includes(options.mobility_aid)
    && (options.start_date === null || isDateString(options.start_date))
    && (options.trip_days === null || (
      Number.isSafeInteger(options.trip_days)
      && options.trip_days >= 1
    ))
    && Array.isArray(options.travel_styles)
    && options.travel_styles.every((value) => typeof value === 'string')
    && Array.isArray(options.requirements)
    && options.requirements.every((value) => typeof value === 'string')
    && (
      options.return_to_origin === null
      || typeof options.return_to_origin === 'boolean'
    )
}

export function isChatResponse(data) {
  return typeof data?.reply === 'string'
    && data.reply.trim().length > 0
    && isChatOptions(data.options)
    && Array.isArray(data.warnings)
    && data.warnings.every((value) => typeof value === 'string')
    && (data.plan === null || isPlanResponse(data))
}

function buildChatBody({
  message,
  history = [],
  options = {},
  currentPlan = null,
} = {}) {
  const text = typeof message === 'string' ? message.trim() : ''

  if (!text || text.length > 1000) {
    throw createChatError(
      '메시지는 1~1,000자로 작성해 주세요.',
      'invalid_request',
    )
  }

  if (!Array.isArray(history) || !isRecord(options)) {
    throw createChatError(
      '대화 기록이나 선택 옵션을 확인해 주세요.',
      'invalid_request',
    )
  }

  const previousMessages = history.slice(-20).map((item) => {
    const content = typeof item?.content === 'string'
      ? item.content.trim()
      : ''
    const limit = item?.role === 'assistant' ? 4000 : 1000

    if (
      !['user', 'assistant'].includes(item?.role)
      || !content
      || content.length > limit
    ) {
      throw createChatError(
        '이전 대화의 형식이나 길이를 확인해 주세요.',
        'invalid_request',
      )
    }

    return { role: item.role, content }
  })

  return {
    message: text,
    history: previousMessages,
    options,
    current_plan: currentPlan,
  }
}

async function notifyChatCancellation(requestId) {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), CANCEL_TIMEOUT_MS)

  try {
    const response = await fetch('/api/chat/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: requestId }),
      signal: controller.signal,
      keepalive: true,
    })
    await response.body?.cancel()
  } catch {
    // 취소 통지 실패가 원래 요청의 취소 결과를 바꾸지 않도록 합니다.
  } finally {
    clearTimeout(timeoutId)
  }
}

export async function requestTravelChat(
  input,
  { signal, requestId = crypto.randomUUID() } = {},
) {
  const body = JSON.stringify(buildChatBody(input))
  if (
    typeof requestId !== 'string'
    || !REQUEST_ID_PATTERN.test(requestId)
    || (signal != null && (
      typeof signal.aborted !== 'boolean'
      || typeof signal.addEventListener !== 'function'
      || typeof signal.removeEventListener !== 'function'
    ))
  ) {
    throw createChatError(
      '요청 식별자와 취소 신호를 확인해 주세요.',
      'invalid_request',
    )
  }

  const controller = new AbortController()
  let started = false
  let settled = false
  let rejectStopped
  const stopped = new Promise((_, reject) => { rejectStopped = reject })

  function stop(code) {
    if (settled || controller.signal.aborted) return

    const message = code === 'client_timeout'
      ? '답변 대기 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.'
      : '요청이 취소되었습니다.'
    controller.abort(createChatError(message, code))
    rejectStopped(controller.signal.reason)
    if (started) void notifyChatCancellation(requestId)
  }

  const onAbort = () => stop('request_cancelled')
  signal?.addEventListener('abort', onAbort, { once: true })
  const timeoutId = setTimeout(() => stop('client_timeout'), REQUEST_TIMEOUT_MS)

  async function fetchReply() {
    controller.signal.throwIfAborted()
    started = true
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Chat-Request-ID': requestId,
      },
      body,
      signal: controller.signal,
    })
    controller.signal.throwIfAborted()

    let data
    try {
      data = await response.json()
    } catch {
      throw createChatError(
        '답변 정보를 읽지 못했습니다. 다시 시도해 주세요.',
        'invalid_response',
        response.status,
      )
    }
    controller.signal.throwIfAborted()

    if (!response.ok) {
      const apiError = data?.error
      const message = typeof apiError?.message === 'string'
        && apiError.message.trim()
        ? apiError.message
        : '답변을 받지 못했습니다. 다시 시도해 주세요.'
      const code = typeof apiError?.code === 'string'
        ? apiError.code
        : 'api_error'
      const details = Array.isArray(apiError?.details)
        ? apiError.details.filter((item) => (
          typeof item?.field === 'string'
          && typeof item?.reason === 'string'
        ))
        : []

      throw createChatError(message, code, response.status, details)
    }

    if (!isChatResponse(data)) {
      throw createChatError(
        '채팅 응답 형식이 올바르지 않습니다. 다시 시도해 주세요.',
        'invalid_response',
        response.status,
      )
    }

    return data
  }

  try {
    if (signal?.aborted) onAbort()
    const response = await Promise.race([fetchReply(), stopped])
    controller.signal.throwIfAborted()
    return response
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason
    if (error?.name === 'ChatApiError') throw error

    throw createChatError(
      '서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      'network_error',
    )
  } finally {
    settled = true
    clearTimeout(timeoutId)
    signal?.removeEventListener('abort', onAbort)
  }
}