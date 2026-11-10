import { useEffect, useMemo, useRef, useState } from 'react'
import ConversationPanel from './ConversationPanel.jsx'
import TravelOptions from './TravelOptions.jsx'
import TravelPlan from './TravelPlan.jsx'
import ItineraryMap from './ItineraryMap.jsx'
import { isChatOptions, requestTravelChat } from './services/chatApi.js'
import {
  createEmptyChatSession,
  loadChatSession,
  saveChatSession,
} from './services/chatSession.js'
import { getCenterOrigin } from './services/originLocation.js'
import {
  DEFAULT_ORIGIN,
  formatTripDuration,
} from './services/travelOptions.js'
import logoImage from './assets/gachi-jeju-logo.png'

function getRetryMessage(messages) {
  const last = messages.at(-1)
  return last?.role === 'user' && last.status === 'failed' ? last : null
}

function reconcileOrigin(place, options) {
  const latitude = options.departure_latitude
  const longitude = options.departure_longitude
  if (latitude === null || longitude === null) return null

  if (
    place?.name === options.departure
    && place.latitude === latitude
    && place.longitude === longitude
  ) {
    return place
  }

  return {
    id: `chat-origin:${latitude},${longitude}`,
    name: options.departure || '출발지',
    address: '',
    latitude,
    longitude,
    source: 'agent',
  }
}

