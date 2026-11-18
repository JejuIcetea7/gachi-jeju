import { isChatOptions } from './chatApi.js'
import { isPlanResponse } from './planApi.js'
import { getPlanTransitGuidance } from './planTransit.js'

const TIMEOUT_MS = 30_000
const MAX_PDF_BYTES = 20 * 1024 * 1024

function pdfError(message, code) {
  const error = new Error(message)
  error.name = 'ItineraryPdfError'
  error.code = code
  return error
}

export async function requestItineraryPdf(planResult, { signal } = {}) {
  if (!isPlanResponse(planResult) || !isChatOptions(planResult.options) || !planResult.plan.days.length) {
    throw pdfError('PDF로 저장할 일정을 먼저 만들어 주세요.', 'invalid_request')
  }
  if (signal?.aborted) throw pdfError('PDF 다운로드를 취소했습니다.', 'cancelled')

  const body = JSON.stringify({
    options: planResult.options,
    plan: planResult.plan,
    warnings: planResult.warnings,
    transit_routes: [...getPlanTransitGuidance(planResult.plan, planResult.transit_routes).values()],
  })
  const filename = `같이가는제주_${planResult.options.start_date || '날짜미정'}_여행일정.pdf`
  const controller = new AbortController()
  let timedOut = false
  let onStopped
  const stopped = new Promise((resolve, reject) => {
    onStopped = () => reject(pdfError(
      timedOut ? 'PDF 준비 시간이 초과됐습니다. 다시 시도해 주세요.' : 'PDF 다운로드를 취소했습니다.',
      timedOut ? 'timeout' : 'cancelled',
    ))
    controller.signal.addEventListener('abort', onStopped, { once: true })
  })
  const onAbort = () => controller.abort()
  signal?.addEventListener('abort', onAbort, { once: true })
  if (signal?.aborted) controller.abort()
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, TIMEOUT_MS)

  const operation = async () => {
    if (controller.signal.aborted) return stopped
    const response = await fetch('/api/itineraries/pdf', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body, signal: controller.signal,
    })
    if (!response.ok) {
      const data = await response.json().catch(() => null)
      throw pdfError(
        typeof data?.error?.message === 'string' && data.error.message.trim()
          ? data.error.message : 'PDF를 준비하지 못했습니다. 다시 시도해 주세요.',
        'server_error',
      )
    }
    if (!/^application\/pdf(?:;|$)/i.test(response.headers.get('Content-Type') || '')) {
      throw pdfError('서버에서 올바른 PDF를 받지 못했습니다.', 'invalid_response')
    }
    const content = await response.arrayBuffer()
    const signature = String.fromCharCode(...new Uint8Array(content).slice(0, 5))
    if (signature !== '%PDF-' || content.byteLength > MAX_PDF_BYTES) {
      throw pdfError('서버에서 올바른 PDF를 받지 못했습니다.', 'invalid_response')
    }
    return { blob: new Blob([content], { type: 'application/pdf' }), filename }
  }
  try {
    return await Promise.race([operation(), stopped])
  } catch (error) {
    if (error?.name === 'ItineraryPdfError') throw error
    if (controller.signal.aborted) return await stopped
    throw pdfError('PDF 서버에 연결하지 못했습니다. 다시 시도해 주세요.', 'network_error')
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
    controller.signal.removeEventListener('abort', onStopped)
  }
}

export function savePdfFile({ blob, filename }) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  try {
    link.href = url
    link.download = filename
    link.hidden = true
    document.body.append(link)
    link.click()
  } finally {
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
