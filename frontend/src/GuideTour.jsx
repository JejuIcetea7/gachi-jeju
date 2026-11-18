import { useEffect, useId, useRef, useState } from 'react'

// investment-bite 프로젝트의 TourOverlay와 같은 방식으로 화면을 안내합니다.
// 설명할 부분만 밝게 남기고 나머지는 어둡게 덮은 뒤, 옆에 설명 카드와 캐릭터를 띄웁니다.
const CARD_WIDTH = 320
const CARD_MIN_HEIGHT = 180
const SPOT_PADDING = 8
const CARD_GAP = 12
const VIEWPORT_MARGIN = 16
const CHARACTER_OVERLAP = 70
// 캐릭터가 카드 위로 솟아오르는 높이입니다. 카드를 대상 아래에 둘 때 캐릭터가 대상을 가리지 않게 이만큼 띄웁니다.
const CHARACTER_RISE = 84
// 캐릭터가 카드 왼쪽 아래로 갈 때 카드 아래로 내려가는 높이와 왼쪽으로 나가는 폭입니다(CSS와 같은 값).
const CHARACTER_DROP = 128
const CHARACTER_LEFT_OVERLAP = 24
const NARROW_QUERY = '(max-width: 600px)'
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'
// 사이드바가 열리는 동안(0.24초) 대상 위치가 바뀌므로 몇 번 다시 잽니다.
const MEASURE_DELAYS = [0, 280, 560]

function findTourTarget(targetName) {
  return document.querySelector(`[data-tour="${targetName}"]`)
}

function clampValue(value, minimum, maximum) {
  let clampedValue = value
  if (clampedValue > maximum) {
    clampedValue = maximum
  }
  if (clampedValue < minimum) {
    clampedValue = minimum
  }
  return clampedValue
}

function measureSpot(target) {
  if (!target) return null

  const rect = target.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return null

  return {
    x: rect.left - SPOT_PADDING,
    y: rect.top - SPOT_PADDING,
    w: rect.width + SPOT_PADDING * 2,
    h: rect.height + SPOT_PADDING * 2,
  }
}

function calculateCardPosition(spot, placement, cardWidth, cardHeight, hasCharacter) {
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight

  let characterOverlap = 0
  let bottomGap = CARD_GAP
  if (hasCharacter) {
    characterOverlap = CHARACTER_OVERLAP
    bottomGap = CHARACTER_RISE
  }

  // 정한 쪽(위·아래)에 카드 자리가 없으면 반대쪽에 두고, 양쪽 다 없으면 대상 위에 겹쳐 둡니다.
  const spaceAbove = spot.y - VIEWPORT_MARGIN
  const spaceBelow = viewportHeight - (spot.y + spot.h) - VIEWPORT_MARGIN
  const fitsAbove = spaceAbove >= cardHeight + CARD_GAP
  const fitsBelow = spaceBelow >= cardHeight + bottomGap

  let finalPlacement = placement
  if (placement === 'top' && !fitsAbove) {
    finalPlacement = 'over'
    if (fitsBelow) {
      finalPlacement = 'bottom'
    }
  } else if (placement === 'bottom' && !fitsBelow) {
    finalPlacement = 'over'
    if (fitsAbove) {
      finalPlacement = 'top'
    }
  }

  let left = spot.x + spot.w + CARD_GAP
  let top = spot.y + spot.h / 2 - cardHeight / 2

  if (finalPlacement === 'bottom') {
    left = spot.x + spot.w / 2 - cardWidth / 2
    top = spot.y + spot.h + bottomGap
  } else if (finalPlacement === 'top') {
    left = spot.x + spot.w / 2 - cardWidth / 2
    top = spot.y - cardHeight - CARD_GAP
  } else if (finalPlacement === 'left') {
    left = spot.x - cardWidth - CARD_GAP - characterOverlap
  } else if (finalPlacement === 'over') {
    // 대상 중 화면에 보이는 부분의 가운데에 둡니다.
    let visibleTop = spot.y
    if (visibleTop < 0) {
      visibleTop = 0
    }
    let visibleBottom = spot.y + spot.h
    if (visibleBottom > viewportHeight) {
      visibleBottom = viewportHeight
    }
    left = spot.x + spot.w / 2 - cardWidth / 2
    top = (visibleTop + visibleBottom) / 2 - cardHeight / 2
  }

  const maximumLeft = viewportWidth - cardWidth - characterOverlap - VIEWPORT_MARGIN
  const maximumTop = viewportHeight - cardHeight - VIEWPORT_MARGIN

  return {
    left: clampValue(left, VIEWPORT_MARGIN, maximumLeft),
    top: clampValue(top, VIEWPORT_MARGIN, maximumTop),
  }
}

