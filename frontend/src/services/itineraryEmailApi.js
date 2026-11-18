import { isChatOptions } from './chatApi.js'
import { isPlanResponse } from './planApi.js'
import { getPlanTransitGuidance } from './planTransit.js'

const TIMEOUT_MS = 45_000
const SERVER_ERROR_CODES = new Set([
  'invalid_request', 'pdf_unavailable',
  'email_not_configured', 'email_auth_failed',
  'email_rejected', 'email_send_failed',
  'email_invalid_pdf', 'email_attachment_too_large',
  'email_prepare_failed', 'email_rate_limited',
  'email_busy', 'email_in_progress', 'email_delivery_unknown',
])
const UNKNOWN_MESSAGE =
  '메일 접수 결과를 확인하지 못했습니다. 수신함과 스팸함을 먼저 확인해 주세요.'

function emailError(message, code, status = null) {
  const error = new Error(message)
  error.name = 'ItineraryEmailApiError'
  error.code = code
  error.status = status
  error.uncertain = [
    'email_delivery_unknown', 'email_in_progress',
  ].includes(code)
  return error
}

export async function requestItineraryEmail(
  planResult,
  recipient,
  { signal } = {},
) {
  if (
    !isPlanResponse(planResult)
    || !isChatOptions(planResult.options)
    || !planResult.plan.days.length
  ) {
    throw emailError(
      '이메일로 보낼 일정을 먼저 만들어 주세요.',
      'invalid_request',
    )
  }

  const address = typeof recipient === 'string'
    ? recipient.trim() : ''

  if (
    address.length > 254
    || address.includes('\0')
    || !/^[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+$/u.test(address)
  ) {
    throw emailError(
      '이메일 주소 형식을 확인해 주세요.',
      'invalid_recipient',
    )
  }

  if (signal?.aborted) {
    throw emailError(
      '발송 요청을 전송하지 않았습니다.',
      'cancelled',
    )
  }

  const body = JSON.stringify({
    recipient: address,
    options: planResult.options,
    plan: planResult.plan,
    warnings: planResult.warnings,
    transit_routes: [
      ...getPlanTransitGuidance(
        planResult.plan, planResult.transit_routes,
      ).values(),
    ],
  })

  const controller = new AbortController()
  let started = false
  let onStopped
  const stopped = new Promise((resolve, reject) => {
    onStopped = () => reject(started
      ? emailError(UNKNOWN_MESSAGE, 'email_delivery_unknown')
      : emailError('발송 요청을 전송하지 않았습니다.', 'cancelled'))
    controller.signal.addEventListener(
      'abort', onStopped, { once: true },
    )
  })

  const onAbort = () => controller.abort()
  signal?.addEventListener('abort', onAbort, { once: true })
  if (signal?.aborted) controller.abort()
  const timer = setTimeout(
    () => controller.abort(), TIMEOUT_MS,
  )

  const operation = async () => {
    if (controller.signal.aborted) return stopped
    started = true

    const response = await fetch('/api/itineraries/email', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: controller.signal,
    })

    let data
    try {
      data = await response.json()
    } catch {
      throw emailError(
        UNKNOWN_MESSAGE, 'email_delivery_unknown', response.status,
      )
    }

    if (!response.ok) {
      if (
        SERVER_ERROR_CODES.has(data?.error?.code)
        && typeof data.error.message === 'string'
        && data.error.message.trim()
      ) {
        throw emailError(
          data.error.message, data.error.code, response.status,
        )
      }
      throw emailError(
        UNKNOWN_MESSAGE, 'email_delivery_unknown', response.status,
      )
    }

    if (
      response.status !== 200
      || data?.status !== 'accepted'
      || typeof data.duplicate !== 'boolean'
      || typeof data.message !== 'string'
      || !data.message.trim()
    ) {
      throw emailError(
        UNKNOWN_MESSAGE, 'email_delivery_unknown', response.status,
      )
    }

    return {
      status: data.status,
      duplicate: data.duplicate,
      message: data.message,
    }
  }

  try {
    return await Promise.race([operation(), stopped])
  } catch (error) {
    if (error?.name === 'ItineraryEmailApiError') throw error
    if (controller.signal.aborted) return await stopped
    throw emailError(UNKNOWN_MESSAGE, 'email_delivery_unknown')
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
    controller.signal.removeEventListener('abort', onStopped)
  }
}