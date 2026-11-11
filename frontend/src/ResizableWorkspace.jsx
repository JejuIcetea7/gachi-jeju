import { Children, useEffect, useRef, useState } from 'react'
import {
  DEFAULT_PANEL_WEIGHTS,
  PANEL_GUTTER_WIDTH,
  PANEL_MIN_WIDTHS,
  getPanelWidths,
  isPanelWeights,
  resizePanelWeights,
} from './services/workspacePanels.js'

const STORAGE_KEY = 'gachi-jeju.workspace-panels'
const DIVIDERS = [
  { paneId: 'workspace-options-pane', titleId: 'workspace-options-title' },
  { paneId: 'workspace-chat-pane', titleId: 'conversation-title' },
]

function loadPanelWeights() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY))
    if (isPanelWeights(saved)) return saved
  } catch {
    // 저장소를 사용할 수 없어도 기본 너비로 화면을 표시합니다.
  }
  return [...DEFAULT_PANEL_WEIGHTS]
}

function ResizableWorkspace({ children }) {
  const containerRef = useRef(null)
  const dragRef = useRef(null)
  const [weights, setWeights] = useState(loadPanelWeights)
  const [totalWidth, setTotalWidth] = useState(0)
  const [activeDivider, setActiveDivider] = useState(null)
  const panels = Children.toArray(children)
  const widths = getPanelWidths(totalWidth, weights)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const observer = new ResizeObserver(() => {
      const desktop = window.matchMedia('(min-width: 1101px)').matches
      const style = window.getComputedStyle(container)
      const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight)

      setTotalWidth(desktop
        ? Math.max(0, container.clientWidth - padding - PANEL_GUTTER_WIDTH * 2)
        : 0)

      dragRef.current = null
      setActiveDivider(null)
    })

    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(weights))
    } catch {
      // 저장에 실패해도 현재 화면의 너비 조절은 유지합니다.
    }
  }, [weights])

  function stopDragging(event) {
    if (dragRef.current?.pointerId !== event.pointerId) return

    dragRef.current = null
    setActiveDivider(null)

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  function startDragging(event, divider) {
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
    )
    if (next) setWeights(next)
  }

  function handleKeyDown(event, divider) {
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

    const next = resizePanelWeights(totalWidth, weights, divider, delta)
    if (next) setWeights(next)
  }

  function renderDivider(index) {
    const current = widths?.[index] ?? PANEL_MIN_WIDTHS[index]
    const pairWidth = widths
      ? widths[index] + widths[index + 1]
      : PANEL_MIN_WIDTHS[index] + PANEL_MIN_WIDTHS[index + 1]

    return (
      <div
        className="workspace-divider"
        role="separator"
        aria-orientation="vertical"
        aria-labelledby={DIVIDERS[index].titleId}
        aria-controls={DIVIDERS[index].paneId}
        aria-valuemin={PANEL_MIN_WIDTHS[index]}
        aria-valuemax={Math.round(pairWidth - PANEL_MIN_WIDTHS[index + 1])}
        aria-valuenow={Math.round(current)}
        aria-valuetext={`${Math.round(current)}픽셀`}
        aria-disabled={!widths}
        tabIndex={widths ? 0 : -1}
        data-active={activeDivider === index ? 'true' : undefined}
        title="드래그하거나 좌우 방향키로 너비 조절 · 두 번 클릭하면 기본 너비"
        onPointerDown={(event) => startDragging(event, index)}
        onPointerMove={moveDivider}
        onPointerUp={stopDragging}
        onPointerCancel={stopDragging}
        onLostPointerCapture={stopDragging}
        onKeyDown={(event) => handleKeyDown(event, index)}
        onDoubleClick={() => setWeights([...DEFAULT_PANEL_WEIGHTS])}
      />
    )
  }

  return (
    <main
      ref={containerRef}
      className={`workspace-layout workspace-resizable${activeDivider === null ? '' : ' is-resizing'}`}
      style={widths ? {
        '--workspace-options-width': `${widths[0]}px`,
        '--workspace-chat-width': `${widths[1]}px`,
        '--workspace-results-width': `${widths[2]}px`,
      } : undefined}
    >
      {panels[0]}
      {renderDivider(0)}
      {panels[1]}
      {renderDivider(1)}
      {panels[2]}
    </main>
  )
}

export default ResizableWorkspace