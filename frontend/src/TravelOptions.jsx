import { useEffect, useState } from 'react'
import OriginPicker from './OriginPicker.jsx'
import {
  ACCESS_REQUIREMENTS,
  DEFAULT_ORIGIN,
  MOBILITY_OPTIONS,
  TRANSPORT_OPTIONS,
  TRAVEL_STYLES,
  formatTripDuration,
  getTripDays,
  getTripEndDate,
  isTravelDate,
  getKoreaToday,
  isPastTravelDate,
} from './services/travelOptions.js'

function SvgIcon({ size = 20, strokeWidth = 1.8, children }) {
  return (
    <svg
      className="workspace-icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

function CheckIcon() {
  return (
    <SvgIcon size={16} strokeWidth={2.4}>
      <path d="m5 12 5 5 9-10" />
    </SvgIcon>
  )
}

const TRANSPORT_ICONS = {
  walking: (
    <SvgIcon>
      <circle cx="13" cy="4" r="1.8" />
      <path d="m9 21 2.5-6.5L14 17v4M10 12l1.2-4.2L15 10.5l3 1M11.2 7.8 7.5 9.5V13" />
    </SvgIcon>
  ),
  driving: (
    <SvgIcon>
      <path d="M4 13.5 6 8h12l2 5.5V18H4z" />
      <path d="M6.5 18v2M17.5 18v2M4 13.5h16" />
    </SvgIcon>
  ),
  transit: (
    <SvgIcon>
      <rect x="5" y="3" width="14" height="15" rx="2" />
      <path d="M5 11h14M8 21v-3M16 21v-3" />
    </SvgIcon>
  ),
}

const MOBILITY_ICONS = {
  wheelchair: (
    <SvgIcon>
      <circle cx="11" cy="4" r="1.8" />
      <path d="M11 7.5V13h5l2.5 6" />
      <path d="M11 10h4.5" />
      <path d="M8 10.5a5.5 5.5 0 1 0 7.3 7.5" />
    </SvgIcon>
  ),
  stroller: (
    <SvgIcon>
      <path d="M4 5h2l1.5 8h10.5" />
      <path d="M7.5 13a6 6 0 0 1 11.5-2.5" />
      <path d="M13 4.5V10h6" />
      <circle cx="9" cy="18.5" r="1.8" />
      <circle cx="17" cy="18.5" r="1.8" />
    </SvgIcon>
  ),
}

function ChoiceButtons({ labelId, value, choices, icons = {}, stacked = false, onChange }) {
  let listClassName = 'option-choice-list'
  if (stacked) {
    listClassName = 'option-choice-list is-stacked'
  }

  return (
    <div className={listClassName} role="group" aria-labelledby={labelId}>
      {choices.map((choice) => {
        const selected = value === choice.value

        // 선택된 버튼을 다시 누르면 미선택으로 되돌립니다.
        let nextValue = choice.value
        if (selected) {
          nextValue = null
        }

        return (
          <button
            key={choice.value}
            type="button"
            className="option-choice"
            aria-pressed={selected}
            onClick={() => onChange(nextValue)}
          >
            {icons[choice.value]}
            <span>{choice.label}</span>
            {selected && !stacked && <CheckIcon />}
          </button>
        )
      })}
    </div>
  )
}

function ChoiceCheckbox({ label, checked, onChange }) {
  let className = 'option-choice'
  if (checked) {
    className = 'option-choice is-selected'
  }

  return (
    <label className={className}>
      <input
        type="checkbox"
        className="option-choice-input"
        checked={checked}
        onChange={onChange}
      />
      <span>{label}</span>
      {checked && <CheckIcon />}
    </label>
  )
}

function TravelOptions({
  options,
  originPlace = null,
  onChange,
  onOriginConfirm,
  disabled = false,
  isOpen = true,
  onToggleOpen,
}) {
  const [originOpen, setOriginOpen] = useState(false)
  const [dateIssue, setDateIssue] = useState(null)
  const [today, setToday] = useState(getKoreaToday)
  const dateKey = JSON.stringify([options.start_date, options.trip_days])
  const originKey = JSON.stringify([
    options.departure,
    options.departure_latitude,
    options.departure_longitude,
  ])

  useEffect(() => {
    const refreshToday = () => setToday(getKoreaToday())
    const nextMidnight = Date.parse(`${today}T00:00:00+09:00`)
      + 24 * 60 * 60 * 1000

    const timer = window.setTimeout(
      refreshToday,
      Math.max(1, nextMidnight - Date.now() + 100),
    )

    window.addEventListener('focus', refreshToday)
    document.addEventListener('visibilitychange', refreshToday)

    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('focus', refreshToday)
      document.removeEventListener('visibilitychange', refreshToday)
    }
  }, [today])

  const confirmedOrigin = originPlace
    && originPlace.name === options.departure
    && originPlace.latitude === options.departure_latitude
    && originPlace.longitude === options.departure_longitude
    ? originPlace
    : null

  let endDate = ''
  let calendarError = ''
  try {
    endDate = getTripEndDate(options.start_date, options.trip_days)
    if (options.start_date && options.start_date < today) {
      calendarError = '출발일이 지났습니다. 여행 날짜를 다시 선택해 주세요.'
    }
  } catch (error) {
    calendarError = error.message
  }
  const dateError = calendarError
    || (dateIssue?.key === dateKey ? dateIssue.message : '')

  function recordDateError(error) {
    setDateIssue({ key: dateKey, message: error.message })
  }

  function updateDates(patch) {
    if (disabled) return
    try {
      const next = { ...options, ...patch }
      if (isPastTravelDate(next.start_date)) {
        throw new Error('출발일은 오늘 이후 날짜로 선택해 주세요.')
      }
      getTripEndDate(next.start_date, next.trip_days)
      setDateIssue(null)
      onChange(patch)
    } catch (error) {
      recordDateError(error)
    }
  }

  function handleStartDate(value) {
    if (disabled) return

    if (value && !isTravelDate(value)) {
      recordDateError(new Error('여행 날짜를 다시 확인해 주세요.'))
      return
    }

    try {
      const startDate = value || null
      const tripDays = startDate && endDate && endDate >= startDate
        ? getTripDays(startDate, endDate)
        : null

      updateDates({
        start_date: startDate,
        trip_days: tripDays,
      })
    } catch (error) {
      recordDateError(error)
    }
  }

  function handleEndDate(value) {
    if (disabled) return

    try {
      if (isPastTravelDate(value)) {
        throw new Error('마지막 날은 오늘 이후 날짜로 선택해 주세요.')
      }

      updateDates({
        trip_days: getTripDays(options.start_date, value),
      })
    } catch (error) {
      recordDateError(error)
    }
  }

  function toggleList(field, value) {
    if (disabled) return
    const selected = options[field]
    onChange({
      [field]: selected.includes(value)
        ? selected.filter((item) => item !== value)
        : [...selected, value],
    })
  }

  let toggleLabel = '옵션 펼치기'
  if (isOpen) {
    toggleLabel = '옵션 접기'
  }

  return (
    <section
      id="workspace-options-pane"
      className="workspace-options"
      aria-labelledby="workspace-options-title"
      data-tour="options"
    >
      <div className="workspace-card-heading">
        <div className="workspace-heading-text">
          <h2 id="workspace-options-title">여행 옵션</h2>
          <p>필요한 항목만 선택하세요. 대화로 알려주셔도 됩니다.</p>
        </div>

        {typeof onToggleOpen === 'function' && (
          <button
            type="button"
            className="secondary-button workspace-options-toggle"
            aria-expanded={isOpen}
            aria-controls="workspace-options-content"
            onClick={onToggleOpen}
          >
            {toggleLabel}
            <SvgIcon size={18} strokeWidth={2}>
              <path d="m6 15 6-6 6 6" />
            </SvgIcon>
          </button>
        )}
      </div>

      <div id="workspace-options-content" hidden={!isOpen}>
        <fieldset className="options-fieldset" disabled={disabled}>
          <legend className="sr-only">선택할 여행 조건</legend>

          <div className="option-tiles">
            <section className="option-tile" aria-labelledby="option-origin-title">
              <div className="option-tile-heading">
                <h3 id="option-origin-title">
                  <span className="option-tile-icon">
                    <SvgIcon size={16} strokeWidth={2.2}>
                      <path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z" />
                      <circle cx="12" cy="10" r="2.5" />
                    </SvgIcon>
                  </span>
                  출발지
                </h3>
              </div>
              <div className="option-tile-body">
                <div className="option-origin-box">
                  <span className="option-origin-icon">
                    <SvgIcon>
                      <path d="M4 21V9l8-5 8 5v12" />
                      <path d="M9 21v-6h6v6" />
                    </SvgIcon>
                  </span>
                  <div className="option-origin-text">
                    <p className="option-value">{options.departure || DEFAULT_ORIGIN}</p>
                    {confirmedOrigin?.address && (
                      <p className="options-note">{confirmedOrigin.address}</p>
                    )}
                  </div>
                </div>
                <details
                  className="option-origin-details"
                  open={originOpen}
                  onToggle={(event) => setOriginOpen(event.currentTarget.open)}
                >
                  <summary
                    aria-disabled={disabled}
                    onClick={(event) => {
                      if (disabled) event.preventDefault()
                    }}
                  >
                    출발지 변경
                  </summary>
                  {originOpen && (
                    <OriginPicker
                      key={originKey}
                      initialKeyword={options.departure || DEFAULT_ORIGIN}
                      confirmedPlace={confirmedOrigin}
                      onConfirm={(place) => {
                        if (disabled) return
                        onOriginConfirm(place)
                      }}
                    />
                  )}
                </details>
              </div>
            </section>

            <section className="option-tile" aria-labelledby="option-date-title">
              <div className="option-tile-heading">
                <h3 id="option-date-title">
                  <span className="option-tile-icon">
                    <SvgIcon size={16} strokeWidth={2.2}>
                      <rect x="3.5" y="5" width="17" height="15" rx="2" />
                      <path d="M3.5 10h17M8 3v4M16 3v4" />
                    </SvgIcon>
                  </span>
                  여행 날짜와 기간
                </h3>
                <span className="option-duration">
                  {formatTripDuration(options.trip_days)}
                </span>
              </div>
              <div className="option-tile-body">
                <div className="options-date-grid">
                  <label className="option-select" htmlFor="option-start-date">
                    <span>출발일</span>
                    <input
                      id="option-start-date"
                      type="date"
                      value={options.start_date ?? ''}
                      min={today}
                      aria-invalid={Boolean(dateError)}
                      onChange={(event) => handleStartDate(event.target.value)}
                    />
                  </label>
                  <label className="option-select" htmlFor="option-end-date">
                    <span>마지막 날</span>
                    <input
                      id="option-end-date"
                      type="date"
                      value={endDate}
                      min={options.start_date && options.start_date > today
                        ? options.start_date : today}
                      disabled={!options.start_date}
                      aria-invalid={Boolean(dateError)}
                      onChange={(event) => handleEndDate(event.target.value)}
                    />
                  </label>
                </div>
                <p className="options-note">당일은 같은 날짜를 선택하세요.</p>
                {dateError && (
                  <p className="form-error" role="alert">{dateError}</p>
                )}
              </div>
            </section>

            <section className="option-tile" aria-labelledby="option-transport-title">
              <div className="option-tile-heading">
                <h3 id="option-transport-title">
                  <span className="option-tile-icon">
                    <SvgIcon size={16} strokeWidth={2.2}>
                      <circle cx="6" cy="19" r="2" />
                      <circle cx="18" cy="5" r="2" />
                      <path d="M8 19h8a4 4 0 0 0 0-8H8a4 4 0 0 1 0-8h8" />
                    </SvgIcon>
                  </span>
                  이동수단
                </h3>
              </div>
              <div className="option-tile-body">
                <ChoiceButtons
                  labelId="option-transport-title"
                  value={options.transport}
                  choices={TRANSPORT_OPTIONS}
                  icons={TRANSPORT_ICONS}
                  stacked
                  onChange={(value) => onChange({ transport: value })}
                />
              </div>
            </section>

            <section className="option-tile option-group" aria-labelledby="option-mobility-title">
              <div className="option-tile-heading">
                <h3 id="option-mobility-title">
                  <span className="option-tile-icon">
                    <SvgIcon size={16} strokeWidth={2.2}>
                      <circle cx="11" cy="4" r="1.8" />
                      <path d="M11 7.5V13h5l2.5 6" />
                      <path d="M11 10h4.5" />
                      <path d="M8 10.5a5.5 5.5 0 1 0 7.3 7.5" />
                    </SvgIcon>
                  </span>
                  이동 보조기구
                </h3>
              </div>
              <div className="option-tile-body">
                <ChoiceButtons
                  labelId="option-mobility-title"
                  value={options.mobility_aid}
                  choices={MOBILITY_OPTIONS}
                  icons={MOBILITY_ICONS}
                  onChange={(value) => onChange({ mobility_aid: value })}
                />
              </div>
            </section>

            <div
              className="option-tile option-group"
              role="group"
              aria-labelledby="option-requirements-title"
            >
              <div className="option-tile-heading">
                <h3 id="option-requirements-title">
                  <span className="option-tile-icon">
                    <SvgIcon size={16} strokeWidth={2.2}>
                      <path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6z" />
                      <path d="m9 12 2 2 4-4" />
                    </SvgIcon>
                  </span>
                  필요한 접근 조건
                </h3>
              </div>
              <div className="option-tile-body">
                <div className="option-choice-list">
                  {ACCESS_REQUIREMENTS.map((item) => (
                    <ChoiceCheckbox
                      key={item.value}
                      label={item.label}
                      checked={options.requirements.includes(item.value)}
                      onChange={() => toggleList('requirements', item.value)}
                    />
                  ))}
                </div>
              </div>
            </div>

            <div
              className="option-tile option-group"
              role="group"
              aria-labelledby="option-styles-title"
            >
              <div className="option-tile-heading">
                <h3 id="option-styles-title">
                  <span className="option-tile-icon">
                    <SvgIcon size={16} strokeWidth={2.2}>
                      <circle cx="12" cy="12" r="9" />
                      <path d="m15.5 8.5-2 5-5 2 2-5z" />
                    </SvgIcon>
                  </span>
                  여행 종류
                </h3>
              </div>
              <div className="option-tile-body">
                <div className="option-choice-list">
                  {TRAVEL_STYLES.map((style) => (
                    <ChoiceCheckbox
                      key={style}
                      label={style}
                      checked={options.travel_styles.includes(style)}
                      onChange={() => toggleList('travel_styles', style)}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </fieldset>
      </div>
    </section>
  )
}

export default TravelOptions
