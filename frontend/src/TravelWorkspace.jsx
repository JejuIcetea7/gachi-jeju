import { useEffect, useMemo, useRef, useState } from 'react'
import ConversationPanel from './ConversationPanel.jsx'
import TravelOptions from './TravelOptions.jsx'
import TravelPlan from './TravelPlan.jsx'
import ItineraryPdfDownload from './ItineraryPdfDownload.jsx'
import ItineraryEmailDialog from './ItineraryEmailDialog.jsx'
import LodgingPanel from './LodgingPanel.jsx'
import {
  chooseLodging, getLodgingSearchCenter, getLodgingSelectionMessage, getLodgingStay,
  normalizeLodgingOptions, removeLodging, resizeLodgingOptions,
} from './services/lodgingPlanning.js'
import ItineraryMap from './ItineraryMap.jsx'
import { isChatOptions, requestTravelChat } from './services/chatApi.js'
import { createChatRequestTracker } from './services/chatRequestTracker.js'
import {
  createEmptyChatSession,
  loadChatSession,
  markChatMessageCancelled,
  saveChatSession,
} from './services/chatSession.js'
import { getCenterOrigin } from './services/originLocation.js'
import {
  DEFAULT_ORIGIN,
  formatTripDuration,
} from './services/travelOptions.js'
import logoImage from './assets/gachi-jeju-logo.png'
import ResizableWorkspace from './ResizableWorkspace.jsx'
import GuideTour from './GuideTour.jsx'
import mascotPoint from './assets/mascot/hallabong-point.webp'
import mascotQuestion from './assets/mascot/basalt-question.webp'
import mascotMap from './assets/mascot/duo-map.webp'
import mascotCamera from './assets/mascot/hallabong-camera.webp'
import mascotThumbsUp from './assets/mascot/hallabong-thumbs-up.webp'
import mascotWave from './assets/mascot/hallabong-wave.webp'
import mascotFace from './assets/mascot/hallabong-face.webp'
import mascotMagnifier from './assets/mascot/basalt-magnifier.webp'
import mascotFlustered from './assets/mascot/basalt-flustered.webp'
import { getTripExampleMessages } from './services/tripExamples.js'

const CANCELLED_NOTICE = '요청을 취소했습니다. 내용을 바꾸어 보내거나 이 요청을 다시 보낼 수 있습니다.'
const SIDEBAR_ID = 'workspace-sidebar'
const SIDEBAR_TITLE_ID = 'workspace-results-title'
const GUIDE_TOUR_STORAGE_KEY = 'gachi-jeju.guide-tour.v1'

// 대화창에 넣는 마스코트: 시작 전 인사, 답변 아이콘, 답변 대기, 오류
const CONVERSATION_MASCOTS = {
  greeting: mascotWave,
  assistant: mascotFace,
  waiting: mascotMagnifier,
  error: mascotFlustered,
}
const DESKTOP_QUERY = '(min-width: 901px)'

// 첫 방문 가이드 단계입니다. target은 화면 요소의 data-tour 값입니다.
const GUIDE_TOUR_STEPS = [
  {
    target: 'options',
    placement: 'bottom',
    title: '여행 옵션',
    text: '출발지·날짜·이동 방법·꼭 필요한 접근 조건을 고르면 일정에 반영돼요. 고르지 않고 대화로 말해도 괜찮아요. 오른쪽 위 \'옵션 접기\'로 접고 펼칠 수 있어요.',
    character: mascotPoint,
  },
  {
    target: 'chat',
    placement: 'top',
    title: '대화로 일정 만들기',
    text: '가고 싶은 곳과 필요한 조건을 적어 보내면 일정을 만들어 드려요. 만든 일정도 대화로 고칠 수 있어요.',
    character: mascotQuestion,
  },
  {
    target: 'map',
    placement: 'left',
    title: '여행 지도',
    text: '일정이 만들어지면 오른쪽에 지도와 일정표가 열려요. 지도에서는 출발지·방문 장소와 이동 경로를 볼 수 있어요.',
    character: mascotMap,
  },
  {
    target: 'plan-area',
    placement: 'left',
    title: '일정표',
    text: '날짜별 방문 시간과 이동 방법이 이곳에 표시돼요. 장소 이름을 누르면 접근성 안내와 사진을 볼 수 있어요.',
    character: mascotCamera,
  },
  {
    target: 'export',
    placement: 'bottom',
    title: '일정 저장하기',
    text: '만든 일정은 PDF로 내려받거나 이메일로 받을 수 있어요.',
    character: mascotThumbsUp,
  },
  {
    target: 'chat',
    placement: 'right',
    title: '보면서 고치기',
    text: '지도와 일정을 보면서도 왼쪽 대화창으로 바로 일정을 고칠 수 있어요.',
    character: mascotWave,
  },
]

