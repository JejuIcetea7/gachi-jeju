import { useEffect, useRef, useState } from 'react'

const FIELD_LABELS = {
  message: '메시지',
  history: '이전 대화',
  options: '선택 옵션',
  current_plan: '이전 일정',
  plan: '생성된 일정',
  departure: '출발지',
  departure_latitude: '출발지 위치',
  departure_longitude: '출발지 위치',
  start_date: '출발일',
  trip_days: '여행 기간',
  transport: '이동수단',
  mobility_aid: '이동 보조기구',
  travel_styles: '여행 종류',
  requirements: '접근 조건',
  return_to_origin: '귀환 여부',
  body: '여행 요청',
}

function formatDetail(detail) {
  const parts = detail.field.split('.')
  const field = parts[0] === 'options' ? parts[1] : parts[0]
  const label = Object.hasOwn(FIELD_LABELS, field)
    ? FIELD_LABELS[field]
    : '여행 요청'

  return `${label}: ${detail.reason.replace(/^Value error,\s*/, '')}`
}

function SvgIcon({ size = 20, strokeWidth = 1.8, children }) {
  return (
    <svg
      className="workspace-icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

function ChatIcon({ size }) {
  return (
    <SvgIcon size={size}>
      <path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-5 4v-4H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" />
    </SvgIcon>
  )
}

function UserIcon() {
  return (
    <SvgIcon size={18}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </SvgIcon>
  )
}

function ArrowIcon() {
  return (
    <SvgIcon size={18} strokeWidth={2}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </SvgIcon>
  )
}

function ConversationPanel({
  messages = [],
  onSend,
  isSending = false,
  error = null,
  notice = null,
  onRetry,
  onCancel,
  onOpenPlan,
  planLabel = '',
  headerAction = null,
  mascotImages = {},
}) {
  const [draft, setDraft] = useState('')
  const inputRef = useRef(null)
  const logRef = useRef(null)
  const followLatestRef = useRef(true)
  const wasSendingRef = useRef(isSending)
  const content = draft.trim()
  const canSend = !isSending
    && content.length > 0
    && draft.length <= 1000
    && typeof onSend === 'function'

  const lastMessage = messages.at(-1)
  const showPlanBanner = typeof onOpenPlan === 'function'
    && lastMessage?.role === 'assistant'

  const details = Array.isArray(error?.details)
    ? error.details.filter((item) => (
      typeof item?.field === 'string'
      && typeof item?.reason === 'string'
    ))
    : []

  const retryButton = typeof onRetry === 'function' ? (
    <button
      type="button"
      className="secondary-button"
      disabled={isSending}
      onClick={() => {
        followLatestRef.current = true
        onRetry()
      }}
    >
      이 요청 다시 보내기
    </button>
  ) : null

  // 사이드바가 열리고 닫혀 대화 영역 크기가 바뀌어도 최신 메시지를 계속 보여 줍니다.
  useEffect(() => {
    const log = logRef.current
    if (!log) return

    const observer = new ResizeObserver(() => {
      if (followLatestRef.current) {
        log.scrollTop = log.scrollHeight
      }
    })

    observer.observe(log)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const log = logRef.current
    if (log && followLatestRef.current) {
      log.scrollTop = log.scrollHeight
    }
  }, [messages.length, isSending])

  useEffect(() => {
    if (wasSendingRef.current && !isSending) {
      const active = document.activeElement
      if (
        active === document.body
        || inputRef.current?.form?.contains(active)
      ) {
        inputRef.current?.focus()
      }
    }
    wasSendingRef.current = isSending
  }, [isSending])

  function handleSubmit(event) {
    event.preventDefault()
    if (!canSend) return

    followLatestRef.current = true
    const accepted = onSend(content)
    if (accepted !== false) setDraft('')
  }

  function handleKeyDown(event) {
    if (
      event.key !== 'Enter'
      || event.shiftKey
      || event.nativeEvent.isComposing
      || event.nativeEvent.keyCode === 229
    ) {
      return
    }

    event.preventDefault()
    event.currentTarget.form?.requestSubmit()
  }

  return (
    <section
      id="workspace-chat-pane"
      className="workspace-chat"
      aria-labelledby="conversation-title"
      data-tour="chat"
    >
      <div className="workspace-card-heading">
        <div className="conversation-heading">
          <span className="conversation-heading-icon">
            <ChatIcon size={24} />
          </span>
          <div className="workspace-heading-text">
            <h2 id="conversation-title">함께 여행을 계획해요</h2>
            <p>가고 싶은 곳과 필요한 조건을 편하게 알려주세요.</p>
          </div>
        </div>
        {headerAction}
      </div>

      <div
        ref={logRef}
        className="conversation-log"
        role="log"
        aria-label="여행 도우미와 나눈 대화"
        aria-live="polite"
        aria-relevant="additions"
        aria-busy={isSending}
        tabIndex={0}
        onScroll={(event) => {
          const element = event.currentTarget
          followLatestRef.current =
            element.scrollHeight
            - element.scrollTop
            - element.clientHeight < 80
        }}
      >
        {messages.length === 0 ? (
          <div className="conversation-empty">
            {mascotImages.greeting && (
              <img className="conversation-empty-mascot" src={mascotImages.greeting} alt="" />
            )}
            <p>어떤 제주 여행을 떠나고 싶으신가요?</p>
            <p>
              “부모님과 2박 3일 여행을 하고 싶어요.
              오래 걷지 않는 곳으로 추천해 주세요.”
            </p>
            <p>위의 옵션은 필요한 항목만 선택하시면 됩니다.</p>
          </div>
        ) : (
          <ol className="conversation-messages">
            {messages.map((message) => (
              <li
                key={message.id}
                className={`conversation-message ${message.role === 'user'
                  ? 'is-user' : 'is-assistant'}${message.status === 'failed'
                  ? ' is-failed' : ''}`}
              >
                {message.role === 'user' && (
                  <span className="conversation-avatar">
                    <UserIcon />
                  </span>
                )}
                {message.role !== 'user' && mascotImages.assistant && (
                  <span className="conversation-avatar is-mascot">
                    <img src={mascotImages.assistant} alt="" />
                  </span>
                )}
                {message.role !== 'user' && !mascotImages.assistant && (
                  <span className="conversation-avatar">
                    <ChatIcon size={18} />
                  </span>
                )}
                <div className="conversation-bubble">
                  <p className="conversation-author">
                    {message.role === 'user' ? '나' : '같이 가는 제주'}
                  </p>
                  <p className="conversation-content">{message.content}</p>
                  {message.status === 'failed' && (
                    <p className="conversation-failed">답변 확인 실패</p>
                  )}
                  {message.status === 'cancelled' && (
                    <p className="conversation-cancelled">요청 취소</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>

      {showPlanBanner && (
        <button
          type="button"
          className="conversation-plan-banner"
          onClick={onOpenPlan}
        >
          <SvgIcon size={22}>
            <path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z" />
            <path d="M9 4v14M15 6v14" />
          </SvgIcon>
          <strong>지도·일정 열기</strong>
          {planLabel && <span>{planLabel}</span>}
          <span className="conversation-plan-banner-action">
            열기
            <ArrowIcon />
          </span>
        </button>
      )}

      {isSending && (
        <div className="conversation-progress">
          {mascotImages.waiting ? (
            <img className="conversation-state-mascot is-moving" src={mascotImages.waiting} alt="" />
          ) : (
            <span className="conversation-progress-dot" aria-hidden="true" />
          )}
          <p role="status">여행 정보를 확인하고 답변을 준비하고 있습니다.</p>
          {typeof onCancel === 'function' && (
            <button type="button" className="secondary-button" onClick={onCancel}>
              요청 취소
            </button>
          )}
        </div>
      )}

      {!isSending && !error && notice && (
        <div className="conversation-notice" role="status">
          <p>{notice}</p>
          {retryButton}
        </div>
      )}

      {error && (
        <div className="conversation-error" role="alert">
          {mascotImages.error && (
            <img className="conversation-state-mascot" src={mascotImages.error} alt="" />
          )}
          <div className="conversation-error-body">
            <p>{error.message}</p>
            {details.length > 0 && (
              <ul>
                {details.map((detail, index) => (
                  <li key={index}>{formatDetail(detail)}</li>
                ))}
              </ul>
            )}
            {retryButton}
          </div>
        </div>
      )}

      <form className="conversation-form" onSubmit={handleSubmit}>
        <label htmlFor="conversation-input" className="sr-only">
          여행 요청
        </label>
        <textarea
          ref={inputRef}
          id="conversation-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isSending}
          maxLength={1000}
          rows={4}
          aria-describedby="conversation-input-help conversation-input-count"
          placeholder="어디에 가고 싶으세요? 필요한 조건도 함께 알려주세요."
        />
        <div className="conversation-form-footer">
          <div>
            <p id="conversation-input-help">
              Enter로 보내기 · Shift+Enter로 줄바꿈
            </p>
            <span id="conversation-input-count">
              {draft.length} / 1,000자
            </span>
          </div>
          <button
            type="submit"
            className="primary-button"
            disabled={!canSend}
          >
            {isSending ? '답변 대기 중' : '보내기'}
            {!isSending && <ArrowIcon />}
          </button>
        </div>
      </form>
    </section>
  )
}

export default ConversationPanel