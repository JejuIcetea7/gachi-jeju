import { useEffect, useRef, useState } from 'react'

const STORAGE_KEY = 'gachi-jeju.workspace-sidebar.v2'
const DESKTOP_QUERY = '(min-width: 901px)'
const SIDEBAR_MIN_WIDTH = 420
// 사이드바가 열려도 왼쪽 대화창은 이 폭보다 좁아지지 않습니다.
const CHAT_MIN_WIDTH = 360
const DEFAULT_SIDEBAR_RATIO = 0.6
const DEFAULT_SIDEBAR_MAX_WIDTH = 880
const KEYBOARD_STEP = 24
const KEYBOARD_LARGE_STEP = 80

// 사용자가 너비를 바꾸지 않았으면 null을 돌려주고 기본 너비를 씁니다.
function loadSidebarRatio() {
  let sidebarRatio = null

  try {
    const savedRatio = Number(JSON.parse(window.localStorage.getItem(STORAGE_KEY)))
    if (Number.isFinite(savedRatio) && savedRatio > 0 && savedRatio <= 1) {
      sidebarRatio = savedRatio
    }
  } catch {
    // 저장소를 사용할 수 없어도 기본 너비로 화면을 표시합니다.
  }

  return sidebarRatio
}

function calculateSidebarWidth(containerWidth, sidebarRatio) {
  let maximumWidth = containerWidth - CHAT_MIN_WIDTH
  if (maximumWidth < SIDEBAR_MIN_WIDTH) {
    maximumWidth = SIDEBAR_MIN_WIDTH
  }

  let sidebarWidth = containerWidth * sidebarRatio
  if (sidebarWidth < SIDEBAR_MIN_WIDTH) {
    sidebarWidth = SIDEBAR_MIN_WIDTH
  }
  if (sidebarWidth > maximumWidth) {
    sidebarWidth = maximumWidth
  }

  return Math.round(sidebarWidth)
}

function calculateDefaultSidebarWidth(containerWidth) {
  let defaultWidth = containerWidth * DEFAULT_SIDEBAR_RATIO
  if (defaultWidth > DEFAULT_SIDEBAR_MAX_WIDTH) {
    defaultWidth = DEFAULT_SIDEBAR_MAX_WIDTH
  }

  const defaultRatio = defaultWidth / containerWidth
  return calculateSidebarWidth(containerWidth, defaultRatio)
}