function hasSeenGuideTour() {
  let seen = false
  try {
    seen = window.localStorage.getItem(GUIDE_TOUR_STORAGE_KEY) === 'done'
  } catch {
    // 저장소를 읽지 못하면 처음 방문으로 봅니다.
  }
  return seen
}

function markGuideTourSeen() {
  try {
    window.localStorage.setItem(GUIDE_TOUR_STORAGE_KEY, 'done')
  } catch {
    // 저장하지 못해도 가이드는 정상으로 닫습니다.
  }
}

// 가이드 단계마다 사이드바를 열지 정합니다. 1·2단계는 메인 화면, 3~5단계는 사이드바를 봅니다.
// 6단계는 넓은 화면에서는 사이드바 옆 대화창을, 좁은 화면에서는 사이드바를 닫고 대화창을 봅니다.
// 가이드를 시작하기 전에 메인 화면과 사이드바 스크롤을 맨 위로 올립니다.
function scrollWorkspaceToTop() {
  const scrollAreas = document.querySelectorAll('.workspace-main, .workspace-sidebar-scroll')
  for (const scrollArea of scrollAreas) {
    scrollArea.scrollTo({ top: 0 })
  }
}

function getGuideSidebarState(stepIndex) {
  let shouldOpen = true
  if (stepIndex <= 1) {
    shouldOpen = false
  }
  const isDesktop = window.matchMedia(DESKTOP_QUERY).matches
  if (stepIndex === 5 && !isDesktop) {
    shouldOpen = false
  }
  return shouldOpen
}

