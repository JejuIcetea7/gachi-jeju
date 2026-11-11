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