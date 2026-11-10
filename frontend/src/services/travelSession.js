import { isPlanResponse } from './planApi.js'

const STORAGE_KEY = 'gachi-jeju.travel-session'
const STORAGE_VERSION = 1

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function hasStringFields(value, fields) {
  return isRecord(value) &&
    fields.every((field) => typeof value[field] === 'string')
}

function isStringArray(value) {
  return Array.isArray(value) &&
    value.every((item) => typeof item === 'string')
}

function isPlace(value) {
  return value === null || (
    hasStringFields(value, ['id', 'name', 'address']) &&
    Number.isFinite(value.latitude) && Math.abs(value.latitude) <= 90 &&
    Number.isFinite(value.longitude) && Math.abs(value.longitude) <= 180
  )
}

function isSummary(value) {
  if (value === null) {
    return true
  }

  return hasStringFields(value, [
    'originName', 'themeName', 'transportName',
    'travelDate', 'startTime', 'endTime',
  ]) &&
    isPlace(value.originPlace) &&
    isStringArray(value.requirementNames) &&
    Number.isFinite(value.restMinutes) && value.restMinutes >= 0 &&
    typeof value.returnToOrigin === 'boolean'
}

function isTravelState(value) {
  if (!hasStringFields(value, [
    'currentStep', 'originName', 'selectedThemeId',
    'selectedTransportMode', 'travelDate', 'startTime', 'endTime',
  ])) {
    return false
  }

  if (!Array.isArray(value.chatMessages)) {
    return false
  }

  for (const message of value.chatMessages) {
    if (
      !hasStringFields(message, ['id', 'role', 'content']) ||
      !isSummary(message.conditions) || message.conditions === null
    ) {
      return false
    }
  }

  return ['theme', 'conditions', 'summary'].includes(value.currentStep) &&
    isPlace(value.originPlace) &&
    isStringArray(value.selectedRequirements) &&
    Number.isFinite(value.restMinutes) && value.restMinutes >= 0 &&
    typeof value.returnToOrigin === 'boolean' &&
    isSummary(value.summaryData) &&
    (value.currentStep !== 'summary' || value.summaryData !== null)
}

export function loadTravelSession() {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return null
    }

    const saved = JSON.parse(raw)
    if (
      !isRecord(saved) ||
      saved.version !== STORAGE_VERSION ||
      !isTravelState(saved.state)
    ) {
      return null
    }

    return {
      ...saved.state,
      planResult: isPlanResponse(saved.state.planResult)
        ? saved.state.planResult
        : null,
    }
  } catch {
    return null
  }
}

export function saveTravelSession(state) {
  try {
    const saved = {
      version: STORAGE_VERSION,
      state,
    }

    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(saved))
    return true
  } catch {
    return false
  }
}