function ResizableWorkspace({
  main,
  sidebar,
  sidebarId,
  sidebarTitleId,
  sidebarOpen = false,
  onCloseSidebar,
}) {
  const containerRef = useRef(null)
  const sidebarRef = useRef(null)
  const dragRef = useRef(null)
  const previousFocusRef = useRef(null)
  const [sidebarRatio, setSidebarRatio] = useState(loadSidebarRatio)
  const [containerWidth, setContainerWidth] = useState(0)
  const [isResizing, setIsResizing] = useState(false)

  const isDesktop = containerWidth > 0
  let sidebarWidth = null
  let maximumWidth = SIDEBAR_MIN_WIDTH
  if (isDesktop) {
    sidebarWidth = calculateDefaultSidebarWidth(containerWidth)
    if (sidebarRatio !== null) {
      sidebarWidth = calculateSidebarWidth(containerWidth, sidebarRatio)
    }
    maximumWidth = calculateSidebarWidth(containerWidth, 1)
  }

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const observer = new ResizeObserver(() => {
      let nextWidth = 0
      if (window.matchMedia(DESKTOP_QUERY).matches) {
        nextWidth = container.clientWidth
      }

      setContainerWidth(nextWidth)
      dragRef.current = null
      setIsResizing(false)
    })

    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    try {
      if (sidebarRatio === null) {
        window.localStorage.removeItem(STORAGE_KEY)
      } else {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sidebarRatio))
      }
    } catch {
      // 저장에 실패해도 현재 화면의 너비 조절은 유지합니다.
    }
  }, [sidebarRatio])

  useEffect(() => {
    if (!sidebarOpen) return

    previousFocusRef.current = document.activeElement
    const sidebarElement = sidebarRef.current
    const title = document.getElementById(sidebarTitleId)
    title?.focus({ preventScroll: true })

    return () => {
      const activeElement = document.activeElement
      const focusWasInSidebar = activeElement === document.body
        || Boolean(sidebarElement?.contains(activeElement))
      if (!focusWasInSidebar) return

      // 열기 전 포커스가 없었으면 사이드바를 여닫는 버튼으로 돌려보냅니다.
      let returnElement = previousFocusRef.current
      if (!returnElement?.isConnected || returnElement === document.body) {
        returnElement = document.querySelector(`[aria-controls="${sidebarId}"][aria-expanded]`)
      }
      returnElement?.focus({ preventScroll: true })
    }
  }, [sidebarOpen, sidebarId, sidebarTitleId])

  function updateSidebarWidth(nextWidth, baseWidth) {
    if (baseWidth <= 0) return

    const clampedWidth = calculateSidebarWidth(baseWidth, nextWidth / baseWidth)
    setSidebarRatio(clampedWidth / baseWidth)
  }

  function stopResizing(event) {
    if (dragRef.current?.pointerId !== event.pointerId) return

    dragRef.current = null
    setIsResizing(false)

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  function startResizing(event) {
    if (!isDesktop || dragRef.current || event.button !== 0 || !event.isPrimary) return

    event.preventDefault()
    event.currentTarget.focus()
    event.currentTarget.setPointerCapture(event.pointerId)

    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: sidebarWidth,
      containerWidth,
    }
    setIsResizing(true)
  }

  function moveResizing(event) {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return

    const movedDistance = event.clientX - drag.startX
    const nextWidth = drag.startWidth - movedDistance
    updateSidebarWidth(nextWidth, drag.containerWidth)
  }

  function handleDividerKeyDown(event) {
    if (!isDesktop || dragRef.current) return

    let step = KEYBOARD_STEP
    if (event.shiftKey) {
      step = KEYBOARD_LARGE_STEP
    }

    let nextWidth = null
    if (event.key === 'ArrowLeft') nextWidth = sidebarWidth + step
    if (event.key === 'ArrowRight') nextWidth = sidebarWidth - step
    if (event.key === 'Home') nextWidth = SIDEBAR_MIN_WIDTH
    if (event.key === 'End') nextWidth = maximumWidth
    if (nextWidth === null) return

    event.preventDefault()
    updateSidebarWidth(nextWidth, containerWidth)
  }

  function handleSidebarKeyDown(event) {
    if (event.key !== 'Escape') return
    if (event.target.closest('dialog')) return
    if (typeof onCloseSidebar !== 'function') return

    event.preventDefault()
    onCloseSidebar()
  }

  let bodyClassName = 'workspace-body'
  if (isResizing) {
    bodyClassName = 'workspace-body is-resizing'
  }

  // 사이드바 폭은 바깥 틀에 지정해 사이드바와 왼쪽 대화창이 함께 씁니다.
  let bodyStyle
  if (sidebarWidth !== null) {
    bodyStyle = { '--workspace-sidebar-width': `${sidebarWidth}px` }
  }

  let sidebarState = 'closed'
  if (sidebarOpen) {
    sidebarState = 'open'
  }

  let currentWidth = SIDEBAR_MIN_WIDTH
  if (sidebarWidth !== null) {
    currentWidth = sidebarWidth
  }

  return (
    <div
      ref={containerRef}
      className={bodyClassName}
      data-sidebar-state={sidebarState}
      style={bodyStyle}
    >
      <main className="workspace-main">
        <div className="workspace-main-inner">
          {main}
        </div>
      </main>

      <aside
        id={sidebarId}
        ref={sidebarRef}
        className="workspace-sidebar"
        data-state={sidebarState}
        aria-labelledby={sidebarTitleId}
        inert={!sidebarOpen}
        onKeyDown={handleSidebarKeyDown}
      >
        <div
          className="workspace-sidebar-divider"
          role="separator"
          aria-orientation="vertical"
          aria-label="지도·일정 너비 조절"
          aria-controls={sidebarId}
          aria-valuemin={SIDEBAR_MIN_WIDTH}
          aria-valuemax={maximumWidth}
          aria-valuenow={currentWidth}
          aria-valuetext={`${currentWidth}픽셀`}
          aria-disabled={!isDesktop}
          tabIndex={isDesktop ? 0 : -1}
          data-active={isResizing ? 'true' : undefined}
          title="드래그하거나 좌우 방향키로 너비 조절 · 두 번 클릭하면 기본 너비"
          onPointerDown={startResizing}
          onPointerMove={moveResizing}
          onPointerUp={stopResizing}
          onPointerCancel={stopResizing}
          onLostPointerCapture={stopResizing}
          onKeyDown={handleDividerKeyDown}
          onDoubleClick={() => setSidebarRatio(null)}
        />

        <div className="workspace-sidebar-scroll">
          {sidebar}
        </div>
      </aside>
    </div>
  )
}

export default ResizableWorkspace
