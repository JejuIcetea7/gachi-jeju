import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_PANEL_WEIGHTS,
  PANEL_MIN_WIDTHS,
  getPanelWidths,
  isPanelWeights,
  resizePanelWeights,
} from '../../frontend/src/services/workspacePanels.js'

const closeTo = (actual, expected) => {
  assert.ok(Math.abs(actual - expected) < 0.000001)
}

test('세 영역의 너비 합은 사용 가능한 너비와 같습니다', () => {
  for (const total of [820, 1000, 1600]) {
    const widths = getPanelWidths(total)

    closeTo(widths.reduce((sum, value) => sum + value, 0), total)
    widths.forEach((width, index) => {
      assert.ok(width >= PANEL_MIN_WIDTHS[index])
    })
  }
})

test('첫 경계선을 옮기면 양옆 영역만 변경됩니다', () => {
  const original = getPanelWidths(1600)
  const weights = resizePanelWeights(1600, DEFAULT_PANEL_WEIGHTS, 0, 100)
  const resized = getPanelWidths(1600, weights)

  closeTo(resized[0], original[0] + 100)
  closeTo(resized[1], original[1] - 100)
  closeTo(resized[2], original[2])
})

test('두 번째 경계선도 최소 너비를 지킵니다', () => {
  for (const delta of [-10000, 10000]) {
    const weights = resizePanelWeights(1600, DEFAULT_PANEL_WEIGHTS, 1, delta)
    const widths = getPanelWidths(1600, weights)

    assert.ok(isPanelWeights(weights))
    closeTo(widths.reduce((sum, value) => sum + value, 0), 1600)

    widths.forEach((width, index) => {
      assert.ok(width >= PANEL_MIN_WIDTHS[index] - 0.000001)
    })
  }
})

test('화면이 좁아져도 조절한 비율과 최소 너비를 반영합니다', () => {
  const weights = resizePanelWeights(1600, DEFAULT_PANEL_WEIGHTS, 0, 100)
  const widths = getPanelWidths(1000, weights)

  closeTo(widths.reduce((sum, value) => sum + value, 0), 1000)
  widths.forEach((width, index) => {
    assert.ok(width >= PANEL_MIN_WIDTHS[index])
  })
})

test('저장된 설정이 잘못되면 기본 비율을 사용합니다', () => {
  for (const value of [null, [], [1, 1, 1], [-1, 1, 1], [NaN, 0, 1]]) {
    assert.equal(isPanelWeights(value), false)
    assert.deepEqual(getPanelWidths(1600, value), getPanelWidths(1600))
  }
})

test('조절할 공간이 없거나 입력이 잘못되면 변경하지 않습니다', () => {
  assert.equal(getPanelWidths(800), null)
  assert.equal(resizePanelWeights(820, DEFAULT_PANEL_WEIGHTS, 0, 10), null)
  assert.equal(resizePanelWeights(1600, DEFAULT_PANEL_WEIGHTS, 2, 10), null)
  assert.equal(resizePanelWeights(1600, DEFAULT_PANEL_WEIGHTS, 0, NaN), null)
})

test('옵션을 접어도 화면 너비에 맞게 두 영역의 합과 최소 너비를 지킵니다', () => {
  for (const viewport of [901, 1024, 1280, 1366, 1440, 1920]) {
    const available = viewport - 32 - 16
    const widths = getPanelWidths(available, undefined, false)

    assert.equal(widths[0], 0)
    closeTo(widths.reduce((sum, width) => sum + width, 0), available)
    assert.ok(widths[1] >= PANEL_MIN_WIDTHS[1])
    assert.ok(widths[2] >= PANEL_MIN_WIDTHS[2])
  }
  assert.equal(getPanelWidths(599, undefined, false), null)
  assert.deepEqual(getPanelWidths(600, undefined, false), [0, 280, 320])
})

test('접은 배치의 경계선도 양쪽 최소 너비와 숨긴 영역을 지킵니다', () => {
  const total = 1200
  const initial = [0, 0.15, 0.85]
  const original = [...initial]
  for (const delta of [-10000, -100, 100, 10000]) {
    const weights = resizePanelWeights(total, initial, 1, delta, false)
    const widths = getPanelWidths(total, weights, false)

    assert.ok(isPanelWeights(weights))
    assert.equal(weights[0], 0)
    assert.equal(widths[0], 0)
    closeTo(widths[1] + widths[2], total)
    assert.ok(widths[1] >= 280 - 0.000001)
    assert.ok(widths[2] >= 320 - 0.000001)
  }
  assert.deepEqual(initial, original)
  assert.equal(resizePanelWeights(total, initial, 0, 20, false), null)
  assert.equal(resizePanelWeights(600, initial, 1, 20, false), null)
})

test('펼친 배치의 비율을 접은 배치에 잘못 적용하지 않습니다', () => {
  assert.deepEqual(
    getPanelWidths(1200, DEFAULT_PANEL_WEIGHTS, false),
    getPanelWidths(1200, undefined, false),
  )
  assert.deepEqual(
    getPanelWidths(1200, [0, 0.5, 0.5], false),
    [0, 580, 620],
  )
})

test('옵션을 펼치고 접는 배치의 저장 비율은 독립적으로 다시 사용할 수 있습니다', () => {
  const expanded = resizePanelWeights(1500, DEFAULT_PANEL_WEIGHTS, 0, 80)
  const compact = resizePanelWeights(1516, [0, 0.15, 0.85], 1, -100, false)
  const before = getPanelWidths(1500, expanded)

  getPanelWidths(1516, compact, false)
  assert.deepEqual(getPanelWidths(1500, expanded), before)
  closeTo(
    getPanelWidths(1516, compact, false)[1],
    getPanelWidths(1516, [0, 0.15, 0.85], false)[1] - 100,
  )
})
