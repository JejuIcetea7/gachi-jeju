import { useState } from 'react'
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
} from './services/travelOptions.js'

const DURATION_OPTIONS = [1, 2, 3].map((days) => ({
  value: String(days),
  label: formatTripDuration(days),
}))

const RETURN_OPTIONS = [
  { value: 'return', label: '마지막 날 출발지로 돌아오기' },
  { value: 'finish', label: '마지막 여행지에서 마치기' },
]

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
  const dateKey = JSON.stringify([options.start_date, options.trip_days])
  const originKey = JSON.stringify([
    options.departure,
    options.departure_latitude,
    options.departure_longitude,
  ])

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
      getTripEndDate(next.start_date, next.trip_days)
      setDateIssue(null)
      onChange(patch)
    } catch (error) {
      recordDateError(error)
    }
  }

  function handleStartDate(value) {
    if (value && !isTravelDate(value)) {
      recordDateError(new Error('여행 날짜를 다시 확인해 주세요.'))
      return
    }
    updateDates({ start_date: value || null })
  }

  function handleEndDate(value) {
    if (disabled) return
    try {
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
    <aside className="workspace-options" aria-labelledby="workspace-options-title">
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
                  setOriginOpen(false)
                }}
              />
            )}
          </details>
        </section>

        <section className="option-group">
          <h3>여행 날짜와 기간</h3>
          <OptionSelect
            id="option-duration"
            label="여행 기간"
            value={options.trip_days == null ? '' : String(options.trip_days)}
            choices={DURATION_OPTIONS}
            onChange={(value) => updateDates({
              trip_days: value === null ? null : Number(value),
            })}
          />
          <div className="options-date-grid">
            <label className="option-select" htmlFor="option-start-date">
              <span>출발일</span>
              <input
                id="option-start-date"
                type="date"
                value={options.start_date ?? ''}
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
                min={options.start_date || undefined}
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

        <section className="option-group">
          <OptionSelect
            id="option-return"
            label="여행 마무리"
            value={options.return_to_origin === null
              ? ''
              : options.return_to_origin ? 'return' : 'finish'}
            choices={RETURN_OPTIONS}
            onChange={(value) => onChange({
              return_to_origin: value === null ? null : value === 'return',
            })}
          />
        </section>
      </fieldset>
    </aside>
  )
}

export default TravelOptions