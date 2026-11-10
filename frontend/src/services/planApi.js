const REQUEST_TIMEOUT_MS = 180_000

function createPlanError(message, code, status = null, details = []) {
  const error = new Error(message)
  error.name = 'PlanApiError'
  error.code = code
  error.status = status
  error.details = details
  return error
}

function isScheduleItem(item) {
  return ['visit', 'move'].includes(item?.item_type)
    && typeof item.title === 'string'
    && typeof item.start_time === 'string'
    && typeof item.end_time === 'string'
    && Array.isArray(item.accessibility_notes)
    && item.accessibility_notes.every((text) => typeof text === 'string')
}

export function isPlanResponse(data) {
  const plan = data?.plan
  return Array.isArray(plan?.assumptions)
    && plan.assumptions.every((text) => typeof text === 'string')
    && Array.isArray(plan.days)
    && plan.days.every((day) => (
      Number.isInteger(day?.day)
      && day.day > 0
      && Array.isArray(day.items)
      && day.items.every(isScheduleItem)
    ))
    && Array.isArray(data.warnings)
    && data.warnings.every((text) => typeof text === 'string')
}

export async function requestTravelPlan(requestBody) {
  const body = JSON.stringify(requestBody)
  const controller = new AbortController()
  let timedOut = false
  const timeoutId = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, REQUEST_TIMEOUT_MS)

  try {
    const response = await fetch('/api/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: controller.signal,
    })

    let data
    try {
      data = await response.json()
    } catch {
      throw createPlanError(
        '일정 정보를 읽지 못했습니다. 다시 시도해 주세요.',
        'invalid_response',
        response.status,
      )
    }

    if (!response.ok) {
      const apiError = data?.error
      const message = typeof apiError?.message === 'string'
        && apiError.message.trim()
        ? apiError.message
        : '일정을 만들지 못했습니다. 다시 시도해 주세요.'
      const code = typeof apiError?.code === 'string'
        ? apiError.code
        : 'api_error'
      const details = Array.isArray(apiError?.details)
        ? apiError.details.filter((item) => (
          typeof item?.field === 'string'
          && typeof item?.reason === 'string'
        ))
        : []
      throw createPlanError(message, code, response.status, details)
    }

    if (!isPlanResponse(data)) {
      throw createPlanError(
        '일정 정보의 형식이 올바르지 않습니다. 다시 시도해 주세요.',
        'invalid_response',
        response.status,
      )
    }

    return data
  } catch (error) {
    if (timedOut) {
      throw createPlanError(
        '응답 대기 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.',
        'client_timeout',
      )
    }
    if (error?.name === 'PlanApiError') {
      throw error
    }
    throw createPlanError(
      '서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      'network_error',
    )
  } finally {
    clearTimeout(timeoutId)
  }
}