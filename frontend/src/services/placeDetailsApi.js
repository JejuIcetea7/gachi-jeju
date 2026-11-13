const REQUEST_TIMEOUT_MS = 15_000

function detailError(message, code, status = null) {
  const error = new Error(message)
  error.name = 'PlaceDetailsApiError'
  error.code = code
  error.status = status
  return error
}

function isNullableText(value) {
  return value === null || typeof value === 'string'
}

function isDetailResponse(data, placeId) {
  return data?.place_id === placeId
    && typeof data.name === 'string'
    && data.name.trim().length > 0
    && isNullableText(data.category)
    && typeof data.closed_days === 'string'
    && Array.isArray(data.spots)
    && data.spots.every((spot) => (
      isNullableText(spot?.name) && typeof spot?.description === 'string'
    ))
    && Array.isArray(data.accessibility_facts)
    && data.accessibility_facts.every((fact) => (
      isNullableText(fact?.section)
      && typeof fact?.facility_name === 'string'
      && isNullableText(fact?.description)
      && typeof fact?.raw_value === 'string'
    ))
}

export async function requestPlaceDetails(placeId, { signal } = {}) {
  if (
    typeof placeId !== 'string'
    || !placeId.trim()
    || placeId.trim().length > 80
  ) {
    throw detailError('상세 정보를 조회할 장소를 확인해 주세요.', 'invalid_request')
  }

  const id = placeId.trim()
  const endpoint = `/api/places/${encodeURIComponent(id)}/details`
  signal?.throwIfAborted()

  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS)
  const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout

  try {
    const response = await fetch(endpoint, { signal: requestSignal })

    let data
    try {
      data = await response.json()
    } catch (error) {
      if (requestSignal.aborted) throw error
      throw detailError('장소 상세 정보를 읽지 못했습니다.', 'invalid_response')
    }

    if (!response.ok) {
      throw detailError(
        typeof data?.error?.message === 'string' && data.error.message.trim()
          ? data.error.message
          : '장소 상세 정보를 불러오지 못했습니다.',
        typeof data?.error?.code === 'string' ? data.error.code : 'api_error',
        response.status,
      )
    }

    if (!isDetailResponse(data, id)) {
      throw detailError('장소 상세 정보의 형식이 올바르지 않습니다.', 'invalid_response')
    }

    return {
      place_id: data.place_id,
      name: data.name,
      category: data.category,
      closed_days: data.closed_days,
      spots: data.spots.map((spot) => ({
        name: spot.name,
        description: spot.description,
      })),
      accessibility_facts: data.accessibility_facts.map((fact) => ({
        section: fact.section,
        facility_name: fact.facility_name,
        description: fact.description,
        raw_value: fact.raw_value,
      })),
    }
  } catch (error) {
    if (signal?.aborted) throw error

    if (timeout.aborted) {
      throw detailError(
        '상세 정보 조회 시간이 초과됐습니다. 다시 시도해 주세요.',
        'client_timeout',
      )
    }

    if (error?.name === 'PlaceDetailsApiError') throw error

    throw detailError(
      '상세 정보 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      'network_error',
    )
  }
}
