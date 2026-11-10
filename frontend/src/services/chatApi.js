import { isPlanResponse } from './planApi.js'

const REQUEST_TIMEOUT_MS = 180_000

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
    && [null, 'walking', 'driving', 'transit'].includes(options.transport)
    && [null, 'wheelchair', 'stroller', 'none'].includes(options.mobility_aid)
    && (options.start_date === null || isDateString(options.start_date))
    && (options.trip_days === null || (
      Number.isInteger(options.trip_days)
      && options.trip_days >= 1
      && options.trip_days <= 3
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

export async function requestTravelChat(input) {
  const body = JSON.stringify(buildChatBody(input))
  const controller = new AbortController()
  let timedOut = false

  const timeoutId = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, REQUEST_TIMEOUT_MS)

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: controller.signal,
    })

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
  } catch (error) {
    if (timedOut) {
      throw createChatError(
        '답변 대기 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.',
        'client_timeout',
      )
    }

    if (error?.name === 'ChatApiError') throw error

    throw createChatError(
      '서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      'network_error',
    )
  } finally {
    clearTimeout(timeoutId)
  }
}