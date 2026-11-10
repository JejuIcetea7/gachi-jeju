import { useEffect, useRef, useState } from 'react'
import TripSummary from './TripSummary.jsx'
import './App.css'
import { loadTravelSession, saveTravelSession } from './services/travelSession.js'
import OriginPicker from './OriginPicker.jsx'
import { buildPlanRequest } from './services/planRequest.js'
import { requestTravelPlan } from './services/planApi.js'

const DEFAULT_ORIGIN = '제주빅데이터센터'

const TRAVEL_THEMES = [
  {
    id: 'nature',
    name: '자연·산책',
    description: '공원과 숲에서 여유롭게 쉬어갑니다.',
  },
  {
    id: 'coast',
    name: '바다·풍경',
    description: '제주의 바다와 해안 풍경을 만나봅니다.',
  },
  {
    id: 'culture',
    name: '문화·관람',
    description: '박물관과 전시 공간을 둘러봅니다.',
  },
  {
    id: 'market',
    name: '시장 둘러보기',
    description: '제주의 시장과 동네를 만나봅니다.',
  },
  {
    id: 'mixed',
    name: '골고루 추천',
    description: '여러 종류의 장소를 함께 둘러봅니다.',
  },
]

const TRANSPORT_MODES = [
  {
    id: 'walking',
    name: '도보',
    description: '걸어서 이동합니다.',
  },
  {
    id: 'driving',
    name: '자동차',
    description: '자가용이나 택시로 이동합니다.',
  },
  {
    id: 'transit',
    name: '대중교통',
    description: '버스와 도보를 이용합니다.',
  },
]

const ACCESS_REQUIREMENTS = [
  {
    id: 'step_free',
    name: '계단 피하기',
    description: '계단을 이용하지 않는 동선이 필요합니다.',
  },
  {
    id: 'accessible_toilet',
    name: '장애인 화장실',
    description: '장애인 화장실이 있는 장소가 필요합니다.',
  },
  {
    id: 'accessible_parking',
    name: '장애인 주차구역',
    description: '장애인 주차구역이 필요합니다.',
  },
]