function SvgIcon({ size = 20, children }) {
  return (
    <svg
      className="workspace-icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

function MapIcon({ size }) {
  return (
    <SvgIcon size={size}>
      <path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z" />
      <path d="M9 4v14M15 6v14" />
    </SvgIcon>
  )
}

function CloseIcon() {
  return (
    <SvgIcon size={18}>
      <path d="M6 6l12 12M18 6 6 18" />
    </SvgIcon>
  )
}

function RestartIcon() {
  return (
    <SvgIcon size={18}>
      <path d="M4 12a8 8 0 1 0 2.3-5.6" />
      <path d="M4 4v4h4" />
    </SvgIcon>
  )
}

function GuideIcon() {
  return (
    <SvgIcon size={18}>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14" />
      <path d="M12 17h.01" />
    </SvgIcon>
  )
}

function InfoIcon() {
  return (
    <SvgIcon>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </SvgIcon>
  )
}

function getVisitSummaries(day) {
  const visitSummaries = []
  if (!day) return visitSummaries

  let visitNumber = 0
  for (const item of day.items) {
    if (item.item_type !== 'visit') continue

    visitNumber += 1
    visitSummaries.push({
      number: visitNumber,
      startTime: item.start_time,
      title: item.title,
    })
  }

  return visitSummaries
}

function getRetryMessage(messages) {
  const last = messages.at(-1)
  return last?.role === 'user' && ['failed', 'cancelled'].includes(last.status)
    ? last : null
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
  const [requestTracker] = useState(createChatRequestTracker)
  const [requestState, setRequestState] = useState(() => {
    const previous = getRetryMessage(session.messages)
    return {
      isSending: false,
      error: previous?.status === 'failed'
        ? { message: '이전 요청의 답변을 확인하지 못했습니다. 다시 보낼 수 있습니다.' }
        : null,
      notice: previous?.status === 'cancelled' ? CANCELLED_NOTICE : null,
    }
  })
  const [viewKey, setViewKey] = useState(0)
  const [optionsOpen, setOptionsOpen] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [showAllDays, setShowAllDays] = useState(false)
  const [tourActive, setTourActive] = useState(false)
  const [tourStep, setTourStep] = useState(0)
  const sidebarBeforeTourRef = useRef(false)
  const focusBeforeTourRef = useRef(null)
  const busyRef = useRef(false)

  const { options, originPlace, messages, planResult, selectedDay } = session
  const tripExamples = messages.length === 0 && !planResult
    ? getTripExampleMessages(options, originPlace) : []

  // 일정이 새로 만들어지거나 바뀌면 사이드바를 엽니다. 새로고침으로 복원한 일정은 열지 않습니다.
  const [shownPlanResult, setShownPlanResult] = useState(planResult)
  if (shownPlanResult !== planResult) {
    setShownPlanResult(planResult)
    if (planResult) {
      setSidebarOpen(true)
    }
  }
  const { isSending, error, notice } = requestState
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

  // 처음 방문하면 화면이 뜬 뒤 가이드를 자동으로 시작합니다.
  useEffect(() => {
    if (hasSeenGuideTour()) return

    const timer = window.setTimeout(() => {
      scrollWorkspaceToTop()
      sidebarBeforeTourRef.current = false
      focusBeforeTourRef.current = null
      setShowAllDays(false)
      setTourStep(0)
      setTourActive(true)
      setSidebarOpen(false)
    }, 120)
    return () => window.clearTimeout(timer)
  }, [])

  useEffect(() => () => {
    requestTracker.reset()
    busyRef.current = false
  }, [requestTracker])

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
      const changed = { ...previous.options, ...patch }
      const next = Object.hasOwn(patch, 'trip_days')
        ? resizeLodgingOptions(changed)
        : normalizeLodgingOptions(changed)
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

  async function receiveReply(ticket, content, requestOptions = options) {
    const messageId = ticket.messageId
    try {
      const response = await requestTravelChat({
        message: content,
        history: messages.filter((item) => item.status === 'sent'),
        options: requestOptions,
        currentPlan: planResult?.plan ?? null,
      }, {
        signal: ticket.controller.signal,
        requestId: ticket.requestId,
      })
      if (!requestTracker.isCurrent(ticket)) return

      const assistantMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: response.reply,
        status: 'sent',
      }

      setSession((previous) => {
        if (!requestTracker.isLatest(ticket)) return previous
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

      setRequestState((previous) => requestTracker.isLatest(ticket)
        ? { isSending: false, error: null, notice: null }
        : previous)
    } catch (cause) {
      if (!requestTracker.isCurrent(ticket)) return
      const cancelled = cause?.code === 'request_cancelled'

      setSession((previous) => {
        if (!requestTracker.isLatest(ticket)) return previous
        if (cancelled) return markChatMessageCancelled(previous, messageId)
        return {
          ...previous,
          messages: previous.messages.map((item) => (
            item.id === messageId ? { ...item, status: 'failed' } : item
          )),
        }
      })

      setRequestState((previous) => requestTracker.isLatest(ticket) ? {
        isSending: false,
        error: cancelled ? null : {
          message: cause?.message || '답변을 받지 못했습니다. 다시 시도해 주세요.',
          details: cause?.details ?? [],
        },
        notice: cancelled ? CANCELLED_NOTICE : null,
      } : previous)
    } finally {
      if (requestTracker.finish(ticket)) busyRef.current = false
    }
  }

  function handleSend(content, requestOptions = options) {
    const text = content.trim()
    if (busyRef.current || !text || text.length > 1000 || !isChatOptions(requestOptions)) return false

    const messageId = crypto.randomUUID()
    const ticket = requestTracker.begin(messageId)
    if (!ticket) return false
    busyRef.current = true

    setRequestState({ isSending: true, error: null, notice: null })
    setSession((previous) => ({
      ...previous,
      options: requestOptions,
      originPlace: reconcileOrigin(previous.originPlace, requestOptions),
      messages: [
        ...previous.messages,
        { id: messageId, role: 'user', content: text, status: 'pending' },
      ],
    }))

    void receiveReply(ticket, text, requestOptions)
    return true
  }

  function handleLodgingSelect(night, candidate) {
    if (busyRef.current || hasChangedOptions) return false
    try {
      const next = chooseLodging(options, night, candidate)
      const selected = getLodgingStay(next, night)
      return handleSend(getLodgingSelectionMessage(selected), next)
    } catch (cause) {
      setRequestState({ isSending: false, error: { message: cause.message } })
      return false
    }
  }

  function handleLodgingRemove(night) {
    if (busyRef.current || hasChangedOptions) return false
    const next = removeLodging(options, night)
    return handleSend(`${night}일차 숙소 선택을 취소하고 미정으로 두겠습니다. `
      + '해당 숙소 도착 이동과 다음 날 숙소 출발 이동을 제외하고 기존 관광 일정과 조건은 유지해 주세요.', next)
  }

  function handleRetry() {
    if (busyRef.current || !retryMessage) return
    const ticket = requestTracker.begin(retryMessage.id)
    if (!ticket) return

    busyRef.current = true
    setRequestState({ isSending: true, error: null, notice: null })

    setSession((previous) => ({
      ...previous,
      messages: previous.messages.map((item) => (
        item.id === retryMessage.id ? { ...item, status: 'pending' } : item
      )),
    }))

    void receiveReply(ticket, retryMessage.content)
  }

  function handleCancel() {
    const ticket = requestTracker.cancel()
    if (!ticket) return
    busyRef.current = false
    setSession((previous) => markChatMessageCancelled(previous, ticket.messageId))
    setRequestState({ isSending: false, error: null, notice: CANCELLED_NOTICE })
  }

  function handleNewTrip() {
    if (busyRef.current) return

    requestTracker.reset()
    setSession(createEmptyChatSession())
    setRequestState({ isSending: false, error: null })
    setViewKey((previous) => previous + 1)
    setOptionsOpen(true)
    setSidebarOpen(false)
    setShowAllDays(false)
  }

  function startGuideTour() {
    scrollWorkspaceToTop()
    sidebarBeforeTourRef.current = sidebarOpen
    focusBeforeTourRef.current = document.activeElement
    setShowAllDays(false)
    setTourStep(0)
    setTourActive(true)
    setSidebarOpen(getGuideSidebarState(0))
  }

  function moveGuideTour(nextStep) {
    setTourStep(nextStep)
    setSidebarOpen(getGuideSidebarState(nextStep))
  }

  function finishGuideTour() {
    setTourActive(false)
    setSidebarOpen(sidebarBeforeTourRef.current)
    markGuideTourSeen()

    const returnElement = focusBeforeTourRef.current
    if (returnElement?.isConnected && returnElement !== document.body) {
      window.setTimeout(() => returnElement.focus({ preventScroll: true }), 0)
    }
  }

  function handleGuideNext() {
    if (tourStep >= GUIDE_TOUR_STEPS.length - 1) {
      finishGuideTour()
      return
    }
    moveGuideTour(tourStep + 1)
  }

  function handleGuidePrevious() {
    if (tourStep <= 0) return
    moveGuideTour(tourStep - 1)
  }

  function handleDaySelect(dayNumber) {
    setShowAllDays(false)
    setSession((previous) => ({
      ...previous,
      selectedDay: dayNumber,
    }))
  }

  const visibleDay = visiblePlan?.days[0] ?? null
  const visitSummaries = getVisitSummaries(visibleDay)

  // 전체 일정 보기는 날짜가 2일 이상일 때만 씁니다. 세션에는 저장하지 않습니다.
  let isAllDaysView = false
  if (showAllDays && days.length > 1) {
    isAllDaysView = true
  }

  let sidebarTitle = '아직 만든 일정이 없습니다'
  if (planResult && isAllDaysView) {
    sidebarTitle = '전체 일정'
  } else if (planResult && visibleDay) {
    sidebarTitle = `${visibleDay.day}일차 일정`
  } else if (planResult) {
    sidebarTitle = '추천 여행 일정'
  }

  let planBannerLabel = ''
  if (planResult) {
    planBannerLabel = `${formatTripDuration(planOptions.trip_days)} · 날짜별 지도와 일정표`
  }

  let sidebarToggleLabel = '지도·일정 보기'
  if (sidebarOpen) {
    sidebarToggleLabel = '지도·일정 닫기'
  }

  const mainContent = (
    <>
      <TravelOptions
        options={options}
        originPlace={originPlace}
        onChange={handleOptionsChange}
        onOriginConfirm={handleOriginConfirm}
        disabled={isSending}
        isOpen={optionsOpen}
        onToggleOpen={() => setOptionsOpen((previous) => !previous)}
      />

      <ConversationPanel
        messages={messages}
        onSend={handleSend}
        tripExamples={tripExamples}
        isSending={isSending}
        error={error}
        notice={notice}
        onCancel={handleCancel}
        onRetry={retryMessage ? handleRetry : undefined}
        onOpenPlan={planResult ? () => setSidebarOpen(true) : undefined}
        planLabel={planBannerLabel}
        mascotImages={CONVERSATION_MASCOTS}
        headerAction={(
          <button
            type="button"
            className="secondary-button"
            disabled={isSending}
            onClick={handleNewTrip}
          >
            <RestartIcon />
            새 여행 시작
          </button>
        )}
      />
    </>
  )

  const sidebarContent = (
    <section className="workspace-results" aria-labelledby={SIDEBAR_TITLE_ID}>
      <div className="workspace-card-heading">
        <div className="workspace-heading-text">
          <p className="workspace-eyebrow">
            <MapIcon size={16} />
            여행 지도와 일정
          </p>
          <h2 id={SIDEBAR_TITLE_ID} tabIndex={-1}>{sidebarTitle}</h2>
          {planResult && (
            <p className="workspace-plan-summary">
              {planOptions.start_date || '출발일 확인 필요'}
              {' · '}
              {formatTripDuration(planOptions.trip_days)}
            </p>
          )}
        </div>

        <div className="workspace-results-actions">
          {planResult && (
            <div className="itinerary-export-actions" data-tour="export">
              <ItineraryPdfDownload
                planResult={planResult}
                disabled={isSending}
              />
              <ItineraryEmailDialog
                planResult={planResult}
                disabled={isSending}
              />
            </div>
          )}

          {!planResult && tourActive && (
            <div className="itinerary-export-actions is-guide-preview" data-tour="export" aria-hidden="true">
              <button type="button" className="secondary-button" disabled tabIndex={-1}>
                전체 일정 PDF 다운로드
              </button>
              <button type="button" className="secondary-button" disabled tabIndex={-1}>
                이메일로 받기
              </button>
            </div>
          )}

          <button
            type="button"
            className="secondary-button workspace-sidebar-close"
            onClick={() => setSidebarOpen(false)}
          >
            <CloseIcon />
            접기
          </button>
        </div>
      </div>

      {planResult ? (
        <>
          {hasChangedOptions && (
            <p className="workspace-result-notice" role="status">
              <InfoIcon />
              <span>
                변경한 옵션은 다음 메시지에 함께 반영합니다.
                아래에는 이전에 만든 지도와 일정을 표시합니다.
              </span>
            </p>
          )}

          {days.length > 1 && (
            <div
              className="workspace-day-buttons"
              role="group"
              aria-label="확인할 여행 날짜"
            >
              <button
                type="button"
                aria-pressed={isAllDaysView}
                onClick={() => setShowAllDays(true)}
              >
                전체 일정
              </button>
              {days.map((day) => (
                <button
                  key={day.day}
                  type="button"
                  aria-pressed={!isAllDaysView && selectedDay === day.day}
                  onClick={() => handleDaySelect(day.day)}
                >
                  {day.day}일차
                </button>
              ))}
            </div>
          )}

          {isAllDaysView && (
            <>
              <p className="workspace-all-days-notice">
                <InfoIcon />
                <span>
                  모든 날짜의 일정을 이어서 표시합니다.
                  일차를 고르면 지도에서 장소와 경로를 볼 수 있습니다.
                  숙소 선택도 일차별 보기에서 할 수 있습니다.
                </span>
              </p>

              <div key="all-days" className="workspace-schedule" data-tour="plan-area">
                {planResult.warnings.length > 0 && (
                  <div className="plan-notice">
                    <h3>
                      <InfoIcon />
                      확인할 사항
                    </h3>
                    <ul>
                      {planResult.warnings.map((warning, index) => (
                        <li key={index}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <TravelPlan
                  plan={planResult.plan}
                  autoFocus={false}
                  transitRoutes={planResult.transit_routes}
                />
              </div>
            </>
          )}

          {!isAllDaysView && visitSummaries.length > 0 && (
            <ol
              className="workspace-visit-summary"
              aria-label={`${visibleDay.day}일차 방문지 요약`}
            >
              {visitSummaries.map((summary) => (
                <li key={summary.number}>
                  <span className="workspace-visit-number">{summary.number}</span>
                  <span className="workspace-visit-text">
                    <span>{summary.startTime} 방문</span>
                    <strong>{summary.title}</strong>
                  </span>
                </li>
              ))}
            </ol>
          )}

          {!isAllDaysView && (
            <div data-tour="map">
              <ItineraryMap
                day={visibleDay}
                options={planOptions}
                mapRoutes={planResult.map_routes}
              />
            </div>
          )}

          {!isAllDaysView && (
            <div key={visibleDay?.day ?? 0} className="workspace-schedule" data-tour="plan-area">
              {planResult.warnings.length > 0 && (
                <div className="plan-notice">
                  <h3>
                    <InfoIcon />
                    확인할 사항
                  </h3>
                  <ul>
                    {planResult.warnings.map((warning, index) => (
                      <li key={index}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}

              <LodgingPanel
                key={JSON.stringify([selectedDay, getLodgingSearchCenter(visibleDay)])}
                day={visibleDay}
                options={planOptions}
                disabled={Boolean(isSending || hasChangedOptions)}
                onSelect={handleLodgingSelect}
                onRemove={handleLodgingRemove}
              />

              <TravelPlan
                plan={visiblePlan}
                autoFocus={false}
                transitRoutes={planResult.transit_routes}
              />
            </div>
          )}
        </>
      ) : (
        <>
          <div className="workspace-result-empty" role="status" data-tour="plan-area">
            <img className="workspace-result-empty-mascot" src={mascotMap} alt="" />
            <div>
              <p className="workspace-result-empty-title">
                대화로 일정을 먼저 만들어 주세요.
              </p>
              <p>
                가고 싶은 곳과 필요한 조건을 대화로 알려주시면,
                일정이 만들어진 뒤 이곳에 날짜별 지도와 일정표를 표시합니다.
              </p>
            </div>
          </div>

          <div data-tour="map">
            <ItineraryMap options={options} />
          </div>
        </>
      )}
    </section>
  )

  return (
    <div className="travel-workspace">
      <header className="workspace-header">
        <div className="workspace-header-inner">
          <h1>
            <img className="brand-logo" src={logoImage} alt="같이 가는 제주" />
          </h1>

          <div className="workspace-header-actions">
            <button
              type="button"
              className="secondary-button workspace-guide-button"
              aria-label="가이드 투어"
              disabled={tourActive}
              onClick={startGuideTour}
            >
              <GuideIcon />
              <span className="workspace-guide-label">가이드 투어</span>
            </button>

            <button
              type="button"
              className="secondary-button workspace-sidebar-toggle"
              aria-expanded={sidebarOpen}
              aria-controls={SIDEBAR_ID}
              onClick={() => setSidebarOpen((previous) => !previous)}
            >
              <MapIcon />
              {sidebarToggleLabel}
            </button>
          </div>
        </div>
      </header>

      <ResizableWorkspace
        key={viewKey}
        main={mainContent}
        sidebar={sidebarContent}
        sidebarId={SIDEBAR_ID}
        sidebarTitleId={SIDEBAR_TITLE_ID}
        sidebarOpen={sidebarOpen}
        onCloseSidebar={() => setSidebarOpen(false)}
      />

      <GuideTour
        active={tourActive}
        step={tourStep}
        steps={GUIDE_TOUR_STEPS}
        onNext={handleGuideNext}
        onPrevious={handleGuidePrevious}
        onSkip={finishGuideTour}
      />
    </div>
  )
}

export default TravelWorkspace
