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

function OptionSelect({ id, label, value, choices, onChange }) {
  return (
    <label className="option-select" htmlFor={id}>
      <span>{label}</span>
      <select
        id={id}
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">미선택</option>
        {choices.map((choice) => (
          <option key={choice.value} value={choice.value}>
            {choice.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function TravelOptions({
  options,
  originPlace = null,
  onChange,
  onOriginConfirm,
  disabled = false,
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

  return (
    <aside
      id="workspace-options-pane"
      className="workspace-options"
      aria-labelledby="workspace-options-title"
    >
      <div className="workspace-panel-heading">
        <h2 id="workspace-options-title">여행 옵션</h2>
        <p>필요한 항목만 선택하세요. 대화로 알려주셔도 됩니다.</p>
      </div>

      <fieldset className="options-fieldset" disabled={disabled}>
        <legend className="sr-only">선택할 여행 조건</legend>

        <section className="option-group">
          <h3>출발지</h3>
          <p className="option-value">{options.departure || DEFAULT_ORIGIN}</p>
          {confirmedOrigin?.address && (
            <p className="options-note">{confirmedOrigin.address}</p>
          )}
          <details
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
        </section>

        <section className="option-group">
          <h3>여행 날짜와 기간</h3>
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
          <p className="options-note">
            {formatTripDuration(options.trip_days)} · 당일은 같은 날짜를 선택하세요.
          </p>
          {dateError && (
            <p className="form-error" role="alert">{dateError}</p>
          )}
        </section>

        <section className="option-group">
          <h3>이동 조건</h3>
          <OptionSelect
            id="option-transport"
            label="이동수단"
            value={options.transport}
            choices={TRANSPORT_OPTIONS}
            onChange={(value) => onChange({ transport: value })}
          />
          <OptionSelect
            id="option-mobility"
            label="이동 보조기구"
            value={options.mobility_aid}
            choices={MOBILITY_OPTIONS}
            onChange={(value) => onChange({ mobility_aid: value })}
          />
        </section>

        <fieldset className="option-group">
          <legend>여행 종류</legend>
          <div className="option-check-list">
            {TRAVEL_STYLES.map((style) => (
              <label key={style} className="option-check">
                <input
                  type="checkbox"
                  checked={options.travel_styles.includes(style)}
                  onChange={() => toggleList('travel_styles', style)}
                />
                <span>{style}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="option-group">
          <legend>필요한 접근 조건</legend>
          <div className="option-check-list">
            {ACCESS_REQUIREMENTS.map((item) => (
              <label key={item.value} className="option-check">
                <input
                  type="checkbox"
                  checked={options.requirements.includes(item.value)}
                  onChange={() => toggleList('requirements', item.value)}
                />
                <span>{item.label}</span>
              </label>
            ))}
          </div>
        </fieldset>
      </fieldset>
    </aside>
  )
}

export default TravelOptions