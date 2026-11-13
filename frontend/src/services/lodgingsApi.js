import { isLodgingCoordinate } from './lodgingPlanning.js'

const REQUEST_TIMEOUT_MS = 25_000

function lodgingError(message, code, status = null) {
  const error = new Error(message)
  error.name = 'LodgingsApiError'
  error.code = code
  error.status = status
  return error
}

function validCandidate(place, radiusM) {
  return isLodgingCoordinate(place)
    && typeof place.provider_place_id === 'string' && /^[0-9]{1,40}$/.test(place.provider_place_id)
    && typeof place.name === 'string' && place.name.trim().length > 0 && place.name.length <= 200
    && typeof place.address === 'string' && place.address.length <= 300
    && typeof place.phone === 'string' && place.phone.length <= 80
    && Number.isSafeInteger(place.distance_m) && place.distance_m >= 0 && place.distance_m <= radiusM
    && place.place_url === `https://place.map.kakao.com/${place.provider_place_id}`
}

function validResponse(data, { latitude, longitude, radiusM, maxCount }) {
  return data?.source === 'kakao_local'
    && typeof data.requested_at === 'string'
    && /(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(data.requested_at)
    && Number.isFinite(Date.parse(data.requested_at))
    && data.center?.latitude === latitude && data.center?.longitude === longitude
    && data.radius_m === radiusM
    && Array.isArray(data.places) && data.places.length <= maxCount
    && data.places.every((place) => validCandidate(place, radiusM))
    && new Set(data.places.map((place) => place.provider_place_id)).size === data.places.length
}

export async function requestNearbyLodgings({ latitude, longitude, radiusM = 5000, maxCount = 5, signal } = {}) {
  if (!isLodgingCoordinate({ latitude, longitude })
    || !Number.isSafeInteger(radiusM) || radiusM < 1 || radiusM > 20_000
    || !Number.isSafeInteger(maxCount) || maxCount < 1 || maxCount > 10) {
    throw lodgingError('숙소를 검색할 위치와 범위를 확인해 주세요.', 'invalid_request')
  }
  signal?.throwIfAborted()
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS)
  const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout
  const query = new URLSearchParams({ latitude, longitude, radius_m: radiusM, max_count: maxCount })
  try {
    const response = await fetch(`/api/lodgings/nearby?${query}`, { signal: requestSignal })
    let data
    try {
      data = await response.json()
    } catch (error) {
      if (requestSignal.aborted) throw error
      throw lodgingError('숙소 검색 결과를 읽지 못했습니다.', 'invalid_response')
    }
    requestSignal.throwIfAborted()
    if (!response.ok) {
      throw lodgingError(
        typeof data?.error?.message === 'string' && data.error.message.trim()
          ? data.error.message : '주변 숙소를 조회하지 못했습니다.',
        typeof data?.error?.code === 'string' ? data.error.code : 'api_error', response.status,
      )
    }
    if (!validResponse(data, { latitude, longitude, radiusM, maxCount })) {
      throw lodgingError('숙소 검색 결과의 형식이 올바르지 않습니다.', 'invalid_response')
    }
    return {
      source: data.source, requested_at: data.requested_at,
      center: { latitude, longitude }, radius_m: data.radius_m,
      places: data.places.map((place) => ({
        provider_place_id: place.provider_place_id, name: place.name, address: place.address,
        phone: place.phone, latitude: place.latitude, longitude: place.longitude,
        distance_m: place.distance_m, place_url: place.place_url,
      })),
    }
  } catch (error) {
    if (signal?.aborted) throw signal.reason
    if (timeout.aborted) throw lodgingError('숙소 검색 시간이 초과됐습니다. 다시 시도해 주세요.', 'client_timeout')
    if (error?.name === 'LodgingsApiError') throw error
    throw lodgingError('숙소 검색 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.', 'network_error')
  }
}