function App() {
  const [savedState] = useState(loadTravelSession)
  const [currentStep, setCurrentStep] = useState(
    savedState?.originPlace ? savedState.currentStep : 'theme',
  )
  const [originName, setOriginName] = useState(
    savedState?.originName ?? DEFAULT_ORIGIN,
  )
  const [originPlace, setOriginPlace] = useState(
    savedState?.originPlace ?? null,
  )
  const [selectedThemeId, setSelectedThemeId] = useState(
    savedState?.selectedThemeId ?? '',
  )
  const [selectedTransportMode, setSelectedTransportMode] = useState(
    savedState?.selectedTransportMode ?? '',
  )
  const [travelDate, setTravelDate] = useState(
    savedState?.travelDate ?? '',
  )
  const [startTime, setStartTime] = useState(
    savedState?.startTime ?? '',
  )
  const [endTime, setEndTime] = useState(
    savedState?.endTime ?? '',
  )
  const [selectedRequirements, setSelectedRequirements] = useState(
    savedState?.selectedRequirements ?? [],
  )
  const [restMinutes, setRestMinutes] = useState(
    savedState?.restMinutes ?? 0,
  )
  const [returnToOrigin, setReturnToOrigin] = useState(
    savedState?.returnToOrigin ?? false,
  )
  const [summaryData, setSummaryData] = useState(
    savedState?.summaryData ?? null,
  )
  const [hasAttemptedReview, setHasAttemptedReview] = useState(false)
  const [chatMessages, setChatMessages] = useState(
    savedState?.chatMessages ?? [],
  )
  const [isGenerating, setIsGenerating] = useState(false)
  const [planResult, setPlanResult] = useState(
    savedState?.planResult ?? null,
  )
  const [planError, setPlanError] = useState(null)
  const generatingRef = useRef(false)

  useEffect(() => {
    saveTravelSession({
      currentStep,
      originName,
      originPlace,
      selectedThemeId,
      selectedTransportMode,
      travelDate,
      startTime,
      endTime,
      selectedRequirements,
      restMinutes,
      returnToOrigin,
      summaryData,
      chatMessages,
      planResult,
    })
  }, [
    currentStep,
    originName,
    originPlace,
    selectedThemeId,
    selectedTransportMode,
    travelDate,
    startTime,
    endTime,
    selectedRequirements,
    restMinutes,
    returnToOrigin,
    summaryData,
    chatMessages,
    planResult,
  ])

  const selectedTheme = TRAVEL_THEMES.find(
    (theme) => theme.id === selectedThemeId,
  )

  function handleThemeSelect(themeId) {
    if (!originPlace) {
      return
    }

    setSelectedThemeId(themeId)
    setCurrentStep('conditions')
  }

  function handleRequirementToggle(requirementId) {
    setSelectedRequirements((previousRequirements) => {
      if (previousRequirements.includes(requirementId)) {
        return previousRequirements.filter(
          (selectedId) => selectedId !== requirementId,
        )
      }

      return [...previousRequirements, requirementId]
    })
  }

  function handleOriginConfirm(place) {
    setOriginPlace(place)
    setOriginName(place.name)

    setSummaryData((previousSummary) => {
      if (!previousSummary) {
        return previousSummary
      }

      return {
        ...previousSummary,
        originName: place.name,
        originPlace: place,
      }
    })
  }

  function clearPlanFeedback() {
    setPlanResult(null)
    setPlanError(null)
  }

  function handleChatSend(content) {
    const requestText = content.trim()

    if (generatingRef.current || !requestText || !summaryData) {
      return false
    }

    const nextMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: requestText,
      conditions: summaryData,
    }

    setChatMessages((previousMessages) => [
      ...previousMessages,
      nextMessage,
    ])
    clearPlanFeedback()
    return true
  }

  function handleChatRemove(messageId) {
    if (generatingRef.current) {
      return
    }
    setChatMessages((previousMessages) => (
      previousMessages.filter((message) => message.id !== messageId)
    ))
    clearPlanFeedback()
  }

  async function handleGeneratePlan() {
    if (generatingRef.current) {
      return
    }

    generatingRef.current = true
    setIsGenerating(true)
    clearPlanFeedback()

    try {
      const requestBody = buildPlanRequest({
        summary: summaryData,
        themeId: selectedThemeId,
        transportMode: selectedTransportMode,
        requirementIds: selectedRequirements,
        messages: chatMessages,
      })
      const result = await requestTravelPlan(requestBody)
      setPlanResult(result)
    } catch (error) {
      setPlanError({
        message: typeof error?.message === 'string'
          ? error.message
          : '일정을 만들지 못했습니다. 다시 시도해 주세요.',
        code: error?.code ?? 'request_error',
        status: error?.status ?? null,
        details: Array.isArray(error?.details) ? error.details : [],
      })
    } finally {
      generatingRef.current = false
      setIsGenerating(false)
    }
  }

  const hasInvalidTime = Boolean(
    startTime && endTime && endTime <= startTime,
  )

  const selectedTransport = TRANSPORT_MODES.find(
    (mode) => mode.id === selectedTransportMode,
  )

  function getConditionsError() {
    if (!originName.trim() || !originPlace) {
      return '첫 화면에서 출발지를 선택해 주세요.'
    }

    if (!selectedTheme || !selectedTransport) {
      return '여행 종류와 이동수단을 선택해 주세요.'
    }

    if (!travelDate || !startTime || !endTime) {
      return '여행 날짜와 시작·종료 시간을 모두 입력해 주세요.'
    }

    if (hasInvalidTime) {
      return '종료 시간은 시작 시간보다 늦게 선택해 주세요.'
    }

    const [startHours, startMinutes] = startTime.split(':').map(Number)
    const [endHours, endMinutes] = endTime.split(':').map(Number)
    const availableMinutes =
      (endHours * 60 + endMinutes) - (startHours * 60 + startMinutes)

    if (restMinutes > 0 && restMinutes >= availableMinutes) {
      return '휴식 시간은 전체 여행 시간보다 짧게 선택해 주세요.'
    }

    return ''
  }

  const conditionsError = getConditionsError()

  function handleConditionsReview() {
    setHasAttemptedReview(true)

    if (conditionsError) {
      return
    }

    const requirementNames = []

    for (const requirement of ACCESS_REQUIREMENTS) {
      if (selectedRequirements.includes(requirement.id)) {
        requirementNames.push(requirement.name)
      }
    }

    setSummaryData({
      originName: originName.trim(),
      originPlace,
      themeName: selectedTheme.name,
      transportName: selectedTransport.name,
      travelDate,
      startTime,
      endTime,
      requirementNames,
      restMinutes,
      returnToOrigin,
    })

    setCurrentStep('summary')
  }

  return (
    <div className="travel-app">
      <header className="app-header">
        <span className="brand-name">같이 가는 제주</span>
        <span className="brand-tagline">
          당신의 속도로 만나는 제주
        </span>
      </header>

      <main className="page-content">
        {currentStep === 'theme' ? (
          <section aria-labelledby="welcome-title">
            <div className="page-heading">
              <p className="eyebrow">함께 계획하는 제주 나들이</p>
              <h1 id="welcome-title">
                어디에서
                <br />
                제주 여행을 시작할까요?
              </h1>
              <p>출발지를 선택한 뒤 원하는 여행을 골라 주세요.</p>
            </div>

            <OriginPicker
              initialKeyword={originName}
              confirmedPlace={originPlace}
              onConfirm={handleOriginConfirm}
            />

            {!originPlace && (
              <p className="map-status" role="status">
                출발지를 검색한 뒤 출발지 사용 버튼을 눌러 주세요.
              </p>
            )}

            <fieldset className="choice-fieldset">
              <legend>어떤 제주를 만나고 싶으신가요?</legend>

              <div className="theme-grid">
                {TRAVEL_THEMES.map((theme) => (
                  <button
                    key={theme.id}
                    type="button"
                    className={
                      selectedThemeId === theme.id
                        ? 'theme-card is-selected'
                        : 'theme-card'
                    }
                    disabled={!originPlace}
                    onClick={() => handleThemeSelect(theme.id)}
                  >
                    <span>
                      <strong>{theme.name}</strong>
                      <span className="theme-description">
                        {theme.description}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </fieldset>
          </section>
        ) : currentStep === 'conditions' ? (
          <section aria-labelledby="conditions-title">
            <button
              className="back-button"
              type="button"
              onClick={() => {
                setCurrentStep('theme')
              }}
            >
              ← 여행 종류 다시 선택
            </button>

            <div className="page-heading">
              <p className="eyebrow">여행 조건 설정</p>
              <h1 id="conditions-title">
                어떻게 이동하시나요?
              </h1>
              <p>
                {originName.trim()}에서 출발하는{' '}
                {selectedTheme?.name} 여행입니다.
              </p>
            </div>

            <fieldset className="choice-fieldset">
              <legend>주로 이용할 이동수단을 선택하세요</legend>

              <div className="transport-options">
                {TRANSPORT_MODES.map((mode) => (
                  <button
                    key={mode.id}
                    type="button"
                    className={
                      selectedTransportMode === mode.id
                        ? 'theme-card is-selected'
                        : 'theme-card'
                    }
                    aria-pressed={selectedTransportMode === mode.id}
                    onClick={() => {
                      setSelectedTransportMode(mode.id)
                    }}
                  >
                    <span>
                      <strong>{mode.name}</strong>
                      <span className="theme-description">
                        {mode.description}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </fieldset>

            {selectedTransportMode && (
              <section
                className="trip-time-section"
                aria-labelledby="trip-time-title"
              >
                <h2 id="trip-time-title">언제 떠나시나요?</h2>

                <div className="time-fields">
                  <label htmlFor="travel-date">
                    여행 날짜
                    <input
                      id="travel-date"
                      type="date"
                      value={travelDate}
                      onChange={(event) => {
                        setTravelDate(event.target.value)
                      }}
                      required
                    />
                  </label>

                  <label htmlFor="start-time">
                    시작 시간
                    <input
                      id="start-time"
                      type="time"
                      value={startTime}
                      onChange={(event) => {
                        setStartTime(event.target.value)
                      }}
                      required
                    />
                  </label>

                  <label htmlFor="end-time">
                    종료 시간
                    <input
                      id="end-time"
                      type="time"
                      value={endTime}
                      onChange={(event) => {
                        setEndTime(event.target.value)
                      }}
                      aria-invalid={hasInvalidTime}
                      aria-describedby={
                        hasInvalidTime
                          ? 'travel-time-error'
                          : undefined
                      }
                      required
                    />
                  </label>
                </div>

                {hasInvalidTime && (
                  <p
                    id="travel-time-error"
                    className="form-error"
                    role="alert"
                  >
                    종료 시간은 시작 시간보다 늦게 선택해 주세요.
                  </p>
                )}

                <fieldset className="choice-fieldset">
                  <legend>
                    꼭 필요한 이용 조건이 있나요? (여러 개 선택)
                  </legend>

                  <div className="theme-grid">
                    {ACCESS_REQUIREMENTS.map((requirement) => (
                      <button
                        key={requirement.id}
                        type="button"
                        className={
                          selectedRequirements.includes(requirement.id)
                            ? 'theme-card is-selected'
                            : 'theme-card'
                        }
                        aria-pressed={
                          selectedRequirements.includes(requirement.id)
                        }
                        onClick={() => {
                          handleRequirementToggle(requirement.id)
                        }}
                      >
                        <span>
                          <strong>{requirement.name}</strong>
                          <span className="theme-description">
                            {requirement.description}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                </fieldset>
                <div className="rest-field">
                  <label htmlFor="rest-minutes">
                    중간 휴식 시간 (1회)
                  </label>

                  <select
                    id="rest-minutes"
                    value={restMinutes}
                    onChange={(event) => {
                      setRestMinutes(Number(event.target.value))
                    }}
                  >
                    <option value={0}>휴식 없이 계획</option>
                    <option value={10}>10분</option>
                    <option value={20}>20분</option>
                    <option value={30}>30분</option>
                  </select>
                </div>

                <fieldset className="choice-fieldset">
                  <legend>여행을 어디서 마칠까요?</legend>

                  <div className="return-options">
                    <button
                      type="button"
                      className={
                        !returnToOrigin
                          ? 'theme-card is-selected'
                          : 'theme-card'
                      }
                      aria-pressed={!returnToOrigin}
                      onClick={() => {
                        setReturnToOrigin(false)
                      }}
                    >
                      <span>
                        <strong>마지막 여행지에서 마치기</strong>
                        <span className="theme-description">
                          마지막으로 방문한 장소에서 여행을 마칩니다.
                        </span>
                      </span>
                    </button>

                    <button
                      type="button"
                      className={
                        returnToOrigin
                          ? 'theme-card is-selected'
                          : 'theme-card'
                      }
                      aria-pressed={returnToOrigin}
                      onClick={() => {
                        setReturnToOrigin(true)
                      }}
                    >
                      <span>
                        <strong>출발지로 돌아오기</strong>
                        <span className="theme-description">
                          출발지까지 돌아오는 시간을 일정에 포함합니다.
                        </span>
                      </span>
                    </button>
                  </div>
                </fieldset>

                {hasAttemptedReview && conditionsError && (
                  <p
                    id="conditions-error"
                    className="form-error"
                    role="alert"
                  >
                    {conditionsError}
                  </p>
                )}

                <button
                  type="button"
                  className="primary-button"
                  onClick={handleConditionsReview}
                >
                  여행 조건 확인
                </button>
              </section>
            )}
          </section>
        ) : (
          <TripSummary
            summary={summaryData}
            onEditOrigin={() => {
              if (generatingRef.current) {
                return
              }
              clearPlanFeedback()
              setHasAttemptedReview(false)
              setCurrentStep('theme')
            }}
            chatMessages={chatMessages}
            onChatSend={handleChatSend}
            onChatRemove={handleChatRemove}
            isGenerating={isGenerating}
            planResult={planResult}
            planError={planError}
            onGenerate={handleGeneratePlan}
            onEdit={() => {
              if (generatingRef.current) {
                return
              }
              clearPlanFeedback()
              setHasAttemptedReview(false)
              setCurrentStep('conditions')
            }}
          />
        )}
      </main>
    </div>
  )
}

export default App