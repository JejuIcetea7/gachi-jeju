import { useEffect, useRef, useState } from 'react'
import { requestItineraryPdf, savePdfFile } from './services/itineraryPdfApi.js'

function ItineraryPdfDownload({ planResult, disabled = false }) {
  const activeRequest = useRef(null)
  const [state, setState] = useState(null)
  const current = state?.snapshot === planResult ? state : null
  const isDownloading = current?.pending === true

  useEffect(() => () => {
    activeRequest.current?.abort()
    activeRequest.current = null
  }, [planResult])

  async function handleDownload() {
    if (disabled || activeRequest.current) return
    const controller = new AbortController()
    activeRequest.current = controller
    setState({ snapshot: planResult, pending: true })

    try {
      const file = await requestItineraryPdf(planResult, { signal: controller.signal })
      if (activeRequest.current !== controller || controller.signal.aborted) return
      savePdfFile(file)
      setState({
        snapshot: planResult, pending: false,
        message: 'PDF 파일을 브라우저에 전달했습니다. 다운로드 목록을 확인해 주세요.',
      })
    } catch (error) {
      if (activeRequest.current !== controller || controller.signal.aborted) return
      setState({
        snapshot: planResult, pending: false, error: true,
        message: error.message || 'PDF를 준비하지 못했습니다. 다시 시도해 주세요.',
      })
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null
    }
  }

  return (
    <div className="itinerary-pdf-download">
      <button
        type="button"
        className="secondary-button"
        disabled={disabled || isDownloading}
        aria-busy={isDownloading}
        onClick={handleDownload}
      >
        {isDownloading ? 'PDF 준비 중…' : '전체 일정 PDF 다운로드'}
      </button>
      <p className="itinerary-pdf-description">현재 표시된 일정의 전체 날짜와 확인사항을 저장합니다.</p>
      {current?.message && (
        <p
          className={current.error ? 'itinerary-pdf-error' : 'itinerary-pdf-status'}
          role={current.error ? 'alert' : 'status'}
        >
          {current.message}
        </p>
      )}
    </div>
  )
}

export default ItineraryPdfDownload