// 캐릭터가 화면 밖으로 잘리지 않을 자리를 고릅니다.
// 기본은 카드 오른쪽 위, 위가 좁으면 왼쪽 아래, 둘 다 좁으면 작게 줄여 카드 안 오른쪽 위입니다.
function chooseCharacterPlacement(cardPosition, cardHeight) {
  let characterPlacement = 'inside'
  const roomAbove = cardPosition.top
  const roomBelow = window.innerHeight - (cardPosition.top + cardHeight)

  if (roomAbove >= CHARACTER_RISE) {
    characterPlacement = 'top-right'
  } else if (roomBelow >= CHARACTER_DROP + VIEWPORT_MARGIN) {
    characterPlacement = 'bottom-left'
  }
  return characterPlacement
}

// 좁은 화면에서는 카드를 화면 아래쪽에 고정합니다. 대상은 위쪽으로 스크롤해 보여 줍니다.
function calculateBottomSheetPosition(cardHeight) {
  return {
    left: VIEWPORT_MARGIN,
    top: window.innerHeight - cardHeight - VIEWPORT_MARGIN,
  }
}

function calculateCenteredPosition(cardWidth, cardHeight) {
  return {
    left: clampValue((window.innerWidth - cardWidth) / 2, VIEWPORT_MARGIN, window.innerWidth),
    top: clampValue((window.innerHeight - cardHeight) / 2, VIEWPORT_MARGIN, window.innerHeight),
  }
}

