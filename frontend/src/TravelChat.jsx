import { useState } from 'react'

const MAX_REQUEST_LENGTH = 1000

function TravelChat({ messages, onSend }) {
  const [draft, setDraft] = useState('')

  function handleSubmit(event) {
    event.preventDefault()

    const content = draft.trim()
    if (!content) {
      return
    }

    onSend(content)
    setDraft('')
  }

  return (
    <section className="travel-chat" aria-labelledby="travel-chat-title">
      <h2 id="travel-chat-title">어떤 여행을 더하고 싶으신가요?</h2>
      <p id="travel-request-help">
        위에서 선택한 여행 조건에 더하고 싶은 내용을 적어 주세요.
      </p>
      <p className="chat-notice">
        지금은 요청을 기록할 수 있습니다. 추천 답변은 준비 중입니다.
      </p>

      <div
        className="chat-messages"
        role="log"
        aria-label="작성한 여행 요청"
        tabIndex={0}
      >
        {messages.length === 0 ? (
          <p className="chat-empty">아직 작성한 요청이 없습니다.</p>
        ) : (
          <ol className="chat-message-list">
            {messages.map((message) => (
              <li key={message.id} className="chat-message">
                <p className="chat-message-author">나의 요청</p>
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
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={4}
          maxLength={MAX_REQUEST_LENGTH}
          aria-describedby="travel-request-help travel-request-limit"
          placeholder="오래 걷는 장소는 피하고, 바다가 보이는 곳도 넣어 주세요."
          required
        />

        <div className="chat-actions">
          <span id="travel-request-limit">
            {draft.length} / {MAX_REQUEST_LENGTH}자
          </span>
          <button
            type="submit"
            className="primary-button"
            disabled={!draft.trim()}
          >
            요청 추가
          </button>
        </div>
      </form>
    </section>
  )
}

export default TravelChat