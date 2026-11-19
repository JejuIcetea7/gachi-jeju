export const PANEL_MIN_WIDTHS = Object.freeze([220, 280, 320])
export const COLLAPSED_PANEL_MIN_WIDTHS = Object.freeze([0, 280, 320])
export const DEFAULT_PANEL_WEIGHTS = Object.freeze([0.07, 0.24, 0.69])
export const DEFAULT_COLLAPSED_PANEL_WEIGHTS = Object.freeze([0, 0.15, 0.85])
export const PANEL_GUTTER_WIDTH = 16

export function isPanelWeights(value) {
  return Array.isArray(value)
    && value.length === PANEL_MIN_WIDTHS.length
    && value.every((weight) => (
      Number.isFinite(weight) && weight >= 0 && weight <= 1
    ))
    && Math.abs(value.reduce((total, weight) => total + weight, 0) - 1) < 0.000001
}

function getPanelSettings(weights, optionsOpen) {
  const minimums = optionsOpen ? PANEL_MIN_WIDTHS : COLLAPSED_PANEL_MIN_WIDTHS
  const fallback = optionsOpen
    ? DEFAULT_PANEL_WEIGHTS
    : DEFAULT_COLLAPSED_PANEL_WEIGHTS
  const selected = isPanelWeights(weights) && (optionsOpen || weights[0] === 0)
    ? weights
    : fallback

  return {
    minimums,
    minimumTotal: minimums.reduce((total, width) => total + width, 0),
    selected,
  }
}

export function getPanelWidths(
  totalWidth,
  weights = DEFAULT_PANEL_WEIGHTS,
  optionsOpen = true,
) {
  const { minimums, minimumTotal, selected } = getPanelSettings(weights, optionsOpen)
  if (!Number.isFinite(totalWidth) || totalWidth < minimumTotal) return null

  const weightTotal = selected.reduce((total, weight) => total + weight, 0)
  const remaining = totalWidth - minimumTotal

  return minimums.map((minimum, index) => (
    minimum + remaining * selected[index] / weightTotal
  ))
}

export function resizePanelWeights(
  totalWidth,
  weights,
  divider,
  delta,
  optionsOpen = true,
) {
  const { minimums, minimumTotal } = getPanelSettings(weights, optionsOpen)
  const widths = getPanelWidths(totalWidth, weights, optionsOpen)

  if (
    !widths
    || ![0, 1].includes(divider)
    || (!optionsOpen && divider === 0)
    || !Number.isFinite(delta)
    || totalWidth <= minimumTotal
  ) {
    return null
  }

  const left = divider
  const right = divider + 1
  const change = Math.max(
    minimums[left] - widths[left],
    Math.min(delta, widths[right] - minimums[right]),
  )

  widths[left] += change
  widths[right] -= change

  const remaining = totalWidth - minimumTotal

  return widths.map((width, index) => (
    Math.max(0, width - minimums[index]) / remaining
  ))
}