function GuideTour({
  active = false,
  step = 0,
  steps = [],
  onNext,
  onPrevious,
  onSkip,
}) {
  const titleId = useId()
  const textId = useId()
  const cardRef = useRef(null)
  const [layout, setLayout] = useState(null)

  let current = null
  if (active && steps[step]) {
    current = steps[step]
  }

  useEffect(() => {
    if (!current) return

    let isAlive = true
    const timers = []

    function updateLayout() {
      if (!isAlive) return

      const card = cardRef.current
      let cardWidth = CARD_WIDTH
      let cardHeight = CARD_MIN_HEIGHT
      if (card) {
        cardWidth = card.offsetWidth
        cardHeight = card.offsetHeight
      }

      const isNarrow = window.matchMedia(NARROW_QUERY).matches
      const hasCharacter = Boolean(current.character)

      const spot = measureSpot(findTourTarget(current.target))
      let cardPosition = calculateCenteredPosition(cardWidth, cardHeight)
      if (isNarrow) {
        cardPosition = calculateBottomSheetPosition(cardHeight)
      } else if (spot) {
        cardPosition = calculateCardPosition(
          spot,
          current.placement,
          cardWidth,
          cardHeight,
          hasCharacter,
        )
      }

      // 좁은 화면은 카드가 화면 아래에 붙어 있어 캐릭터 자리가 항상 위에 있습니다.
      let characterPlacement = 'top-right'
      if (!isNarrow) {
        characterPlacement = chooseCharacterPlacement(cardPosition, cardHeight)
      }

      // 캐릭터가 왼쪽 아래로 가면 카드 왼쪽 바깥으로 나가므로 그만큼 카드를 오른쪽으로 옮깁니다.
      const minimumLeftForCharacter = VIEWPORT_MARGIN + CHARACTER_LEFT_OVERLAP
      if (characterPlacement === 'bottom-left' && cardPosition.left < minimumLeftForCharacter) {
        cardPosition = { ...cardPosition, left: minimumLeftForCharacter }
      }

      setLayout({ spot, card: cardPosition, characterPlacement })
    }

    function scrollTargetIntoView() {
      const target = findTourTarget(current.target)
      if (!target) return

      let behavior = 'smooth'
      if (window.matchMedia(REDUCED_MOTION_QUERY).matches) {
        behavior = 'auto'
      }
      // 큰 대상도 시작 부분부터 보이도록 위쪽 끝을 화면 위에 맞춥니다.
      target.scrollIntoView({ behavior, block: 'start', inline: 'nearest' })
    }

    for (const delay of MEASURE_DELAYS) {
      const timer = window.setTimeout(() => {
        if (delay < MEASURE_DELAYS[MEASURE_DELAYS.length - 1]) {
          scrollTargetIntoView()
        }
        window.requestAnimationFrame(updateLayout)
      }, delay)
      timers.push(timer)
    }

    window.addEventListener('resize', updateLayout)
    window.addEventListener('scroll', updateLayout, true)

    return () => {
      isAlive = false
      for (const timer of timers) {
        window.clearTimeout(timer)
      }
      window.removeEventListener('resize', updateLayout)
      window.removeEventListener('scroll', updateLayout, true)
    }
  }, [current])

  // 단계가 바뀌면 카드로 포커스를 옮겨 화면 낭독기가 새 설명을 읽게 합니다.
  useEffect(() => {
    if (!current) return

    const timer = window.setTimeout(() => {
      cardRef.current?.focus({ preventScroll: true })
    }, 80)
    return () => window.clearTimeout(timer)
  }, [current])

  if (!current) return null

  function handleKeyDown(event) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onSkip?.()
      return
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault()
      onNext?.()
      return
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      if (step > 0) onPrevious?.()
      return
    }
    if (event.key !== 'Tab') return

    // 가이드가 떠 있는 동안에는 카드 안 버튼 사이에서만 이동합니다.
    const buttons = cardRef.current?.querySelectorAll('button') ?? []
    if (buttons.length === 0) return

    const firstButton = buttons[0]
    const lastButton = buttons[buttons.length - 1]
    if (event.shiftKey && document.activeElement === firstButton) {
      event.preventDefault()
      lastButton.focus()
    } else if (!event.shiftKey && document.activeElement === lastButton) {
      event.preventDefault()
      firstButton.focus()
    } else if (!cardRef.current?.contains(document.activeElement)) {
      event.preventDefault()
      firstButton.focus()
    }
  }

  let isLastStep = false
  if (step === steps.length - 1) {
    isLastStep = true
  }

  let spotStyle = null
  if (layout?.spot) {
    spotStyle = {
      left: layout.spot.x,
      top: layout.spot.y,
      width: layout.spot.w,
      height: layout.spot.h,
    }
  }

  let cardStyle = { left: VIEWPORT_MARGIN, top: VIEWPORT_MARGIN, visibility: 'hidden' }
  if (layout) {
    cardStyle = { left: layout.card.left, top: layout.card.top }
  }

  return (
    <div
      className="guide-tour"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={textId}
      onKeyDown={handleKeyDown}
    >
      {spotStyle ? (
        <div className="guide-tour-spot" style={spotStyle} />
      ) : (
        <div className="guide-tour-backdrop" />
      )}

      <div
        ref={cardRef}
        className="guide-tour-card"
        style={cardStyle}
        data-character={layout?.characterPlacement ?? 'top-right'}
        tabIndex={-1}
      >
        {current.character && (
          <img className="guide-tour-character" src={current.character} alt="" />
        )}
        <span className="guide-tour-step">
          STEP {step + 1} / {steps.length}
        </span>
        <h2 id={titleId} className="guide-tour-title">{current.title}</h2>
        <p id={textId} className="guide-tour-text">{current.text}</p>
        <div className="guide-tour-foot">
          <button type="button" className="guide-tour-skip" onClick={onSkip}>
            건너뛰기
          </button>
          <button type="button" className="guide-tour-next" onClick={onNext}>
            {isLastStep ? '시작하기' : '다음'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default GuideTour
