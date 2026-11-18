import { useEffect, useId, useRef, useState } from 'react'
import { requestItineraryEmail } from './services/itineraryEmailApi.js'

function ItineraryEmailDialog({ planResult, disabled = false }) {
  const dialogRef = useRef(null)
  const inputRef = useRef(null)
  const activeRequest = useRef(null)
  const [recipient, setRecipient] = useState('')
  const [state, setState] = useState(null)
  const id = useId()

  const current = state
    && state.snapshot === planResult
    && state.recipient === recipient.trim()
    ? state : null

  const pending = current?.pending === true

  useEffect(() => {
    const dialog = dialogRef.current
    return () => {
      activeRequest.current?.abort()
      activeRequest.current = null
      dialog?.close()
    }
  }, [planResult])

  function openDialog() {
    if (disabled) return
    dialogRef.current?.showModal()
    inputRef.current?.focus()
  }

  function closeDialog() {
    if (!activeRequest.current) dialogRef.current?.close()
  }

  async function handleSubmit(event) {
    event.preventDefault()
    if (disabled || activeRequest.current) return

    const controller = new AbortController()
    const address = recipient.trim()
    activeRequest.current = controller
    setState({
      snapshot: planResult,
      recipient: address,
      pending: true,
    })

    try {
      const result = await requestItineraryEmail(
        planResult, address, { signal: controller.signal },
      )
      if (
        activeRequest.current !== controller
        || controller.signal.aborted
      ) return

      setState({
        snapshot: planResult,
        recipient: address,
        pending: false,
        message: result.message,
        error: false,
      })
    } catch (error) {
      if (
        activeRequest.current !== controller
        || controller.signal.aborted
      ) return

      setState({
        snapshot: planResult,
        recipient: address,
        pending: false,
        error: true,
        message: error?.message
          || '메일 접수 결과를 확인하지 못했습니다. 수신함과 스팸함을 확인해 주세요.',
      })
    } finally {
      if (activeRequest.current === controller) {
        activeRequest.current = null
      }
    }
  }

  return (
    <>
      <button
        type="button"
        className="secondary-button"
        disabled={disabled}
        aria-haspopup="dialog"
        aria-controls={id}
        onClick={openDialog}
      >
        이메일로 받기
      </button>

      <dialog
        ref={dialogRef}
        id={id}
        className="itinerary-email-dialog"
        aria-labelledby={id + '-title'}
        aria-describedby={id + '-description'}
        onCancel={(event) => {
          if (activeRequest.current) event.preventDefault()
        }}
      >
        <h2 id={id + '-title'}>일정 PDF 이메일로 받기</h2>
        <p id={id + '-description'}>
          현재 표시된 일정의 전체 날짜와 확인사항을 PDF로 보냅니다.
        </p>

        <form onSubmit={handleSubmit} aria-busy={pending}>
          <label htmlFor={id + '-recipient'}>
            받는 이메일 주소
          </label>
          <input
            ref={inputRef}
            id={id + '-recipient'}
            name="recipient"
            type="email"
            autoComplete="email"
            inputMode="email"
            placeholder="user@example.com"
            maxLength={254}
            required
            value={recipient}
            disabled={disabled || pending}
            onChange={(event) => setRecipient(event.target.value)}
          />

          {pending && (
            <p role="status">
              메일 서버의 접수 결과를 확인하고 있습니다.
            </p>
          )}

          {current?.message && (
            <p
              className={current.error
                ? 'itinerary-email-feedback is-error'
                : 'itinerary-email-feedback'}
              role={current.error ? 'alert' : 'status'}
            >
              {current.message}
            </p>
          )}

          <div className="itinerary-email-form-actions">
            <button
              type="button"
              className="secondary-button"
              disabled={pending}
              onClick={closeDialog}
            >
              닫기
            </button>
            <button
              type="submit"
              className="secondary-button itinerary-email-submit"
              disabled={disabled || pending}
            >
              {pending ? '발송 요청 중…' : 'PDF 보내기'}
            </button>
          </div>
        </form>
      </dialog>
    </>
  )
}

export default ItineraryEmailDialog