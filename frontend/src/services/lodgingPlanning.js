export function isLodgingCoordinate(value) {
  return Number.isFinite(value?.latitude) && value.latitude >= 33.1 && value.latitude <= 33.6
    && Number.isFinite(value?.longitude) && value.longitude >= 126.08 && value.longitude <= 127
}

function text(value, maximum, required = false) {
  return typeof value === 'string' && value.length <= maximum && (!required || value.trim().length > 0)
}

export function isLodgingStay(value) {
  if (!isLodgingCoordinate(value) || !Number.isSafeInteger(value.night) || value.night < 1) return false
  const id = value.provider_place_id ?? null
  const url = value.place_url ?? null
  return text(value.name, 200, true)
    && (value.address === undefined || text(value.address, 300))
    && (id === null || (typeof id === 'string' && /^[0-9]{1,40}$/.test(id)))
    && (url === null || (id !== null && url === `https://place.map.kakao.com/${id}`))
    && Object.keys(value).every((key) => [
      'night', 'name', 'address', 'latitude', 'longitude', 'provider_place_id', 'place_url',
    ].includes(key))
}

export function isLodgingOptions(options) {
  if (options === null || typeof options !== 'object' || Array.isArray(options)) return false
  const status = options?.lodging_status ?? null
  const stays = options?.lodging_stays === undefined ? [] : options.lodging_stays
  if (![null, 'undecided', 'provided', 'not_needed'].includes(status)
    || !Array.isArray(stays) || !stays.every(isLodgingStay)) return false
  const nights = stays.map((stay) => stay.night)
  return new Set(nights).size === nights.length
    && (options.trip_days == null || nights.every((night) => night < options.trip_days))
    && (stays.length === 0 || ['undecided', 'provided'].includes(status))
}

export function normalizeLodgingOptions(options) {
  return {
    ...options,
    lodging_status: options.lodging_status ?? null,
    lodging_stays: (options.lodging_stays ?? []).map((stay) => ({ ...stay })),
  }
}

export function resizeLodgingOptions(options) {
  const next = normalizeLodgingOptions(options)
  next.lodging_stays = next.lodging_stays.filter((stay) => (
    Number.isSafeInteger(next.trip_days) && stay.night < next.trip_days
  ))
  if (next.lodging_status === 'provided' && next.lodging_stays.length < (next.trip_days ?? 1) - 1) {
    next.lodging_status = 'undecided'
  }
  return next
}

export function getLodgingStay(options, night) {
  if (options?.lodging_status === 'not_needed' || !Array.isArray(options?.lodging_stays)) return null
  const matches = options.lodging_stays.filter((stay) => (
    isLodgingStay(stay) && stay.night === night && night < options.trip_days
  ))
  return matches.length === 1 ? matches[0] : null
}

export function getLodgingSearchCenter(day) {
  const lastVisit = (day?.items ?? []).filter((item) => item.item_type === 'visit').at(-1)
  if (lastVisit?.verified !== true || !isLodgingCoordinate(lastVisit)) return null
  return { latitude: lastVisit.latitude, longitude: lastVisit.longitude, name: lastVisit.title }
}

export function chooseLodging(options, night, candidate) {
  if (!Number.isSafeInteger(options?.trip_days) || options.trip_days < 2
    || !Number.isSafeInteger(night) || night < 1 || night >= options.trip_days) {
    throw new Error('숙박일과 여행 기간을 확인해 주세요.')
  }
  const selected = {
    night, name: candidate?.name, address: candidate?.address ?? '',
    latitude: candidate?.latitude, longitude: candidate?.longitude,
    provider_place_id: candidate?.provider_place_id ?? null,
    place_url: candidate?.place_url ?? null,
  }
  if (!isLodgingStay(selected)) throw new Error('선택할 숙소의 위치를 확인해 주세요.')
  const next = resizeLodgingOptions(options)
  next.lodging_stays = [...next.lodging_stays.filter((stay) => stay.night !== night), selected]
    .sort((first, second) => first.night - second.night)
  next.lodging_status = next.lodging_stays.length === options.trip_days - 1 ? 'provided' : 'undecided'
  if (!isLodgingOptions(next)) throw new Error('숙소 선택 정보를 확인해 주세요.')
  return next
}

export function removeLodging(options, night) {
  const next = normalizeLodgingOptions(options)
  next.lodging_stays = next.lodging_stays.filter((stay) => stay.night !== night)
  next.lodging_status = 'undecided'
  return next
}

export function getLodgingSelectionMessage(stay) {
  return `${stay.night}일차 관광 후에는 숙소 '${stay.name}'에서 묵겠습니다. `
    + '선택한 숙소 위치를 사용하고 기존 관광지와 여행 조건은 가능한 한 유지해 주세요. '
    + '해당 숙박일의 숙소 도착 이동과 다음 날 숙소 출발 이동을 조회해 전체 일정의 시간을 다시 계산해 주세요.'
}

function mapCoordinate(value) {
  return Number.isFinite(value?.latitude) && Math.abs(value.latitude) <= 90
    && Number.isFinite(value?.longitude) && Math.abs(value.longitude) <= 180
}

export function getLodgingMapAnchors(day, options) {
  const result = []
  const number = day?.day ?? 1
  const first = number === 1 || options?.lodging_status === 'not_needed'
  const previousStay = getLodgingStay(options, number - 1)
  const nextStay = getLodgingStay(options, number)
  const origin = { latitude: options?.departure_latitude, longitude: options?.departure_longitude }
  if (first && mapCoordinate(origin)) {
    result.push({ id: 'origin', role: 'origin', position: 'start', label: '출발',
      title: options?.departure || '여행 출발지', ...origin })
  } else if (previousStay) {
    result.push({ id: 'lodging-start', role: 'lodging', position: 'start', label: '숙소 출발',
      title: previousStay.name, latitude: previousStay.latitude, longitude: previousStay.longitude })
  }
  if (day && nextStay) {
    result.push({ id: 'lodging-end', role: 'lodging', position: 'end', label: '숙소 도착',
      title: nextStay.name, latitude: nextStay.latitude, longitude: nextStay.longitude })
  }
  if (day && options?.return_to_origin === true && mapCoordinate(origin)
    && (number === options.trip_days || options.lodging_status === 'not_needed')) {
    result.push({ id: 'origin-return', role: 'origin', position: 'end', label: '귀환',
      title: options.departure || '여행 출발지', ...origin })
  }
  return result
}
