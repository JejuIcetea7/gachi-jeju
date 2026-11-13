import { Children, useEffect, useRef, useState } from 'react'
import {
  COLLAPSED_PANEL_MIN_WIDTHS,
  DEFAULT_COLLAPSED_PANEL_WEIGHTS,
  DEFAULT_PANEL_WEIGHTS,
  PANEL_GUTTER_WIDTH,
  PANEL_MIN_WIDTHS,
  getPanelWidths,
  isPanelWeights,
  resizePanelWeights,
} from './services/workspacePanels.js'

const STORAGE_KEY = 'gachi-jeju.workspace-panels.v4'
const COLLAPSED_STORAGE_KEY = 'gachi-jeju.workspace-panels.collapsed.v1'
const OPTIONS_RAIL_WIDTH = 56
const DIVIDERS = [
  { paneId: 'workspace-options-pane', titleId: 'workspace-options-title' },
  { paneId: 'workspace-chat-pane', titleId: 'conversation-title' },
]

function loadPanelWeights(key, fallback) {
  try {
    const saved = JSON.parse(window.localStorage.getItem(key))
    if (
      isPanelWeights(saved)
      && (key !== COLLAPSED_STORAGE_KEY || saved[0] === 0)
    ) return saved
  } catch {
    // 저장소를 사용할 수 없어도 기본 너비로 화면을 표시합니다.
  }
  return [...fallback]
}

