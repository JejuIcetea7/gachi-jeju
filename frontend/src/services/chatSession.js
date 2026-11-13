import { normalizeLodgingOptions } from './lodgingPlanning.js'
import { isChatOptions } from './chatApi.js'
import { isPlanResponse } from './planApi.js'
import { loadTravelSession } from './travelSession.js'
import {
  ACCESS_REQUIREMENTS,
  DEFAULT_ORIGIN,
  TRANSPORT_OPTIONS,
  TRAVEL_STYLES,
  createDefaultChatOptions,
} from './travelOptions.js'

const STORAGE_KEY = 'gachi-jeju.chat-session'
const STORAGE_VERSION = 1

export function createEmptyChatSession() {
  return {
    options: createDefaultChatOptions(),
    originPlace: null,
    messages: [],
    planResult: null,
    selectedDay: 1,
  }
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isOriginPlace(value) {
  return value === null || (
    isRecord(value)
    && ['id', 'name', 'address'].every(
      (key) => typeof value[key] === 'string',
    )
    && Number.isFinite(value.latitude)
    && Math.abs(value.latitude) <= 90
    && Number.isFinite(value.longitude)
    && Math.abs(value.longitude) <= 180
  )
}

function isMessage(value) {
  return isRecord(value)
    && typeof value.id === 'string'
    && value.id.length > 0
    && ['user', 'assistant'].includes(value.role)
    && typeof value.content === 'string'
    && value.content.trim().length > 0
    && value.content.length <= (value.role === 'user' ? 1000 : 4000)
    && ['sent', 'pending', 'failed'].includes(value.status)
}

function isSessionState(value) {
  return isRecord(value)
    && isChatOptions(value.options)
    && isOriginPlace(value.originPlace)
    && Array.isArray(value.messages)
    && value.messages.every(isMessage)
    && (value.planResult === null || (
      isPlanResponse(value.planResult)
      && isChatOptions(value.planResult.options)
    ))
    && Number.isSafeInteger(value.selectedDay)
    && value.selectedDay >= 1
}

function normalizeRestoredState(state) {
  const days = state.planResult?.plan.days ?? []

  return {
    ...state,
    options: normalizeLodgingOptions(state.options),
    planResult: state.planResult ? {
      ...state.planResult,
      options: normalizeLodgingOptions(state.planResult.options),
    } : null,
    messages: state.messages.map((message) => (
      message.status === 'pending'
        ? { ...message, status: 'failed' }
        : message
    )),
    selectedDay: days.some((day) => day.day === state.selectedDay)
      ? state.selectedDay
      : days[0]?.day ?? 1,
  }
}

function migratePreviousSession() {
  const previous = loadTravelSession()
  if (!previous) return createEmptyChatSession()

  const themes = {
    nature: ['자연', '산책로', '공원'],
    coast: ['해변'],
    culture: ['박물관·전시'],
    market: ['시장'],
    mixed: TRAVEL_STYLES,
  }

  const options = {
    ...createDefaultChatOptions(),
    departure: previous.originName.trim() || DEFAULT_ORIGIN,
    departure_latitude: previous.originPlace?.latitude ?? null,
    departure_longitude: previous.originPlace?.longitude ?? null,
    transport: TRANSPORT_OPTIONS.some(
      (item) => item.value === previous.selectedTransportMode,
    ) ? previous.selectedTransportMode : null,
    start_date: previous.travelDate || null,
    trip_days: previous.travelDate ? 1 : null,
    travel_styles: [...(themes[previous.selectedThemeId] ?? [])],
    requirements: previous.selectedRequirements.filter((value) => (
      ACCESS_REQUIREMENTS.some((item) => item.value === value)
    )),
    return_to_origin: previous.returnToOrigin,
  }

  const migrated = {
    options,
    originPlace: previous.originPlace,
    messages: previous.chatMessages.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      status: 'sent',
    })).filter(isMessage),
    planResult: previous.planResult
      ? { ...previous.planResult, options }
      : null,
    selectedDay: 1,
  }

  return isSessionState(migrated)
    ? normalizeRestoredState(migrated)
    : createEmptyChatSession()
}

export function loadChatSession() {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return migratePreviousSession()

    const saved = JSON.parse(raw)

    if (
      saved?.version !== STORAGE_VERSION
      || !isSessionState(saved.state)
    ) {
      return createEmptyChatSession()
    }

    return normalizeRestoredState(saved.state)
  } catch {
    return createEmptyChatSession()
  }
}

export function saveChatSession(state) {
  try {
    if (!isSessionState(state)) return false

    window.sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: STORAGE_VERSION, state }),
    )
    return true
  } catch {
    return false
  }
}