function TravelWorkspace() {
  const [session, setSession] = useState(loadChatSession)
  const [requestState, setRequestState] = useState(() => ({
    isSending: false,
    error: getRetryMessage(session.messages)
      ? { message: '이전 요청의 답변을 확인하지 못했습니다. 다시 보낼 수 있습니다.' }
      : null,
  }))
  const [viewKey, setViewKey] = useState(0)
  const busyRef = useRef(false)

  const { options, originPlace, messages, planResult, selectedDay } = session
  const { isSending, error } = requestState
  const retryMessage = getRetryMessage(messages)
  const days = planResult?.plan.days ?? []
  const planOptions = planResult?.options

  const hasChangedOptions = planOptions && Object.keys(options).some(
    (key) => JSON.stringify(options[key]) !== JSON.stringify(planOptions[key]),
  )

  const visiblePlan = useMemo(() => {
    if (!planResult) return null

    const day = planResult.plan.days.find((item) => item.day === selectedDay)
      ?? planResult.plan.days[0]

    return { ...planResult.plan, days: day ? [day] : [] }
  }, [planResult, selectedDay])

  useEffect(() => {
    saveChatSession(session)
  }, [session])

  useEffect(() => {
    if (
      isSending
      || options.departure !== DEFAULT_ORIGIN
      || options.departure_latitude !== null
      || options.departure_longitude !== null
    ) {
      return
    }

    let active = true

    getCenterOrigin().then((place) => {
      if (!active || busyRef.current) return

      setSession((previous) => {
        if (
          previous.options.departure !== DEFAULT_ORIGIN
          || previous.options.departure_latitude !== null
          || previous.options.departure_longitude !== null
        ) {
          return previous
        }

        return {
          ...previous,
          originPlace: place,
          options: {
            ...previous.options,
            departure_latitude: place.latitude,
            departure_longitude: place.longitude,
          },
        }
      })
    }).catch(() => {
      // 좌표를 확인하지 못하면 출발지 이름으로 요청합니다.
    })

    return () => { active = false }
  }, [
    isSending,
    options.departure,
    options.departure_latitude,
    options.departure_longitude,
  ])

  function handleOptionsChange(patch) {
    if (busyRef.current) return

    setSession((previous) => {
      const next = { ...previous.options, ...patch }
      if (!isChatOptions(next)) return previous

      return {
        ...previous,
        options: next,
        originPlace: reconcileOrigin(previous.originPlace, next),
      }
    })
  }

  function handleOriginConfirm(place) {
    if (busyRef.current) return

    setSession((previous) => {
      const next = {
        ...previous.options,
        departure: place.name,
        departure_latitude: place.latitude,
        departure_longitude: place.longitude,
      }

      if (!isChatOptions(next)) return previous

      return { ...previous, options: next, originPlace: place }
    })
  }

  async function receiveReply(messageId, content) {
    try {
      const response = await requestTravelChat({
        message: content,
        history: messages.filter((item) => item.status === 'sent'),
        options,
        currentPlan: planResult?.plan ?? null,
      })

      const assistantMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: response.reply,
        status: 'sent',
      }

      setSession((previous) => {
        const nextResult = response.plan === null ? previous.planResult : response
        const nextDays = nextResult?.plan.days ?? []

        return {
          ...previous,
          options: response.options,
          originPlace: reconcileOrigin(previous.originPlace, response.options),
          messages: [
            ...previous.messages.map((item) => (
              item.id === messageId ? { ...item, status: 'sent' } : item
            )),
            assistantMessage,
          ],
          planResult: nextResult,
          selectedDay: nextDays.some((item) => item.day === previous.selectedDay)
            ? previous.selectedDay
            : nextDays[0]?.day ?? 1,
        }
      })

      setRequestState({ isSending: false, error: null })
    } catch (cause) {
      setSession((previous) => ({
        ...previous,
        messages: previous.messages.map((item) => (
          item.id === messageId ? { ...item, status: 'failed' } : item
        )),
      }))

      setRequestState({
        isSending: false,
        error: {
          message: cause?.message || '답변을 받지 못했습니다. 다시 시도해 주세요.',
          details: cause?.details ?? [],
        },
      })
    } finally {
      busyRef.current = false
    }
  }

  function handleSend(content) {
    const text = content.trim()
    if (busyRef.current || !text || text.length > 1000) return false

    const messageId = crypto.randomUUID()
    busyRef.current = true

    setRequestState({ isSending: true, error: null })
    setSession((previous) => ({
      ...previous,
      messages: [
        ...previous.messages,
        { id: messageId, role: 'user', content: text, status: 'pending' },
      ],
    }))

    void receiveReply(messageId, text)
    return true
  }

  function handleRetry() {
    if (busyRef.current || !retryMessage) return

    busyRef.current = true
    setRequestState({ isSending: true, error: null })

    setSession((previous) => ({
      ...previous,
      messages: previous.messages.map((item) => (
        item.id === retryMessage.id ? { ...item, status: 'pending' } : item
      )),
    }))

    void receiveReply(retryMessage.id, retryMessage.content)
  }

  function handleNewTrip() {
    if (busyRef.current) return

    setSession(createEmptyChatSession())
    setRequestState({ isSending: false, error: null })
    setViewKey((previous) => previous + 1)
  }

  return (
    <div className="travel-workspace">
      <header className="workspace-header">
        <div>
          <h1>
            <img className="brand-logo" src={logoImage} alt="같이 가는 제주" />
          </h1>
        </div>

        <button
          type="button"
          className="secondary-button"
          disabled={isSending}
          onClick={handleNewTrip}
        >
          새 여행 시작
        </button>
      </header>

      <main className="workspace-layout" key={viewKey}>
        <TravelOptions
          options={options}
          originPlace={originPlace}
          onChange={handleOptionsChange}
          onOriginConfirm={handleOriginConfirm}
          disabled={isSending}
        />

        <ConversationPanel
          messages={messages}
          onSend={handleSend}
          isSending={isSending}
          error={error}
          onRetry={retryMessage ? handleRetry : undefined}
        />

        <section
          className="workspace-results"
          aria-labelledby="workspace-results-title"
        >
          <div className="workspace-panel-heading">
            <h2 id="workspace-results-title">여행 지도와 일정</h2>
            <p>지도에서 장소를 확인하고 날짜별 일정을 살펴보세요.</p>
          </div>

          <div className="workspace-results-content">
            {planResult ? (
              <>
                <p className="workspace-plan-summary">
                  {planOptions.start_date || '출발일 확인 필요'}
                  {' · '}
                  {formatTripDuration(planOptions.trip_days)}
                </p>

                {hasChangedOptions && (
                  <p className="workspace-result-notice">
                    변경한 옵션은 다음 메시지에 함께 반영합니다.
                    아래에는 이전에 만든 지도와 일정을 표시합니다.
                  </p>
                )}

                {days.length > 1 && (
                  <div
                    className="workspace-day-buttons"
                    role="group"
                    aria-label="확인할 여행 날짜"
                  >
                    {days.map((day) => (
                      <button
                        key={day.day}
                        type="button"
                        aria-pressed={selectedDay === day.day}
                        onClick={() => setSession((previous) => ({
                          ...previous,
                          selectedDay: day.day,
                        }))}
                      >
                        {day.day}일차
                      </button>
                    ))}
                  </div>
                )}

                <ItineraryMap
                  day={visiblePlan?.days[0] ?? null}
                  options={planOptions}
                />

                <div
                  className="workspace-schedule-scroll"
                  role="region"
                  aria-label="추천 일정표와 확인사항"
                  tabIndex={0}
                >
                  {planResult.warnings.length > 0 && (
                    <div className="plan-notice">
                      <h3>확인할 사항</h3>
                      <ul>
                        {planResult.warnings.map((warning, index) => (
                          <li key={index}>{warning}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <TravelPlan plan={visiblePlan} autoFocus={false} />
                </div>
              </>
            ) : (
              <>
                <ItineraryMap options={options} />

                <p className="workspace-result-empty">
                  가고 싶은 곳을 대화로 알려주세요.
                  일정이 만들어지면 이곳에 표시합니다.
                </p>
              </>
            )}
          </div>
        </section>
      </main>
    </div>
  )
}

export default TravelWorkspace