import { useRef, useState } from 'react'
import { MAX_PLAN_MESSAGE_LENGTH } from './services/planRequest.js'

function TravelChat({ messages, onSend, onRemove, disabled = false }) {
  const [draft, setDraft] = useState('')
  const inputRef = useRef(null)
  const requestMessages = messages.filter((item) => item?.role === 'user')
  const registeredText = requestMessages
    .map((item) => item.content.trim())
    .filter(Boolean)
    .join('\n')
  const content = draft.trim()
  const combinedLength = registeredText.length
    + (registeredText && content ? 1 : 0)
    + content.length
  const isTooLong = combinedLength > MAX_PLAN_MESSAGE_LENGTH

  function handleSubmit(event) {
    event.preventDefault()
    if (disabled || !content || isTooLong) {
      return
    }

    const accepted = onSend(content)
    if (accepted !== false) {
      setDraft('')
    }
    inputRef.current?.focus()
  }

  function handleRemove(messageId) {
    if (disabled || typeof onRemove !== 'function') {
      return
    }
    onRemove(messageId)
    inputRef.current?.focus()
  }

  return (
    <section className="travel-chat" aria-labelledby="travel-chat-title">
      <h2 id="travel-chat-title">어떤 여행을 더하고 싶으신가요?</h2>
      <p id="travel-request-help">
        요청 추가를 눌러 목록에 등록한 내용을 일정에 반영합니다.
        전체 요청은 합쳐서 1,000자까지 입력할 수 있습니다.
      </p>

      <div
        className="chat-messages"
        role="log"
        aria-label="등록한 여행 요청"
        tabIndex={0}
      >
        {requestMessages.length === 0 ? (
          <p className="chat-empty">아직 등록한 추가 요청이 없습니다.</p>
        ) : (
          <ol className="chat-message-list">
            {requestMessages.map((message, index) => (
              <li key={message.id} className="chat-message">
                <div className="chat-message-header">
                  <p className="chat-message-author">추가 요청 {index + 1}</p>
                  {typeof onRemove === 'function' && (
                    <button
                      type="button"
                      className="chat-remove-button"
                      disabled={disabled}
                      onClick={() => handleRemove(message.id)}
                      aria-label={`${index + 1}번째 추가 요청 삭제`}
                    >
                      삭제
                    </button>
                  )}
                </div>
                <p className="chat-message-content">{message.content}</p>
              </li>
            ))}
          </ol>
        )}
      </div>

      <form className="chat-form" onSubmit={handleSubmit}>
        <label htmlFor="travel-request">추가 요청</label>
        <textarea
          id="travel-request"
          ref={inputRef}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={4}
          maxLength={MAX_PLAN_MESSAGE_LENGTH}
          disabled={disabled}
          aria-invalid={isTooLong}
          aria-describedby={
            'travel-request-help travel-request-count'
            + (isTooLong ? ' travel-request-error' : '')
          }
          placeholder="오래 걷는 장소는 피하고, 바다가 보이는 곳도 넣어 주세요."
          required
        />

        {isTooLong && (
          <p id="travel-request-error" className="form-error" role="alert">
            전체 요청이 1,000자를 넘었습니다.
            입력 내용을 줄이거나 등록한 요청을 삭제해 주세요.
          </p>
        )}

        <div className="chat-actions">
          <span id="travel-request-count">
            전체 요청: {combinedLength} / 1,000자
          </span>
          <button
            type="submit"
            className="primary-button"
            disabled={disabled || !content || isTooLong}
          >
            요청 추가
          </button>
        </div>
      </form>
    </section>
  )
}

export default TravelChat