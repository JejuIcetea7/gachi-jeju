export const PANEL_MIN_WIDTHS = Object.freeze([220, 280, 320])
export const DEFAULT_PANEL_WEIGHTS = Object.freeze([0.07, 0.24, 0.69])
export const PANEL_GUTTER_WIDTH = 16

const MIN_TOTAL_WIDTH = PANEL_MIN_WIDTHS.reduce(
  (total, width) => total + width,
  0,
)

export function isPanelWeights(value) {
  return Array.isArray(value)
    && value.length === PANEL_MIN_WIDTHS.length
    && value.every((weight) => (
      Number.isFinite(weight) && weight >= 0 && weight <= 1
    ))
    && Math.abs(value.reduce((total, weight) => total + weight, 0) - 1) < 0.000001
}

export function getPanelWidths(totalWidth, weights = DEFAULT_PANEL_WEIGHTS) {
  if (!Number.isFinite(totalWidth) || totalWidth < MIN_TOTAL_WIDTH) {
    return null
  }

  const selected = isPanelWeights(weights) ? weights : DEFAULT_PANEL_WEIGHTS
  const weightTotal = selected.reduce((total, weight) => total + weight, 0)
  const remaining = totalWidth - MIN_TOTAL_WIDTH

  return PANEL_MIN_WIDTHS.map((minimum, index) => (
    minimum + remaining * selected[index] / weightTotal
  ))
}

export function resizePanelWeights(totalWidth, weights, divider, delta) {
  const widths = getPanelWidths(totalWidth, weights)

  if (
    !widths
    || ![0, 1].includes(divider)
    || !Number.isFinite(delta)
    || totalWidth <= MIN_TOTAL_WIDTH
  ) {
    return null
  }

  const left = divider
  const right = divider + 1
  const change = Math.max(
    PANEL_MIN_WIDTHS[left] - widths[left],
    Math.min(delta, widths[right] - PANEL_MIN_WIDTHS[right]),
  )

  widths[left] += change
  widths[right] -= change

  const remaining = totalWidth - MIN_TOTAL_WIDTH

  return widths.map((width, index) => (
    Math.max(0, width - PANEL_MIN_WIDTHS[index]) / remaining
  ))
}