function ResizableWorkspace({ children, optionsOpen = true, onToggleOptions }) {
  const containerRef = useRef(null)
  const dragRef = useRef(null)
  const [panelState, setPanelState] = useState(() => ({
    storageKey: STORAGE_KEY,
    weights: loadPanelWeights(STORAGE_KEY, DEFAULT_PANEL_WEIGHTS),
  }))
  const [collapsedWeights, setCollapsedWeights] = useState(() => (
    loadPanelWeights(COLLAPSED_STORAGE_KEY, DEFAULT_COLLAPSED_PANEL_WEIGHTS)
  ))
  const expandedWeights = panelState?.storageKey === STORAGE_KEY
    && isPanelWeights(panelState.weights)
    ? panelState.weights
    : DEFAULT_PANEL_WEIGHTS
  const weights = optionsOpen ? expandedWeights : collapsedWeights
  const minimums = optionsOpen ? PANEL_MIN_WIDTHS : COLLAPSED_PANEL_MIN_WIDTHS
  const [totalWidth, setTotalWidth] = useState(0)
  const [activeDivider, setActiveDivider] = useState(null)
  const panels = Children.toArray(children)
  const widths = getPanelWidths(totalWidth, weights, optionsOpen)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const observer = new ResizeObserver(() => {
      const desktop = window.matchMedia('(min-width: 901px)').matches
      const style = window.getComputedStyle(container)
      const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight)
      const gutters = PANEL_GUTTER_WIDTH * (optionsOpen ? 2 : 1)
        + (optionsOpen ? 0 : OPTIONS_RAIL_WIDTH)

      setTotalWidth(desktop
        ? Math.max(0, container.clientWidth - padding - gutters)
        : 0)

      dragRef.current = null
      setActiveDivider(null)
    })

    observer.observe(container)
    return () => observer.disconnect()
  }, [optionsOpen])

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(expandedWeights))
      window.localStorage.setItem(COLLAPSED_STORAGE_KEY, JSON.stringify(collapsedWeights))
    } catch {
      // 저장에 실패해도 현재 화면의 너비 조절은 유지합니다.
    }
  }, [expandedWeights, collapsedWeights])

  function setWeights(nextWeights) {
    if (optionsOpen) {
      setPanelState({
        storageKey: STORAGE_KEY,
        weights: nextWeights,
      })
    } else {
      setCollapsedWeights(nextWeights)
    }
  }

  function stopDragging(event) {
    if (dragRef.current?.pointerId !== event.pointerId) return

    dragRef.current = null
    setActiveDivider(null)

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  function startDragging(event) {
    const divider = Number(event.currentTarget.dataset.divider)
    if (!widths || dragRef.current || event.button !== 0 || !event.isPrimary) return

    event.preventDefault()
    event.currentTarget.focus()
    event.currentTarget.setPointerCapture(event.pointerId)

    dragRef.current = {
      pointerId: event.pointerId,
      divider,
      startX: event.clientX,
      totalWidth,
      weights: [...weights],
    }
    setActiveDivider(divider)
  }

  function moveDivider(event) {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return

    if (drag.totalWidth !== totalWidth) {
      stopDragging(event)
      return
    }

    const next = resizePanelWeights(
      drag.totalWidth,
      drag.weights,
      drag.divider,
      event.clientX - drag.startX,
      optionsOpen,
    )
    if (next) setWeights(next)
  }

  function handleKeyDown(event) {
    const divider = Number(event.currentTarget.dataset.divider)
    if (!widths || dragRef.current) return

    const step = event.shiftKey ? 80 : 24
    const delta = {
      ArrowLeft: -step,
      ArrowRight: step,
      Home: -totalWidth,
      End: totalWidth,
    }[event.key]

    if (delta === undefined) return
    event.preventDefault()

    const next = resizePanelWeights(totalWidth, weights, divider, delta, optionsOpen)
    if (next) setWeights(next)
  }

  function renderDivider(index) {
    const panelWidth = widths?.[index] ?? minimums[index]
    const pairWidth = widths
      ? widths[index] + widths[index + 1]
      : minimums[index] + minimums[index + 1]

    return (
      <div
        className="workspace-divider"
        role="separator"
        aria-orientation="vertical"
        aria-labelledby={DIVIDERS[index].titleId}
        aria-controls={DIVIDERS[index].paneId}
        aria-valuemin={minimums[index]}
        aria-valuemax={Math.round(pairWidth - minimums[index + 1])}
        aria-valuenow={Math.round(panelWidth)}
        aria-valuetext={`${Math.round(panelWidth)}픽셀`}
        aria-disabled={!widths}
        tabIndex={widths ? 0 : -1}
        data-active={activeDivider === index ? 'true' : undefined}
        data-divider={index}
        title="드래그하거나 좌우 방향키로 너비 조절 · 두 번 클릭하면 기본 너비"
        onPointerDown={startDragging}
        onPointerMove={moveDivider}
        onPointerUp={stopDragging}
        onPointerCancel={stopDragging}
        onLostPointerCapture={stopDragging}
        onKeyDown={handleKeyDown}
        onDoubleClick={() => setWeights([...(optionsOpen
          ? DEFAULT_PANEL_WEIGHTS
          : DEFAULT_COLLAPSED_PANEL_WEIGHTS)])}
      />
    )
  }

  return (
    <main
      ref={containerRef}
      className={`workspace-layout workspace-resizable${activeDivider === null ? '' : ' is-resizing'}`}
      data-options-open={optionsOpen ? 'true' : 'false'}
      style={{
        '--workspace-options-rail-width': `${OPTIONS_RAIL_WIDTH}px`,
        ...(widths ? {
          '--workspace-options-width': `${widths[0]}px`,
          '--workspace-chat-width': `${widths[1]}px`,
          '--workspace-results-width': `${widths[2]}px`,
        } : {}),
      }}
    >
      <div className="workspace-options-slot">
        <button
          type="button"
          className="workspace-options-toggle"
          aria-expanded={optionsOpen}
          aria-controls="workspace-options-pane"
          aria-label={optionsOpen ? '여행 옵션 접기' : '여행 옵션 펼치기'}
          title={optionsOpen ? '여행 옵션 접기' : '여행 옵션 펼치기'}
          onClick={onToggleOptions}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            focusable="false"
          >
            <path d="m9 6 6 6-6 6" />
          </svg>
        </button>
        <div className="workspace-options-content" hidden={!optionsOpen}>
          {panels[0]}
        </div>
      </div>
      {optionsOpen && renderDivider(0)}
      {panels[1]}
      {renderDivider(1)}
      {panels[2]}
    </main>
  )
}

export default ResizableWorkspace
